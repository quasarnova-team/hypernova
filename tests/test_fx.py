"""FX connection manager — offline. A fake transport mirrors a live supernova
FX server (its address space, its establish/close state machine and its exact
refusal diagnostics), so the whole manager — describe, wire, roll back, status,
undo, teaching errors — is proven without asyncua or a server. The live
end-to-end against real docker servers is tests/e2e_fx.py."""

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

    def __init__(self, config=None):
        self.config = json.loads(json.dumps(config or _CONFIG))
        self.connections = {}  # name -> dict(status, address, dataset, role, entity, ds, live)
        self._auto = 0
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

    def _establish(self, argument):
        if self.reject_establish is not None:
            return self._refuse(self.reject_establish, outputs=2)
        try:
            req = json.loads(argument)
        except ValueError as error:
            return self._refuse(f"JSON error: {error}", outputs=2)
        entity_name = req.get("functionalEntity")
        role = req.get("role")
        ds_name = req.get("dataset")
        entities = self.config["entities"]
        if entity_name not in entities:
            return self._refuse(f"unknown functional entity {entity_name!r}", outputs=2)
        if role not in ("publisher", "subscriber"):
            return self._refuse(f"role must be 'publisher' or 'subscriber', not {role!r}", 2)
        entity = entities[entity_name]
        bucket = "outputs" if role == "publisher" else "inputs"
        kind = "output" if role == "publisher" else "input"
        if ds_name not in entity[bucket]:
            return self._refuse(
                f"functional entity {entity_name!r} has no {kind} dataset {ds_name!r}", 2)
        if role == "subscriber" and not isinstance(req.get("peer"), dict):
            return self._refuse("subscriber role requires 'peer' coordinates", 2)
        # a dataset can be connected once per direction
        for endpoint in self.connections.values():
            if (endpoint["live"] and endpoint["entity"] == entity_name
                    and endpoint["ds"] == ds_name and endpoint["role"] == role):
                return self._refuse(
                    f"{kind} dataset {entity_name}.{ds_name} already connected", 2)
        live_in_entity = sum(1 for e in self.connections.values()
                             if e["live"] and e["entity"] == entity_name)
        if live_in_entity >= 64:
            return self._refuse(f"functional entity {entity_name!r} is at its 64-endpoint ceiling", 2)
        name = req.get("connectionName")
        if not name:
            self._auto += 1
            name = f"cep-{self._auto}"
        status = 3 if role == "publisher" else 2  # publisher Operational, subscriber PreOperational
        self.connections[name] = {
            "status": status, "address": req["address"], "role": role,
            "entity": entity_name, "ds": ds_name, "live": True,
            "dataset": f"{role}:{entity_name}.{ds_name}"}
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
