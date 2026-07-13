"""FX connection manager — offline. A fake transport mirrors a live supernova
FX server (its address space, its establish/close state machine and its exact
refusal diagnostics), so the whole manager — describe, wire, roll back, status,
undo, teaching errors — is proven without asyncua or a server. The refusal
strings here were captured verbatim from a live o6 FX server. The live
end-to-end against real docker servers is interop/fx_manager_e2e.py."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestServer

from hypernova import fx
from hypernova.registry.service import create_app
from hypernova.registry.store import Store

GOOD = 0
BAD_INVALID_ARGUMENT = 0x80AB0000

# The config the live test cells ship (control/env -> control/setpoints).
_CONFIG = {
    "component": "ProcessCell",
    "nodeId": "ns=2;s=ProcessCell",
    "publisherId": 91,
    "entities": {
        "control": {
            "outputs": {"env": [("temperature", "FX1.temperature"), ("count", "FX1.counter")]},
            "inputs": {"setpoints": [("setpoint", "FX1.setpoint"), ("command", "FX1.command")]},
            "writerGroups": {"env": (200, 1)},  # (writerGroupId, dataSetWriterId)
        }
    },
    "sourceTypes": {"FX1.temperature": "DOUBLE", "FX1.counter": "INT32",
                    "FX1.setpoint": "DOUBLE", "FX1.command": "INT32"},
}


class FakeFxServer(fx._Transport):
    """An in-memory FX automation component: enough of the real one to exercise
    every manager path, including the precise refusal strings."""

    # Members the real server's strict parser accepts (anything else is refused).
    _KNOWN_MEMBERS = {"functionalEntity", "role", "dataset", "address", "connectionName",
                      "publishingIntervalMs", "ttl", "peer"}

    def __init__(self, config=None):
        self.config = json.loads(json.dumps(config or _CONFIG))
        self.connections = {}  # name -> dict(status, address, dataset, role, entity, ds, live)
        self.name_entity = {}  # every name ever used -> its owning entity (persists past close)
        self.reject_establish = None  # set to a string to force refusal (fault injection)
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def read_source_type(self, source):
        return self.config["sourceTypes"].get(source)

    # -- the two connection services -------------------------------------- #

    async def call(self, method, argument):
        if method == "EstablishConnections":
            return self._establish(argument)
        if method == "CloseConnections":
            return self._close(argument)
        return BAD_INVALID_ARGUMENT, ["", json.dumps(
            {"status": "Error", "diagnostic": f"no such method {method!r}"})]

    def _refuse(self, diagnostic, outputs=1):
        detail = json.dumps({"status": "Error", "diagnostic": diagnostic})
        return BAD_INVALID_ARGUMENT, (["", detail] if outputs == 2 else [detail])

    def _auto_name(self, entity_name):
        """Auto-naming reuses a closed slot (verified: after cep-1 is closed the
        live server hands out cep-1 again; at the ceiling it reuses any closed
        endpoint rather than minting a new name)."""
        distinct = {n for n, e in self.name_entity.items() if e == entity_name}
        if len(distinct) >= 64:  # at the ceiling, reuse a closed endpoint
            closed = sorted(n for n in distinct if not self.connections[n]["live"])
            if closed:
                return closed[0]
        i = 1
        while self.connections.get(f"cep-{i}", {"live": False})["live"]:
            i += 1
        return f"cep-{i}"

    def _establish(self, argument):
        if self.reject_establish is not None:
            return self._refuse(self.reject_establish, outputs=2)
        try:
            req = json.loads(argument)
        except ValueError as error:
            return self._refuse(f"JSON error: {error}", outputs=2)
        for member in req:
            if member not in self._KNOWN_MEMBERS:
                return self._refuse(
                    f"unknown member {member!r} in the connection configuration", 2)
        entity_name = req.get("functionalEntity")
        role = req.get("role")
        ds_name = req.get("dataset")
        entities = self.config["entities"]
        if entity_name not in entities:
            return self._refuse(f"unknown functional entity {entity_name!r}", outputs=2)
        if role not in ("publisher", "subscriber"):
            return self._refuse("role must be 'publisher' or 'subscriber'", 2)
        # optional members that apply to only one role
        if role == "publisher" and "peer" in req:
            return self._refuse("'peer' applies to subscriber connections only", 2)
        if role == "subscriber" and ("ttl" in req or "publishingIntervalMs" in req):
            return self._refuse("'publishingIntervalMs'/'ttl' apply to publisher connections only", 2)
        if role == "subscriber" and not isinstance(req.get("peer"), dict):
            return self._refuse("subscriber connections need a 'peer' object "
                                "(the publishing side's wire coordinates)", 2)
        entity = entities[entity_name]
        bucket = "outputs" if role == "publisher" else "inputs"
        kind = "output" if role == "publisher" else "input"
        if ds_name not in entity[bucket]:
            return self._refuse(
                f"functional entity {entity_name!r} has no {kind} dataset {ds_name!r}", 2)
        name = req.get("connectionName")
        if name:
            live = self.connections.get(name)
            if live and live["live"]:
                return self._refuse(f"connection {name!r} is already established", 2)
            owner = self.name_entity.get(name)
            if owner is not None and owner != entity_name:  # endpoints are per entity
                return self._refuse(
                    f"connection {name!r} belongs to functional entity {owner!r}", 2)
        # a dataset can be connected once at a time, per direction
        for other, endpoint in self.connections.items():
            if (endpoint["live"] and endpoint["entity"] == entity_name
                    and endpoint["ds"] == ds_name and endpoint["role"] == role):
                return self._refuse(
                    f"dataset {ds_name!r} is already connected as {other!r} — "
                    "close that connection first", 2)
        # at most 64 distinct connection names per entity (closed ones count;
        # reusing an existing name is allowed at the ceiling)
        distinct = {n for n, e in self.name_entity.items() if e == entity_name}
        if name and name not in distinct and len(distinct) >= 64:
            return self._refuse(
                f"functional entity {entity_name!r} reached its connection endpoint limit "
                "(64); close and reuse an existing connection name", 2)
        if not name:
            name = self._auto_name(entity_name)
        status = 3 if role == "publisher" else 2  # publisher Operational, subscriber PreOperational
        self.connections[name] = {
            "status": status, "address": req["address"], "role": role,
            "entity": entity_name, "ds": ds_name, "live": True,
            "dataset": f"{role}:{entity_name}.{ds_name}"}
        self.name_entity[name] = entity_name
        detail = {"address": req["address"], "status": fx.STATUS_NAMES[status]}
        if role == "publisher":
            wg, dsw = entity["writerGroups"][ds_name]
            detail["coordinates"] = {"publisherId": self.config["publisherId"],
                                     "publisherIdType": "UInt16",
                                     "writerGroupId": wg, "dataSetWriterId": dsw}
        return GOOD, [name, json.dumps(detail)]

    def _close(self, argument):
        try:
            parsed = json.loads(argument)
            name = parsed["connectionId"] if isinstance(parsed, dict) else parsed
        except (ValueError, TypeError, KeyError):
            name = argument
        endpoint = self.connections.get(name)
        if endpoint is None:
            return self._refuse(f"unknown connection {name!r}")
        if not endpoint["live"]:
            return self._refuse(f"connection {name!r} is not established")
        endpoint["live"] = False
        endpoint["status"] = 0
        return GOOD, [json.dumps({"status": "Initial"})]

    # -- self-description -------------------------------------------------- #

    async def snapshot(self):
        entities = []
        for name, entity in self.config["entities"].items():
            def datasets(bucket):
                return [{"name": ds, "fields": [{"name": f, "source": s} for f, s in fields]}
                        for ds, fields in entity[bucket].items()]
            endpoints = [{"name": n, "status": e["status"], "address": e["address"],
                          "dataset": e["dataset"]}
                         for n, e in self.connections.items() if e["entity"] == name]
            entities.append({"name": name, "outputs": datasets("outputs"),
                             "inputs": datasets("inputs"), "endpoints": endpoints})
        return {"component": self.config["component"], "nodeId": self.config["nodeId"],
                "entities": entities}

    # -- test helper: first data arrives, subscribers go Operational ------- #

    def deliver(self):
        for endpoint in self.connections.values():
            if endpoint["live"] and endpoint["role"] == "subscriber" and endpoint["status"] == 2:
                endpoint["status"] = 3


def server(config=None):
    return fx.connect("opc.tcp://fake", transport=FakeFxServer(config))


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def test_coordinates_roundtrip_to_peer():
    detail = {"coordinates": {"publisherId": 91, "publisherIdType": "UInt16",
                              "writerGroupId": 200, "dataSetWriterId": 1}}
    coords = fx.Coordinates.from_detail(detail)
    assert coords.as_peer() == {"publisherId": 91, "publisherIdType": "UInt16",
                                "writerGroupId": 200, "dataSetWriterId": 1}
    assert fx.Coordinates.from_detail({"status": "Operational"}) is None


def test_diagnostic_extracted_from_detail():
    outputs = ["", json.dumps({"status": "Error", "diagnostic": "unknown functional entity 'ctrl'"})]
    assert fx._diagnostic(outputs) == "unknown functional entity 'ctrl'"
    assert fx._diagnostic([""]) == "no diagnostic returned"


def test_connection_name_sanitized():
    assert fx._connection_name("a\x00b\tc", None, None) == "abc"
    assert len(fx._connection_name("x" * 200, None, None)) == 64


def test_status_names_cover_the_enum():
    assert fx.STATUS_NAMES == {0: "Initial", 1: "Ready", 2: "PreOperational",
                               3: "Operational", 4: "Error"}


# --------------------------------------------------------------------------- #
# describe
# --------------------------------------------------------------------------- #

async def test_describe_lists_entities_datasets_fields():
    async with server() as s:
        component = await s.describe()
        assert component.name == "ProcessCell"
        assert component.node_id == "ns=2;s=ProcessCell"
        control = component.entity("control")
        assert [d.name for d in control.outputs] == ["env"]
        assert control.dataset("env", "output").field_names == ["temperature", "count"]
        assert control.dataset("setpoints", "input").field_names == ["setpoint", "command"]
        assert "temperature" in str(component) and "setpoints" in str(component)


async def test_describe_reports_not_capable_server():
    class Bare(FakeFxServer):
        async def snapshot(self):
            return {"component": "?", "nodeId": "?", "entities": []}
    async with fx.connect("opc.tcp://bare", transport=Bare()) as s:
        component = await s.describe()
        assert component.entities == []


# --------------------------------------------------------------------------- #
# establish / refusals that teach
# --------------------------------------------------------------------------- #

async def test_establish_publisher_returns_coordinates():
    async with server() as s:
        conn_id, detail = await s.establish(entity="control", role="publisher",
                                            dataset="env", address="opc.udp://239.0.0.7:14840",
                                            connection_name="link")
        assert conn_id == "link"
        assert fx.Coordinates.from_detail(detail).writer_group_id == 200
        endpoint = await s.endpoint("control", "link")
        assert endpoint.is_operational and endpoint.status_name == "Operational"


async def test_establish_unknown_dataset_raises_teaching_refusal():
    async with server() as s:
        with pytest.raises(fx.FxRefused) as excinfo:
            await s.establish(entity="control", role="publisher", dataset="nope",
                              address="opc.udp://239.0.0.7:14840")
        assert "no output dataset 'nope'" in str(excinfo.value)
        assert excinfo.value.diagnostic  # the server's own words are preserved


async def test_close_unknown_connection_raises():
    async with server() as s:
        with pytest.raises(fx.FxRefused) as excinfo:
            await s.close_connection("ghost")
        assert "unknown connection 'ghost'" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# link: the flagship, with pre-validation and rollback
# --------------------------------------------------------------------------- #

async def test_link_wires_two_servers_and_reports_status():
    async with server() as a, server() as b:
        link = await fx.link(a.publisher("control", "env"),
                             b.subscriber("control", "setpoints"),
                             address="opc.udp://239.0.0.7:14840", name="demo")
        assert link.name == "demo"
        assert link.coordinates.publisher_id == 91
        state = await link.status()
        assert state["publisher"].is_operational          # publisher active at once
        assert state["subscriber"].status_name == "PreOperational"  # awaiting first data
        assert not await link.is_operational()
        b._t.deliver()                                    # first datagram arrives
        assert (await link.status())["subscriber"].is_operational
        assert await link.is_operational()


async def test_link_default_name_is_readable():
    async with server() as a, server() as b:
        link = await fx.link(a.publisher("control", "env"),
                             b.subscriber("control", "setpoints"),
                             address="opc.udp://239.0.0.7:14840")
        assert link.name == "env-to-setpoints"


async def test_link_rolls_back_publisher_when_subscriber_fails():
    fake_b = FakeFxServer()
    fake_b.reject_establish = "simulated subscriber failure"
    a = server()
    b = fx.connect("opc.tcp://b", transport=fake_b)
    async with a, b:
        with pytest.raises(fx.FxError) as excinfo:
            await fx.link(a.publisher("control", "env"),
                          b.subscriber("control", "setpoints"),
                          address="opc.udp://239.0.0.7:14840", name="demo")
        message = str(excinfo.value)
        assert "rolled the publisher side back" in message
        assert "simulated subscriber failure" in message
        # the publisher endpoint must not be left running
        endpoint = await a.endpoint("control", "demo")
        assert endpoint is None or not endpoint.is_operational


async def test_link_prevalidates_before_touching_any_server():
    async with server() as a, server() as b:
        with pytest.raises(fx.FxError) as excinfo:
            await fx.link(a.publisher("control", "nope"),
                          b.subscriber("control", "setpoints"),
                          address="opc.udp://239.0.0.7:14840")
        assert "no output dataset 'nope'" in str(excinfo.value)
        assert "available output datasets: env" in str(excinfo.value)
        # nothing was established on either side
        assert await a.endpoints() == [] and await b.endpoints() == []


async def test_link_rejects_swapped_roles():
    async with server() as a, server() as b:
        with pytest.raises(fx.FxError) as excinfo:
            await fx.link(a.subscriber("control", "setpoints"),
                          b.publisher("control", "env"),
                          address="opc.udp://239.0.0.7:14840")
        assert "publisher" in str(excinfo.value) and "subscriber" in str(excinfo.value)


async def test_link_unknown_entity_lists_offered_entities():
    async with server() as a, server() as b:
        with pytest.raises(fx.FxError) as excinfo:
            await fx.link(a.publisher("ctrl", "env"),
                          b.subscriber("control", "setpoints"),
                          address="opc.udp://239.0.0.7:14840")
        assert "has no functional entity 'ctrl'" in str(excinfo.value)
        assert "it offers: control" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# unlink
# --------------------------------------------------------------------------- #

async def test_unlink_closes_both_sides_and_is_idempotent():
    async with server() as a, server() as b:
        link = await fx.link(a.publisher("control", "env"),
                             b.subscriber("control", "setpoints"),
                             address="opc.udp://239.0.0.7:14840", name="demo")
        await fx.unlink(link)
        state = await link.status()
        assert state["publisher"].status_name == "Initial"
        assert state["subscriber"].status_name == "Initial"
        await fx.unlink(link)  # closing an already-closed link is not an error


async def test_unlink_reports_when_a_side_cannot_close():
    class StuckClose(FakeFxServer):
        def _close(self, argument):
            return self._refuse("device busy, retry later")
    a = server()
    b = fx.connect("opc.tcp://b", transport=StuckClose())
    async with a, b:
        link = await fx.link(a.publisher("control", "env"),
                             b.subscriber("control", "setpoints"),
                             address="opc.udp://239.0.0.7:14840", name="demo")
        with pytest.raises(fx.FxError) as excinfo:
            await fx.unlink(link)
        assert "unlink incomplete" in str(excinfo.value)
        assert "device busy" in str(excinfo.value)
        # the other side (publisher on A) was still closed
        assert (await a.endpoint("control", "demo")).status_name == "Initial"


async def test_dataset_connected_once_per_direction():
    async with server() as a, server() as b:
        await fx.link(a.publisher("control", "env"),
                      b.subscriber("control", "setpoints"),
                      address="opc.udp://239.0.0.7:14840", name="one")
        with pytest.raises(fx.FxError) as excinfo:
            await fx.link(a.publisher("control", "env"),
                          b.subscriber("control", "setpoints"),
                          address="opc.udp://239.0.0.7:14841", name="two")
        assert "already connected" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# the optional registry bridge (names over coordinates for FX-created streams)
# --------------------------------------------------------------------------- #

async def test_registry_payload_names_the_created_stream(tmp_path):
    store = Store(tmp_path / "reg.json")
    registry = TestServer(create_app(store, listen=False))
    await registry.start_server()
    try:
        import urllib.request
        async with server() as a, server() as b:
            link = await fx.link(a.publisher("control", "env"),
                                 b.subscriber("control", "setpoints"),
                                 address="opc.udp://239.0.0.7:14840", name="cell7/env")
            payload = await fx.registry_payload(link)
            assert payload["publisherId"] == 91
            assert payload["writerGroupId"] == 200
            assert payload["publisherIdType"] == "UINT16"
            assert payload["fields"] == [{"name": "temperature", "type": "DOUBLE"},
                                         {"name": "count", "type": "INT32"}]
            url = f"http://{registry.host}:{registry.port}/api/publications/cell7/env"
            body = json.dumps(payload).encode()
            request = urllib.request.Request(url, data=body, method="PUT",
                                             headers={"Content-Type": "application/json"})
            # the TestServer runs on this event loop, so the blocking PUT must
            # go to a thread or it deadlocks the loop it is trying to reach
            await asyncio.to_thread(lambda: urllib.request.urlopen(request).read())
        # it is now resolvable by name, like any other publication
        assert store.get("cell7/env").writer_group_id == 200
        assert store.get("cell7/env").fields[0].name == "temperature"
    finally:
        await registry.close()


async def test_registry_payload_refuses_unresolved_field_type():
    class NoTypes(FakeFxServer):
        async def read_source_type(self, source):
            return None  # a server whose source types cannot be read
    a = fx.connect("opc.tcp://a", transport=NoTypes())
    async with a, server() as b:
        link = await fx.link(a.publisher("control", "env"),
                             b.subscriber("control", "setpoints"),
                             address="opc.udp://239.0.0.7:14840", name="x")
        with pytest.raises(fx.FxError) as excinfo:
            await fx.registry_payload(link)
        # it names the offending fields and points at the manual escape hatch,
        # rather than silently defaulting a type that would misdecode UADP
        assert "temperature" in str(excinfo.value) and "count" in str(excinfo.value)
        assert "hypernova register" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Fake fidelity to the real establish state machine (strings captured live)
# --------------------------------------------------------------------------- #

async def test_reestablish_same_name_while_live_is_refused():
    async with server() as s:
        await s.establish(entity="control", role="publisher", dataset="env",
                          address="opc.udp://239.0.0.7:14840", connection_name="L")
        with pytest.raises(fx.FxRefused) as excinfo:
            await s.establish(entity="control", role="publisher", dataset="env",
                              address="opc.udp://239.0.0.7:14840", connection_name="L")
        assert "connection 'L' is already established" in str(excinfo.value)


async def test_link_default_name_collision_second_link_refused():
    async with server() as a, server() as b:
        first = await fx.link(a.publisher("control", "env"),
                              b.subscriber("control", "setpoints"),
                              address="opc.udp://239.0.0.7:14840")
        # default names are deterministic (env-to-setpoints), so a second
        # identical wiring collides on the publisher side and is refused; the
        # first link is untouched (nothing half-established)
        with pytest.raises(fx.FxError) as excinfo:
            await fx.link(a.publisher("control", "env"),
                          b.subscriber("control", "setpoints"),
                          address="opc.udp://239.0.0.7:14841")
        assert "already established" in str(excinfo.value)
        assert (await first.status())["publisher"].is_operational


async def test_establish_strict_vocabulary():
    async with server() as s:
        # unknown member (goes through the raw transport — establish() can't emit one)
        status, outputs = await s._t.call("EstablishConnections", json.dumps({
            "functionalEntity": "control", "role": "publisher", "dataset": "env",
            "address": "opc.udp://239.0.0.7:14840", "bogus": 1}))
        assert "unknown member 'bogus'" in fx._diagnostic(outputs)

        with pytest.raises(fx.FxRefused) as excinfo:  # peer on a publisher
            await s.establish(entity="control", role="publisher", dataset="env",
                              address="opc.udp://239.0.0.7:14840",
                              peer={"publisherId": 1, "writerGroupId": 1, "dataSetWriterId": 1})
        assert "'peer' applies to subscriber connections only" in str(excinfo.value)

        with pytest.raises(fx.FxRefused) as excinfo:  # ttl on a subscriber
            await s.establish(entity="control", role="subscriber", dataset="setpoints",
                              address="opc.udp://0.0.0.0:14840", ttl=5,
                              peer={"publisherId": 91, "writerGroupId": 200, "dataSetWriterId": 1})
        assert "apply to publisher connections only" in str(excinfo.value)

        with pytest.raises(fx.FxRefused) as excinfo:  # subscriber without peer
            await s.establish(entity="control", role="subscriber", dataset="setpoints",
                              address="opc.udp://0.0.0.0:14840")
        assert "need a 'peer' object" in str(excinfo.value)


async def test_auto_naming_reuses_closed_slot():
    async with server() as s:
        first, _ = await s.establish(entity="control", role="publisher", dataset="env",
                                     address="opc.udp://239.0.0.7:14840")
        assert first == "cep-1"
        await s.close_connection("cep-1")
        second, _ = await s.establish(entity="control", role="publisher", dataset="env",
                                      address="opc.udp://239.0.0.7:14840")
        assert second == "cep-1"  # the closed slot is reused, not cep-2


async def test_endpoint_ceiling_counts_closed_names_and_allows_reuse():
    async with server() as s:
        for i in range(64):  # 64 distinct names, each closed to free the dataset
            name = f"c{i}"
            await s.establish(entity="control", role="publisher", dataset="env",
                              address="opc.udp://239.0.0.7:14840", connection_name=name)
            await s.close_connection(name)
        with pytest.raises(fx.FxRefused) as excinfo:  # a 65th new name is refused
            await s.establish(entity="control", role="publisher", dataset="env",
                              address="opc.udp://239.0.0.7:14840", connection_name="c64")
        assert "connection endpoint limit (64)" in str(excinfo.value)
        # but reusing one of the 64 existing (closed) names is allowed
        reused, _ = await s.establish(entity="control", role="publisher", dataset="env",
                                      address="opc.udp://239.0.0.7:14840", connection_name="c0")
        assert reused == "c0"
        await s.close_connection("c0")
        # auto-naming at the ceiling reuses a closed endpoint, not a 65th name
        auto, _ = await s.establish(entity="control", role="publisher", dataset="env",
                                    address="opc.udp://239.0.0.7:14840")
        assert auto in {f"c{i}" for i in range(64)}  # an existing name, reused


async def test_cross_entity_name_reuse_is_refused():
    config = {
        "component": "Cell", "nodeId": "ns=2;s=Cell", "publisherId": 5,
        "entities": {
            "a": {"outputs": {"o": [("x", "I.x")]}, "inputs": {}, "writerGroups": {"o": (10, 1)}},
            "b": {"outputs": {"o2": [("y", "I.y")]}, "inputs": {}, "writerGroups": {"o2": (11, 1)}},
        },
        "sourceTypes": {"I.x": "DOUBLE", "I.y": "DOUBLE"},
    }
    async with server(config) as s:
        await s.establish(entity="a", role="publisher", dataset="o",
                          address="opc.udp://239.0.0.7:14840", connection_name="X")
        await s.close_connection("X")  # closed, but the name still belongs to 'a'
        with pytest.raises(fx.FxRefused) as excinfo:
            await s.establish(entity="b", role="publisher", dataset="o2",
                              address="opc.udp://239.0.0.7:14840", connection_name="X")
        assert "belongs to functional entity 'a'" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Safety under real-network failures (not just FxError)
# --------------------------------------------------------------------------- #

async def test_coordinates_from_detail_tolerates_missing_key():
    # a coordinates object missing a key must yield None, not raise KeyError,
    # so link()'s rollback path (not a crash) handles a malformed publisher reply
    assert fx.Coordinates.from_detail(
        {"coordinates": {"publisherId": 1, "writerGroupId": 2}}) is None


async def test_link_rolls_back_on_transport_error_not_just_refusal():
    class DropSub(FakeFxServer):
        async def call(self, method, argument):
            if method == "EstablishConnections":
                raise ConnectionResetError("connection reset by peer")
            return await FakeFxServer.call(self, method, argument)
    a = server()
    b = fx.connect("opc.tcp://b", transport=DropSub())
    async with a, b:
        with pytest.raises(fx.FxError) as excinfo:
            await fx.link(a.publisher("control", "env"),
                          b.subscriber("control", "setpoints"),
                          address="opc.udp://239.0.0.7:14840", name="demo")
        assert "rolled the publisher side back" in str(excinfo.value)
        assert "connection reset by peer" in str(excinfo.value)
        # the publisher on A must not be left running by a transport-level failure
        endpoint = await a.endpoint("control", "demo")
        assert endpoint is None or endpoint.status_name == "Initial"


async def test_link_rollback_survives_cancellation():
    hang = asyncio.Event()

    class HangSub(FakeFxServer):
        async def call(self, method, argument):
            if method == "EstablishConnections":
                await hang.wait()  # never set: hang until the link task is cancelled
            return await FakeFxServer.call(self, method, argument)
    a = server()
    b = fx.connect("opc.tcp://b", transport=HangSub())
    async with a, b:
        task = asyncio.create_task(fx.link(
            a.publisher("control", "env"), b.subscriber("control", "setpoints"),
            address="opc.udp://239.0.0.7:14840", name="demo"))
        await asyncio.sleep(0.05)  # let the publisher side establish, then hang on the subscriber
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # cancellation must not orphan the publisher — it was rolled back
        endpoint = await a.endpoint("control", "demo")
        assert endpoint is None or endpoint.status_name == "Initial"


async def test_unlink_attempts_both_sides_on_transport_error():
    class BrokenClose(FakeFxServer):
        async def call(self, method, argument):
            if method == "CloseConnections":
                raise ConnectionResetError("connection reset")
            return await FakeFxServer.call(self, method, argument)
    a = server()
    b = fx.connect("opc.tcp://b", transport=BrokenClose())
    async with a, b:
        link = await fx.link(a.publisher("control", "env"),
                             b.subscriber("control", "setpoints"),
                             address="opc.udp://239.0.0.7:14840", name="demo")
        with pytest.raises(fx.FxError) as excinfo:
            await fx.unlink(link)
        assert "unlink incomplete" in str(excinfo.value)
        assert "connection reset" in str(excinfo.value)
        # the publisher side on A was still closed despite B's transport failure
        assert (await a.endpoint("control", "demo")).status_name == "Initial"


# --------------------------------------------------------------------------- #
# FX provenance in the registry + browser (the registry "profits" from FX)
# --------------------------------------------------------------------------- #

async def test_registry_payload_carries_fx_provenance():
    async with server() as a, server() as b:
        link = await fx.link(a.publisher("control", "env"),
                             b.subscriber("control", "setpoints"),
                             address="opc.udp://239.0.0.7:14840", name="cell7/env")
        payload = await fx.registry_payload(link)
        assert payload["fx"] == {
            "connection": "cell7/env",
            "publisher": {"server": a.url, "entity": "control", "dataset": "env"},
            "subscriber": {"server": b.url, "entity": "control", "dataset": "setpoints"},
        }


def test_store_roundtrips_and_validates_fx_provenance(tmp_path):
    from hypernova.registry.store import FieldSpec, Publication, StoreError
    provenance = {
        "connection": "link",
        "publisher": {"server": "opc.tcp://a:4841", "entity": "control", "dataset": "env"},
        "subscriber": {"server": "opc.tcp://b:4841", "entity": "control", "dataset": "setpoints"}}
    store = Store(tmp_path / "reg.json")
    store.register(Publication(name="cell/x", address="opc.udp://239.0.0.7:14840",
                               publisher_id=91, writer_group_id=200, dataset_writer_id=1,
                               fields=[FieldSpec("temperature", "DOUBLE")], fx=provenance))
    assert Store(tmp_path / "reg.json").get("cell/x").fx == provenance  # persists + reloads intact
    with pytest.raises(StoreError) as excinfo:  # a hand-edited/garbled provenance is refused
        store.register(Publication(name="cell/y", address="opc.udp://239.0.0.7:14841",
                                   publisher_id=92, writer_group_id=201, dataset_writer_id=1,
                                   fields=[FieldSpec("t", "DOUBLE")],
                                   fx={"connection": "l", "publisher": {"server": "x"}}), replace=True)
    assert "fx.publisher" in str(excinfo.value)


async def test_api_exposes_fx_in_list_and_detail(tmp_path):
    store = Store(tmp_path / "reg.json")
    registry = TestServer(create_app(store, listen=False))
    await registry.start_server()
    try:
        import urllib.request
        base = f"http://{registry.host}:{registry.port}"
        body = json.dumps({
            "address": "opc.udp://239.0.0.7:14840", "publisherId": 91,
            "writerGroupId": 200, "dataSetWriterId": 1,
            "fields": [{"name": "temperature", "type": "DOUBLE"}],
            "fx": {"connection": "cell7/env",
                   "publisher": {"server": "opc.tcp://a:4841", "entity": "control", "dataset": "env"},
                   "subscriber": {"server": "opc.tcp://b:4841", "entity": "control", "dataset": "setpoints"}},
        }).encode()
        request = urllib.request.Request(base + "/api/publications/cell7/env", data=body,
                                         method="PUT", headers={"Content-Type": "application/json"})
        await asyncio.to_thread(lambda: urllib.request.urlopen(request).read())
        listing = json.loads(await asyncio.to_thread(
            lambda: urllib.request.urlopen(base + "/api/publications").read()))
        detail = json.loads(await asyncio.to_thread(
            lambda: urllib.request.urlopen(base + "/api/publications/cell7/env").read()))
        assert listing[0]["fx"]["connection"] == "cell7/env"        # the tree can mark it
        assert detail["fx"]["publisher"]["dataset"] == "env"        # the detail shows provenance
    finally:
        await registry.close()


def test_browser_renders_fx_provenance():
    from hypernova.registry.ui import INDEX_HTML
    assert "fxbar" in INDEX_HTML                            # the provenance bar
    assert "FX link" in INDEX_HTML                          # the detail badge
    assert "fxtag" in INDEX_HTML                            # the namespace-tree marker
    assert "isMulticast" in INDEX_HTML                      # the honest unicast note
    # every provenance field must be escaped — pin the literal esc() call so a
    # regression dropping it (an XSS hole) fails CI, not just marker-presence
    for field in ("connection", "publisher.server", "publisher.entity", "publisher.dataset",
                  "subscriber.server", "subscriber.entity", "subscriber.dataset"):
        assert f"esc(p.fx.{field})" in INDEX_HTML, f"fx.{field} is rendered unescaped"


def test_store_caps_fx_field_length(tmp_path):
    from hypernova.registry.store import FieldSpec, Publication, Store, StoreError
    store = Store(tmp_path / "reg.json")
    oversized = "x" * 300  # > 256 bytes — the surface widened, so close it like description
    with pytest.raises(StoreError) as excinfo:
        store.register(Publication(name="cell/z", address="opc.udp://239.0.0.7:14842",
                                   publisher_id=93, writer_group_id=202, dataset_writer_id=1,
                                   fields=[FieldSpec("t", "DOUBLE")],
                                   fx={"connection": oversized,
                                       "publisher": {"server": "s", "entity": "e", "dataset": "d"},
                                       "subscriber": {"server": "s", "entity": "e", "dataset": "d"}}))
    assert "fx.connection" in str(excinfo.value) and "too long" in str(excinfo.value)


def test_sanitize_name_truncates_to_bytes_not_characters():
    # 30 euro signs = 90 UTF-8 bytes; must clip to <=64 bytes on a char boundary
    result = fx._sanitize_name("€" * 30)
    assert len(result.encode("utf-8")) <= 64
    assert result == "€" * 21           # 21 * 3 bytes = 63, no half character
    assert "�" not in result             # never a broken/replacement character


def test_reraise_or_wrap_never_swallows_control_flow():
    # ordinary exceptions become a teaching FxError naming the phase
    with pytest.raises(fx.FxError) as excinfo:
        fx._reraise_or_wrap(ValueError("boom"), "while locating the component")
    assert "while locating the component: boom" in str(excinfo.value)
    # an FxError passes straight through (not double-wrapped)
    with pytest.raises(fx.FxNotCapable):
        fx._reraise_or_wrap(fx.FxNotCapable("not fx"), "ctx")
    # cancellation / interrupt / exit are re-raised as themselves — this is the
    # bug the reviewer flagged in connect(): they must never become an FxError
    for control in (asyncio.CancelledError(), KeyboardInterrupt(), SystemExit()):
        with pytest.raises(type(control)):
            fx._reraise_or_wrap(control, "ctx")


async def test_read_endpoint_distinguishes_absent_from_transport_error():
    ua = pytest.importorskip("asyncua").ua
    from asyncua.ua.uaerrors import UaStatusCodeError

    class _NodeId:
        def __init__(self, ident, ns):
            self.Identifier, self.NamespaceIndex = ident, ns

    class _Component:
        nodeid = _NodeId("ProcessCell", 2)

    class _Node:
        def __init__(self, result):
            self._result = result

        async def read_value(self):
            if isinstance(self._result, BaseException):
                raise self._result
            return self._result

    class _ClientStub:
        def __init__(self, by_leaf):
            self.by_leaf = by_leaf

        def get_node(self, nodeid):
            return _Node(self.by_leaf[nodeid.Identifier.rsplit(".", 1)[1]])

    def transport(by_leaf):
        t = fx._AsyncuaTransport("opc.tcp://x")
        t._component, t._client = _Component(), _ClientStub(by_leaf)
        return t

    # a genuinely absent endpoint (Status node unknown) reads as None
    absent = transport({"Status": UaStatusCodeError(ua.StatusCodes.BadNodeIdUnknown)})
    assert await absent.read_endpoint("control", "gone") is None

    # a transport/session failure must propagate, not read as 'missing'
    dropped = transport({"Status": ConnectionResetError("connection reset")})
    with pytest.raises(ConnectionResetError):
        await dropped.read_endpoint("control", "demo")

    # a present endpoint returns its live state
    present = transport({"Status": 3, "Address": "opc.udp://239.0.0.7:14840",
                         "Dataset": "publisher:control.env"})
    assert await present.read_endpoint("control", "demo") == {
        "name": "demo", "status": 3, "address": "opc.udp://239.0.0.7:14840",
        "dataset": "publisher:control.env"}
