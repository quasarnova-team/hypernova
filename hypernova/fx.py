"""OPC UA FX (Field eXchange) from hypernova: be the connection manager.

An FX-capable supernova server describes itself as an *automation component*
whose *functional entities* offer named, preconfigured input/output
*datasets*, and exposes ``EstablishConnections`` / ``CloseConnections``
methods. Any OPC UA client that calls them is an FX *connection manager*;
hypernova is the nicest one — names, not coordinates; one call to wire two
servers; live status; a clean undo. The process data then flows directly
server-to-server over Part 14 Pub/Sub — hypernova carries none of it.

    import asyncio
    from hypernova import fx

    async def main():
        async with fx.connect("opc.tcp://server-a:4841") as a, \\
                   fx.connect("opc.tcp://server-b:4841") as b:
            print(await a.describe())              # what does A offer?
            link = await fx.link(a.publisher("control", "env"),
                                 b.subscriber("control", "setpoints"),
                                 address="opc.udp://239.0.0.7:14840")
            print(await link.status())             # live: Operational / Operational
            await fx.unlink(link)                  # clean undo, both sides

    asyncio.run(main())

The connection *state* lives in the servers' own address spaces (the
``ConnectionEndpoints`` with their live ``Status``), never in the hypernova
registry — see ``doc/fx.md`` for that design decision. Requires the ``[fx]``
extra (asyncua); like the OPC UA bridge, it is imported lazily.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

__all__ = [
    "FxError", "FxRefused", "FxNotCapable",
    "Field", "Dataset", "Entity", "Endpoint", "Component", "Coordinates",
    "DatasetRef", "Link", "FxServer", "connect", "link", "unlink",
    "registry_payload", "STATUS_NAMES",
]

# ConnectionEndpointStatusEnum (OPC 10000-81 §10.17), as the servers report it.
STATUS_NAMES = {0: "Initial", 1: "Ready", 2: "PreOperational", 3: "Operational", 4: "Error"}


# --------------------------------------------------------------------------- #
# Errors that name the fix
# --------------------------------------------------------------------------- #

class FxError(RuntimeError):
    """An FX operation could not be completed; the message says what to do."""


class FxRefused(FxError):
    """A server refused a connection call. Carries the server's own diagnostic
    (the reason it put in ``detail``) so the message teaches, not just reports."""

    def __init__(self, server: str, action: str, diagnostic: str) -> None:
        self.server = server
        self.action = action
        self.diagnostic = diagnostic
        super().__init__(f"{server} refused {action}: {diagnostic}")


class FxNotCapable(FxError):
    """The endpoint answered, but it is not an FX automation component."""


def _reraise_or_wrap(error: BaseException, context: str) -> None:
    """Re-raise an ``FxError`` or a non-``Exception`` ``BaseException``
    (cancellation, ``KeyboardInterrupt``, ``SystemExit``) unchanged; wrap only
    an ordinary exception in a teaching ``FxError``. Control-flow exceptions are
    never swallowed into an ``FxError``."""
    if isinstance(error, FxError) or not isinstance(error, Exception):
        raise error
    raise FxError(f"{context}: {error}") from error


# --------------------------------------------------------------------------- #
# The self-description (what a server offers), all live
# --------------------------------------------------------------------------- #

@dataclass
class Field:
    """One dataset member: its dataset-view name and the cache variable it maps."""

    name: str
    source: str = ""


@dataclass
class Dataset:
    """A preconfigured dataset — what a functional entity can publish or receive."""

    name: str
    direction: str  # "output" (publishable) | "input" (receivable)
    fields: list[Field] = field(default_factory=list)

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


@dataclass
class Endpoint:
    """A live connection endpoint on a server — one wired (or once-wired)
    connection, with its status straight from the server."""

    name: str
    status: int
    address: str = ""
    dataset: str = ""

    @property
    def status_name(self) -> str:
        return STATUS_NAMES.get(self.status, f"Unknown({self.status})")

    @property
    def is_operational(self) -> bool:
        return self.status == 3


@dataclass
class Entity:
    """A functional entity: named output and input datasets, and its live
    connection endpoints."""

    name: str
    outputs: list[Dataset] = field(default_factory=list)
    inputs: list[Dataset] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)

    def dataset(self, name: str, direction: str) -> Dataset | None:
        for d in (self.outputs if direction == "output" else self.inputs):
            if d.name == name:
                return d
        return None


@dataclass
class Component:
    """An automation component's self-description: the entities it offers and
    the connections it currently holds — a live snapshot of one server."""

    name: str
    node_id: str
    entities: list[Entity] = field(default_factory=list)

    def entity(self, name: str) -> Entity | None:
        for e in self.entities:
            if e.name == name:
                return e
        return None

    def __str__(self) -> str:
        lines = [f"{self.name}  ({self.node_id})"]
        for e in self.entities:
            lines.append(f"  entity {e.name}")
            for d in e.outputs:
                lines.append(f"    output  {d.name:<16} {', '.join(d.field_names)}")
            for d in e.inputs:
                lines.append(f"    input   {d.name:<16} {', '.join(d.field_names)}")
            if e.endpoints:
                for ep in e.endpoints:
                    lines.append(f"    connection {ep.name:<13} {ep.status_name}"
                                 f"  {ep.dataset}  {ep.address}")
            else:
                lines.append("    connection (none)")
        return "\n".join(lines)


@dataclass
class Coordinates:
    """The publishing side's wire identity — exactly what a subscriber needs as
    its ``peer`` to receive the stream."""

    publisher_id: int
    writer_group_id: int
    dataset_writer_id: int
    publisher_id_type: str = "UInt16"

    @classmethod
    def from_detail(cls, detail: dict) -> "Coordinates | None":
        c = (detail or {}).get("coordinates")
        # any missing key means the publisher gave us nothing to wire a
        # subscriber with — return None so link() takes the rollback path
        # rather than raising KeyError past its rollback guard.
        if not isinstance(c, dict) or not all(
                k in c for k in ("publisherId", "writerGroupId", "dataSetWriterId")):
            return None
        return cls(
            publisher_id=c["publisherId"],
            writer_group_id=c["writerGroupId"],
            dataset_writer_id=c["dataSetWriterId"],
            publisher_id_type=c.get("publisherIdType", "UInt16"))

    def as_peer(self) -> dict:
        return {
            "publisherId": self.publisher_id,
            "publisherIdType": self.publisher_id_type,
            "writerGroupId": self.writer_group_id,
            "dataSetWriterId": self.dataset_writer_id,
        }


# --------------------------------------------------------------------------- #
# The transport seam — real asyncua, or an injected fake for tests
# --------------------------------------------------------------------------- #

class _Transport:
    """The four OPC UA operations FX needs, isolated so the manager logic is
    testable without a server. ``call`` never raises on a Bad status — it
    returns the status and the output arguments, because a refused FX call
    carries its diagnostic *in* the outputs."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def snapshot(self) -> dict: ...
    async def call(self, method: str, argument: str) -> tuple[int, list[str]]: ...

    async def read_source_type(self, source: str) -> str | None:
        """Wire type of a dataset field's source cache variable, if resolvable
        (used only by the optional registry bridge)."""
        return None

    async def read_endpoint(self, entity: str, name: str) -> dict | None:
        """One connection endpoint's live state, or None if it does not exist.
        Default: derive from a full snapshot; the asyncua transport overrides
        this to read just the endpoint's Status/Address/Dataset variables."""
        for e in (await self.snapshot()).get("entities", []):
            if e["name"] != entity:
                continue
            for ep in e.get("endpoints", []):
                if ep["name"] == name:
                    return ep
        return None


class _AsyncuaTransport(_Transport):
    """Talks to a live FX server over OPC UA client/server with asyncua."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._client = None
        self._component = None
        self._methods: dict = {}

    async def connect(self) -> None:
        try:
            from asyncua import Client
        except ImportError:
            raise FxError("hypernova FX needs the [fx] extra (asyncua): "
                          "pip install 'hypernova[fx]'") from None
        self._client = Client(url=self.url)
        try:
            await self._client.connect()
        except Exception as error:  # noqa: BLE001 — many asyncua/socket error types
            raise FxError(f"cannot reach FX server at {self.url}: {error}. "
                          "Check the opc.tcp endpoint and that the server is up.") from None
        # If locating the component fails (e.g. a non-FX endpoint), the session
        # and asyncua's two keepalive/subscription tasks are already live — tear
        # them down before propagating, so a failed connect() leaks nothing. A
        # cancellation here must propagate as cancellation, not become an FxError.
        try:
            await self._locate()
        except BaseException as error:
            await self.disconnect()
            _reraise_or_wrap(error, f"could not read the FX component on {self.url}")

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass

    async def _locate(self) -> None:
        objects = self._client.nodes.objects
        for child in await objects.get_children():
            if child.nodeid.NamespaceIndex == 0:  # skip the standard Server/Aliases nodes
                continue
            for grandchild in await child.get_children():
                if (await grandchild.read_browse_name()).Name == "FunctionalEntities":
                    self._component = child
                    break
            if self._component is not None:
                break
        if self._component is None:
            raise FxNotCapable(
                f"{self.url} is not an FX automation component (no object with a "
                "FunctionalEntities folder). An FX-capable supernova server declares "
                "an <Fx automationComponent=...> element in its configuration.")
        for method in await self._component.get_children():
            name = (await method.read_browse_name()).Name
            if name in ("EstablishConnections", "CloseConnections"):
                self._methods[name] = method

    async def call(self, method: str, argument: str) -> tuple[int, list[str]]:
        from asyncua import ua
        node = self._methods.get(method)
        if node is None:
            raise FxError(f"{self.url}: automation component exposes no {method} method")
        request = ua.CallMethodRequest()
        request.ObjectId = self._component.nodeid
        request.MethodId = node.nodeid
        request.InputArguments = [ua.Variant(argument, ua.VariantType.String)]
        result = (await self._client.uaclient.call([request]))[0]
        outputs = [o.Value for o in result.OutputArguments]
        return result.StatusCode.value, outputs

    async def snapshot(self) -> dict:
        async def value(node):
            try:
                return await node.read_value()
            except Exception:  # noqa: BLE001 — a missing/unreadable child must not abort describe
                return None

        async def children_by_name(node):
            out = {}
            for child in await node.get_children():
                out[(await child.read_browse_name()).Name] = child
            return out

        comp = self._component
        entities = []
        fe = (await children_by_name(comp)).get("FunctionalEntities")
        for entity_name, entity_node in (await children_by_name(fe)).items():
            folders = await children_by_name(entity_node)
            datasets = {"output": [], "input": []}
            for folder_name, direction in (("OutputData", "output"), ("InputData", "input")):
                folder = folders.get(folder_name)
                if folder is None:
                    continue
                for ds_name, ds_node in (await children_by_name(folder)).items():
                    fields = []
                    for f_name, f_node in (await children_by_name(ds_node)).items():
                        fields.append({"name": f_name, "source": await value(f_node) or ""})
                    datasets[direction].append({"name": ds_name, "fields": fields})
            endpoints = []
            ce = folders.get("ConnectionEndpoints")
            if ce is not None:
                for ep_name, ep_node in (await children_by_name(ce)).items():
                    parts = await children_by_name(ep_node)
                    status = await value(parts["Status"]) if "Status" in parts else None
                    endpoints.append({
                        "name": ep_name,
                        "status": int(status) if status is not None else 4,
                        "address": (await value(parts["Address"]) if "Address" in parts else "") or "",
                        "dataset": (await value(parts["Dataset"]) if "Dataset" in parts else "") or "",
                    })
            entities.append({"name": entity_name, "outputs": datasets["output"],
                             "inputs": datasets["input"], "endpoints": endpoints})
        return {"component": (await comp.read_browse_name()).Name,
                "nodeId": comp.nodeid.to_string(), "entities": entities}

    async def read_endpoint(self, entity: str, name: str) -> dict | None:
        """Read just this endpoint's Status/Address/Dataset — three node reads,
        not a full address-space browse (status polling calls this often). A
        genuinely absent endpoint reads as ``None``; a transport or session
        failure *propagates*, so status polling never misreports a dead server
        as 'missing' (which would silently burn a wait_operational timeout) and
        unlink never mistakes a dropped connection for 'already closed'."""
        from asyncua import ua
        from asyncua.ua.uaerrors import UaStatusCodeError
        identifier = self._component.nodeid.Identifier
        namespace = self._component.nodeid.NamespaceIndex
        base = f"{identifier}.FunctionalEntities.{entity}.ConnectionEndpoints.{name}"

        def node(leaf):
            return self._client.get_node(ua.NodeId(f"{base}.{leaf}", namespace))

        try:
            status = await node("Status").read_value()
        except UaStatusCodeError as error:
            if error.code in (ua.StatusCodes.BadNodeIdUnknown, ua.StatusCodes.BadNodeIdInvalid):
                return None  # the endpoint node does not exist — genuinely absent
            raise  # any other status (and every transport error) is a real failure

        async def optional(leaf):
            try:
                return await node(leaf).read_value()
            except UaStatusCodeError:
                return None  # tolerate a missing Address/Dataset variable, not a drop

        address, dataset = await asyncio.gather(optional("Address"), optional("Dataset"))
        return {"name": name, "status": int(status),
                "address": address or "", "dataset": dataset or ""}

    async def read_source_type(self, source: str) -> str | None:
        """The wire type of a source cache variable (for the registry bridge).
        Reads the actual variable node, not the descriptor view."""
        from asyncua import ua
        try:
            node = self._client.get_node(f"ns=2;s={source}")
            vtype = await node.read_data_type_as_variant_type()
            rank = await node.read_attribute(ua.AttributeIds.ValueRank)
            suffix = "[]" if (rank.Value.Value or -1) >= 1 else ""
            return vtype.name.upper() + suffix
        except Exception:  # noqa: BLE001 — best-effort; caller falls back
            return None


# --------------------------------------------------------------------------- #
# Snapshot -> dataclasses
# --------------------------------------------------------------------------- #

def _component_from_snapshot(snap: dict) -> Component:
    entities = []
    for e in snap.get("entities", []):
        def datasets(items, direction):
            return [Dataset(d["name"], direction,
                            [Field(f["name"], f.get("source", "")) for f in d.get("fields", [])])
                    for d in items]
        endpoints = [Endpoint(ep["name"], ep["status"], ep.get("address", ""), ep.get("dataset", ""))
                     for ep in e.get("endpoints", [])]
        entities.append(Entity(e["name"], datasets(e.get("outputs", []), "output"),
                               datasets(e.get("inputs", []), "input"), endpoints))
    return Component(snap.get("component", "?"), snap.get("nodeId", "?"), entities)


def _is_good(status: int) -> bool:
    return (status & 0xC0000000) == 0  # severity bits 00 = Good


def _diagnostic(outputs: list[str]) -> str:
    """Dig the server's ``detail`` diagnostic out of a refused call's outputs."""
    for out in reversed(outputs):
        if not out:
            continue
        try:
            parsed = json.loads(out)
        except (ValueError, TypeError):
            return str(out)
        if isinstance(parsed, dict) and parsed.get("diagnostic"):
            return parsed["diagnostic"]
    return "no diagnostic returned"


# --------------------------------------------------------------------------- #
# One server handle
# --------------------------------------------------------------------------- #

class FxServer:
    """A handle to one FX-capable server, used as an async context manager.
    Every method reflects the server's live state — this class holds no cached
    connection state of its own."""

    def __init__(self, url: str, *, transport: _Transport | None = None) -> None:
        self.url = url
        self._t = transport or _AsyncuaTransport(url)

    async def connect(self) -> "FxServer":
        await self._t.connect()
        return self

    async def close(self) -> None:
        await self._t.disconnect()

    async def __aenter__(self) -> "FxServer":
        return await self.connect()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def describe(self) -> Component:
        """The server's live self-description: entities, datasets, connections."""
        return _component_from_snapshot(await self._t.snapshot())

    def publisher(self, entity: str, dataset: str) -> "DatasetRef":
        """Name an output dataset on this server to publish from."""
        return DatasetRef(self, entity, dataset, "publisher")

    def subscriber(self, entity: str, dataset: str) -> "DatasetRef":
        """Name an input dataset on this server to receive into."""
        return DatasetRef(self, entity, dataset, "subscriber")

    async def establish(self, *, entity: str, role: str, dataset: str, address: str,
                        connection_name: str | None = None, peer: dict | None = None,
                        publishing_interval_ms: int | None = None,
                        ttl: int | None = None) -> tuple[str, dict]:
        """Establish one side of a connection; returns (connectionId, detail).
        Raises :class:`FxRefused` with the server's diagnostic on refusal."""
        argument = {"functionalEntity": entity, "role": role,
                    "dataset": dataset, "address": address}
        if connection_name is not None:
            argument["connectionName"] = connection_name
        if peer is not None:
            argument["peer"] = peer
        if publishing_interval_ms is not None:
            argument["publishingIntervalMs"] = publishing_interval_ms
        if ttl is not None:
            argument["ttl"] = ttl
        status, outputs = await self._t.call("EstablishConnections", json.dumps(argument))
        if not _is_good(status):
            raise FxRefused(self.url, "EstablishConnections", _diagnostic(outputs))
        connection_id = outputs[0] if outputs else ""
        detail = json.loads(outputs[1]) if len(outputs) > 1 and outputs[1] else {}
        return connection_id, detail

    async def close_connection(self, connection_id: str) -> dict:
        """Close a connection by id; returns the server's detail. Raises
        :class:`FxRefused` if there is no such live connection — :func:`unlink`
        tolerates that (an already-closed side is fine), a bare close does not."""
        status, outputs = await self._t.call("CloseConnections", connection_id)
        if not _is_good(status):
            raise FxRefused(self.url, "CloseConnections", _diagnostic(outputs))
        return json.loads(outputs[0]) if outputs and outputs[0] else {}

    async def endpoints(self, entity: str | None = None) -> list[Endpoint]:
        """The live connection endpoints (optionally for one entity)."""
        component = await self.describe()
        result = []
        for e in component.entities:
            if entity is not None and e.name != entity:
                continue
            result.extend(e.endpoints)
        return result

    async def endpoint(self, entity: str, connection_name: str) -> Endpoint | None:
        """One endpoint's live state (cheap: reads only its variables)."""
        raw = await self._t.read_endpoint(entity, connection_name)
        if raw is None:
            return None
        return Endpoint(raw["name"], raw["status"], raw.get("address", ""), raw.get("dataset", ""))

    async def find_endpoint(self, connection_name: str) -> Endpoint | None:
        """Locate an endpoint by name across all entities (entity unknown)."""
        for ep in await self.endpoints():
            if ep.name == connection_name:
                return ep
        return None

    async def source_type(self, source: str) -> str | None:
        """The hypernova wire type of a field's source cache variable — for
        naming an FX-created stream in the registry (see doc/fx.md)."""
        return await self._t.read_source_type(source)


def connect(url: str, *, transport: _Transport | None = None) -> FxServer:
    """Handle to an FX server: ``async with fx.connect(url) as server: ...``."""
    return FxServer(url, transport=transport)


# --------------------------------------------------------------------------- #
# Wiring two servers together
# --------------------------------------------------------------------------- #

@dataclass
class DatasetRef:
    """One end of a wire: a dataset on a server, in a role. Produced by
    ``server.publisher(...)`` / ``server.subscriber(...)``."""

    server: FxServer
    entity: str
    dataset: str
    role: str  # "publisher" | "subscriber"


@dataclass
class Link:
    """A wired connection between two servers. Holds no live state itself —
    :meth:`status` reads it back from both servers on demand."""

    name: str
    publisher: DatasetRef
    subscriber: DatasetRef
    coordinates: Coordinates
    address: str
    listen_address: str
    publisher_connection_id: str = ""
    subscriber_connection_id: str = ""

    async def status(self) -> dict:
        """Both endpoints, live: {'publisher': Endpoint|None, 'subscriber': ...}."""
        return {
            "publisher": await self.publisher.server.endpoint(
                self.publisher.entity, self.publisher_connection_id),
            "subscriber": await self.subscriber.server.endpoint(
                self.subscriber.entity, self.subscriber_connection_id),
        }

    async def is_operational(self) -> bool:
        state = await self.status()
        return bool(state["publisher"] and state["publisher"].is_operational
                    and state["subscriber"] and state["subscriber"].is_operational)

    async def wait_operational(self, timeout: float = 10.0, poll: float = 0.2) -> dict:
        """Poll until both endpoints report Operational, or raise TimeoutError
        naming the last-seen state (a subscriber goes Operational on first data)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        state: dict = {}
        while loop.time() < deadline:
            state = await self.status()
            if (state["publisher"] and state["publisher"].is_operational
                    and state["subscriber"] and state["subscriber"].is_operational):
                return state
            await asyncio.sleep(poll)
        pub = state.get("publisher")
        sub = state.get("subscriber")
        raise TimeoutError(
            f"link {self.name!r} not Operational within {timeout}s "
            f"(publisher={pub.status_name if pub else 'missing'}, "
            f"subscriber={sub.status_name if sub else 'missing'}). "
            "If the subscriber is stuck PreOperational, no data is arriving — "
            "check the data-plane address reachability between the two servers.")


def _sanitize_name(name: str) -> str:
    clean = "".join(c for c in name if c.isprintable() and ord(c) >= 32)
    # the server bounds the name at 64 *bytes*, not characters; truncate on the
    # UTF-8 boundary so a multibyte character is never cut in half.
    return clean.encode("utf-8")[:64].decode("utf-8", "ignore") or "cep"


def _connection_name(name: str | None, publisher: DatasetRef, subscriber: DatasetRef) -> str:
    if name:
        return _sanitize_name(name)
    return _sanitize_name(f"{publisher.dataset}-to-{subscriber.dataset}")


def _require_dataset(component: Component, entity_name: str, dataset_name: str,
                     direction: str) -> Dataset:
    entity = component.entity(entity_name)
    if entity is None:
        offered = ", ".join(e.name for e in component.entities) or "(none)"
        raise FxError(f"{component.name!r} has no functional entity {entity_name!r}; "
                      f"it offers: {offered}")
    dataset = entity.dataset(dataset_name, direction)
    if dataset is None:
        available = ", ".join(d.name for d in
                              (entity.outputs if direction == "output" else entity.inputs)) or "(none)"
        verb = "publish from" if direction == "output" else "receive into"
        raise FxError(f"entity {entity_name!r} has no {direction} dataset {dataset_name!r} "
                      f"to {verb}; available {direction} datasets: {available}")
    return dataset


async def _safe_close(server: FxServer, connection_id: str) -> str | None:
    """Best-effort rollback close. Shielded so that a cancellation of the
    caller cannot abandon a just-established publisher; returns an error string
    (never raises) for any ordinary failure, letting cancellation propagate."""
    try:
        await asyncio.shield(server.close_connection(connection_id))
        return None
    except Exception as error:  # noqa: BLE001 — rollback must not raise over the real cause
        return str(error)


async def link(publisher: DatasetRef, subscriber: DatasetRef, *, address: str,
               listen_address: str | None = None, name: str | None = None,
               publishing_interval_ms: int | None = None, ttl: int | None = None) -> Link:
    """Wire a publisher dataset on one server to a subscriber dataset on another.

    ``address`` is where the publisher sends datagrams (a multicast group, or a
    peer's unicast address). ``listen_address`` is where the subscriber binds;
    it defaults to ``address`` (correct for a multicast group). For unicast,
    pass the subscriber's reachable address as ``address`` and
    ``opc.udp://0.0.0.0:<port>`` as ``listen_address``.

    Both sides are pre-validated against each server's live self-description, so
    a wrong entity/dataset/role fails *before* any server state changes, naming
    the legal choices. The wiring is atomic: if the subscriber side fails, the
    publisher side is rolled back so no half-open connection is left running.
    """
    if publisher.role != "publisher" or subscriber.role != "subscriber":
        raise FxError("link() wires a publisher into a subscriber — call it as "
                      "fx.link(a.publisher(entity, dataset), b.subscriber(entity, dataset), ...)")

    pub_component = await publisher.server.describe()
    sub_component = await subscriber.server.describe()
    _require_dataset(pub_component, publisher.entity, publisher.dataset, "output")
    _require_dataset(sub_component, subscriber.entity, subscriber.dataset, "input")

    connection_name = _connection_name(name, publisher, subscriber)
    listen = listen_address or address

    try:
        pub_id, pub_detail = await publisher.server.establish(
            entity=publisher.entity, role="publisher", dataset=publisher.dataset,
            address=address, connection_name=connection_name,
            publishing_interval_ms=publishing_interval_ms, ttl=ttl)
    except FxError:
        raise  # already a teaching error; nothing established, nothing to undo
    except Exception as error:  # noqa: BLE001 — transport/asyncua failure, name the phase
        raise FxError(f"publisher side failed to establish on {publisher.server.url}: "
                      f"{error}") from error

    coordinates = Coordinates.from_detail(pub_detail)
    if coordinates is None:
        await _safe_close(publisher.server, pub_id)
        raise FxError(f"publisher established on {publisher.server.url} but returned no wire "
                      "coordinates, so the subscriber cannot be wired; rolled the publisher back")

    try:
        sub_id, _ = await subscriber.server.establish(
            entity=subscriber.entity, role="subscriber", dataset=subscriber.dataset,
            address=listen, connection_name=connection_name, peer=coordinates.as_peer())
    except BaseException as error:
        # Roll the publisher back however the subscriber side failed — a refusal,
        # a dropped connection, a timeout, or a cancellation — so a failure never
        # leaves the publisher emitting into a half-open link. Cancellation is
        # re-raised (not wrapped) after the rollback so it still cancels.
        rollback = await _safe_close(publisher.server, pub_id)
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        message = (f"subscriber side failed on {subscriber.server.url} ({error}); "
                   f"rolled the publisher side back on {publisher.server.url} to avoid a "
                   "half-open link")
        if rollback is not None:
            message += f" — but the rollback close also failed: {rollback}"
        raise FxError(message) from error

    return Link(name=connection_name, publisher=publisher, subscriber=subscriber,
                coordinates=coordinates, address=address, listen_address=listen,
                publisher_connection_id=pub_id, subscriber_connection_id=sub_id)


async def _confirm_closed(server: FxServer, entity: str | None, connection_id: str) -> bool:
    """After a close was refused, decide whether the side is down anyway by
    *reading its status back* — absent or Initial means already closed
    (idempotent success) — rather than string-matching the server's prose."""
    try:
        endpoint = (await server.endpoint(entity, connection_id) if entity is not None
                    else await server.find_endpoint(connection_id))
    except Exception:  # noqa: BLE001 — if we cannot even read it, treat the refusal as real
        return False
    return endpoint is None or endpoint.status == 0


async def unlink(link: Link) -> None:
    """Close both sides of a link. Attempts both even if one fails — however it
    fails, transport error included — and raises only after, so one broken side
    never strands the other. Idempotent: a side already down is not an error."""
    errors = []
    for side, connection_id in ((link.subscriber, link.subscriber_connection_id),
                                (link.publisher, link.publisher_connection_id)):
        try:
            await side.server.close_connection(connection_id)
        except FxRefused as error:
            if not await _confirm_closed(side.server, side.entity, connection_id):
                errors.append(f"{side.role} on {side.server.url}: {error}")
        except Exception as error:  # noqa: BLE001 — never let one side's failure strand the other
            errors.append(f"{side.role} on {side.server.url}: {error}")
    if errors:
        raise FxError("unlink incomplete: " + "; ".join(errors))


async def registry_payload(link: Link, *, name: str | None = None,
                           description: str = "") -> dict:
    """Build a hypernova registry registration body for the data-plane stream a
    link's publisher produces — so an FX-created flow can be browsed and
    subscribed by name like any other publication. The FX *connection* state
    stays server-owned; this only names the resulting Part 14 stream. Field
    types are read from the source cache variables; an unreadable type is
    refused (not defaulted — a wrong type would drive UADP decoding).
    See ``doc/fx.md`` for the registry-relationship decision."""
    server = link.publisher.server
    component = await server.describe()
    entity = component.entity(link.publisher.entity)
    dataset = entity.dataset(link.publisher.dataset, "output") if entity else None
    if dataset is None:
        raise FxError(f"cannot name stream: {link.publisher.dataset!r} is no longer an "
                      f"output dataset on {server.url}")
    fields, unresolved = [], []
    for f in dataset.fields:
        wire_type = await server.source_type(f.source)
        if wire_type is None:
            unresolved.append(f.name)
        fields.append({"name": f.name, "type": wire_type})
    if unresolved:
        raise FxError(
            f"cannot name stream {name or link.name!r}: the wire type of field(s) "
            f"{', '.join(unresolved)} could not be read from their source cache "
            f"variables on {server.url}. Register it explicitly instead: "
            "`hypernova register <name> --field name=TYPE ...` with the coordinates "
            f"publisherId={link.coordinates.publisher_id}, "
            f"writerGroupId={link.coordinates.writer_group_id}, "
            f"dataSetWriterId={link.coordinates.dataset_writer_id}.")
    coordinates = link.coordinates
    return {
        "name": name or link.name,
        "address": link.address,
        "publisherId": coordinates.publisher_id,
        "publisherIdType": coordinates.publisher_id_type.upper(),
        "writerGroupId": coordinates.writer_group_id,
        "dataSetWriterId": coordinates.dataset_writer_id,
        "fields": fields,
        "description": description or f"FX link {link.name}",
        "replace": True,
    }
