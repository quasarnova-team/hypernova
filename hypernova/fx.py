"""OPC UA FX connection manager: wire two FX-capable servers together.

A supernova server with an ``<Fx>`` section exposes FunctionalEntities whose
datasets are preconfigured; its ``EstablishConnections`` / ``CloseConnections``
methods (on the AutomationComponent object) activate them. This module is the
client side — the ConnectionManager of OPC UA FX (Part 81), in hypernova's
five-line spirit:

    hypernova fx connect \
        --publisher  opc.tcp://plc-a:4841 --pub-entity control --pub-dataset env \
        --subscriber opc.tcp://plc-b:4841 --sub-entity control --sub-dataset setpoints \
        --address opc.udp://239.192.0.20:4841

The publisher side is established first; its reply carries the wire
coordinates (publisherId / writerGroupId / dataSetWriterId), which are handed
to the subscriber side as its peer. Optionally the resulting stream is
registered in a hypernova registry so it shows up in the browser like any
other publication.

Requires the [bridge] extra (asyncua).
"""

from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger("hypernova.fx")

__all__ = ["establish", "close", "endpoints", "connect_pair"]

_UA_TO_HYPERNOVA_TYPE = {
    "Boolean": "BOOLEAN",
    "SByte": "SBYTE",
    "Byte": "BYTE",
    "Int16": "INT16",
    "UInt16": "UINT16",
    "Int32": "INT32",
    "UInt32": "UINT32",
    "Int64": "INT64",
    "UInt64": "UINT64",
    "Float": "FLOAT",
    "Double": "DOUBLE",
    "String": "STRING",
    "DateTime": "DATETIME",
}


def _need_asyncua():
    try:
        from asyncua import Client  # noqa: F401
    except ImportError:
        raise SystemExit("fx needs the [bridge] extra: pip install 'hypernova[bridge]'")


async def _component_node(client, component: str | None):
    """The AutomationComponent object: by name, or discovered as the object
    under Objects that carries an EstablishConnections method."""
    ns = 2
    if component:
        node = client.get_node(f"ns={ns};s={component}")
        try:
            await node.read_browse_name()
        except Exception:
            raise SystemExit(f"no object '{component}' on the server (ns={ns} string id)")
        return node, component
    objects = client.nodes.objects
    for child in await objects.get_children():
        try:
            names = [(await c.read_browse_name()).Name for c in await child.get_children()]
        except Exception:
            continue
        if "EstablishConnections" in names:
            name = (await child.read_browse_name()).Name
            return child, name
    raise SystemExit("no AutomationComponent found (no object with an EstablishConnections method); "
                     "is this an FX-enabled server?")


async def _call(client, component: str | None, method: str, argument: str):
    node, name = await _component_node(client, component)
    from asyncua import ua
    result = await node.call_method(f"2:{method}", ua.Variant(argument, ua.VariantType.String))
    return result, name


async def establish(url: str, *, component: str | None, entity: str, role: str,
                    dataset: str, address: str, interval: float | None = None,
                    peer: dict | None = None, name: str | None = None,
                    ttl: int | None = None) -> tuple[str, dict]:
    """EstablishConnections on one server; returns (connectionId, detail)."""
    _need_asyncua()
    from asyncua import Client

    request: dict = {
        "functionalEntity": entity,
        "role": role,
        "dataset": dataset,
        "address": address,
    }
    if interval is not None:
        request["publishingIntervalMs"] = interval
    if peer is not None:
        request["peer"] = peer
    if name:
        request["connectionName"] = name
    if ttl is not None:
        request["ttl"] = ttl

    async with Client(url=url) as client:
        outputs, component_name = await _call(
            client, component, "EstablishConnections", json.dumps(request))
    connection_id, detail_text = outputs
    detail = json.loads(detail_text) if detail_text else {}
    logger.info("established %s on %s (%s): %s", connection_id, url, component_name, detail)
    return connection_id, detail


async def close(url: str, *, component: str | None, connection_id: str) -> dict:
    """CloseConnections on one server; returns the detail object."""
    _need_asyncua()
    from asyncua import Client

    async with Client(url=url) as client:
        result, _ = await _call(client, component, "CloseConnections", connection_id)
    detail = json.loads(result) if isinstance(result, str) and result else {}
    return detail


async def endpoints(url: str, *, component: str | None) -> list[dict]:
    """Every ConnectionEndpoint on the server, with its live state."""
    _need_asyncua()
    from asyncua import Client

    found: list[dict] = []
    status_names = {0: "Initial", 1: "Ready", 2: "PreOperational", 3: "Operational", 4: "Error"}
    async with Client(url=url) as client:
        node, component_name = await _component_node(client, component)
        entities_node = None
        for child in await node.get_children():
            if (await child.read_browse_name()).Name == "FunctionalEntities":
                entities_node = child
                break
        if entities_node is None:
            return found
        for entity_node in await entities_node.get_children():
            entity_name = (await entity_node.read_browse_name()).Name
            for sub in await entity_node.get_children():
                if (await sub.read_browse_name()).Name != "ConnectionEndpoints":
                    continue
                for endpoint in await sub.get_children():
                    record = {
                        "component": component_name,
                        "entity": entity_name,
                        "connection": (await endpoint.read_browse_name()).Name,
                    }
                    for prop in await endpoint.get_children():
                        prop_name = (await prop.read_browse_name()).Name
                        try:
                            value = await prop.read_value()
                        except Exception:
                            continue
                        if prop_name == "Status":
                            record["status"] = status_names.get(value, str(value))
                        else:
                            record[prop_name.lower()] = value
                    found.append(record)
    return found


async def _dataset_fields(url: str, component: str | None, entity: str,
                          dataset: str, folder: str) -> list[tuple[str, str]]:
    """Field (name, hypernova type) pairs of a preconfigured dataset, read
    from the FX view: each field variable's value is the mapped address,
    whose node carries the actual data type."""
    from asyncua import Client

    fields: list[tuple[str, str]] = []
    async with Client(url=url) as client:
        node, _ = await _component_node(client, component)
        dataset_node = await node.get_child(
            ["2:FunctionalEntities", f"2:{entity}", f"2:{folder}", f"2:{dataset}"])
        for field in await dataset_node.get_children():
            field_name = (await field.read_browse_name()).Name
            mapped_address = await field.read_value()
            mapped = client.get_node(f"ns=2;s={mapped_address}")
            variant_type = (await mapped.read_data_type_as_variant_type()).name
            fields.append((field_name, _UA_TO_HYPERNOVA_TYPE.get(variant_type, "STRING")))
    return fields


async def connect_pair(*, publisher_url: str, publisher_component: str | None,
                       publisher_entity: str, publisher_dataset: str,
                       subscriber_url: str, subscriber_component: str | None,
                       subscriber_entity: str, subscriber_dataset: str,
                       address: str, interval: float | None = None,
                       name: str | None = None, ttl: int | None = None,
                       register: str | None = None, register_as: str | None = None,
                       network: str | None = None) -> dict:
    """The FX ConnectionManager happy path: publisher first, then the
    subscriber with the publisher's coordinates as peer."""
    pub_id, pub_detail = await establish(
        publisher_url, component=publisher_component, entity=publisher_entity,
        role="publisher", dataset=publisher_dataset, address=address,
        interval=interval, name=name, ttl=ttl)
    coordinates = pub_detail.get("coordinates")
    if not coordinates:
        raise SystemExit("publisher side established but returned no coordinates — "
                         "is the server a supernova with FX?")

    try:
        sub_id, sub_detail = await establish(
            subscriber_url, component=subscriber_component, entity=subscriber_entity,
            role="subscriber", dataset=subscriber_dataset, address=address,
            peer=coordinates, name=name)
    except Exception:
        logger.warning("subscriber side failed; closing the publisher side '%s' again", pub_id)
        try:
            await close(publisher_url, component=publisher_component, connection_id=pub_id)
        except Exception as undo_error:
            logger.warning("undo failed too: %s", undo_error)
        raise

    result = {
        "publisher": {"url": publisher_url, "connectionId": pub_id, "detail": pub_detail},
        "subscriber": {"url": subscriber_url, "connectionId": sub_id, "detail": sub_detail},
        "address": address,
        "coordinates": coordinates,
    }

    if register and register_as:
        from hypernova.client import _registry_call
        fields = await _dataset_fields(
            publisher_url, publisher_component, publisher_entity, publisher_dataset, "OutputData")
        payload = {
            "address": address,
            "publisherId": int(coordinates["publisherId"]),
            "publisherIdType": str(coordinates.get("publisherIdType", "UInt16")).upper(),
            "writerGroupId": int(coordinates["writerGroupId"]),
            "dataSetWriterId": int(coordinates["dataSetWriterId"]),
            "description": f"FX connection {pub_id} ({publisher_entity}.{publisher_dataset})",
            "endpoints": {network: address} if network else {},
            "fields": [{"name": n, "type": t} for n, t in fields],
            "replace": True,
        }
        _registry_call("PUT", f"{register}/api/publications/{register_as}", payload)
        result["registered"] = register_as

    return result
