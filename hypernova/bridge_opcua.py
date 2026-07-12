"""hypernova → classic OPC UA: serve subscribed publications as an ordinary
OPC UA server, so consumers that speak only client/server — WinCC OA first
among them — read hypernova streams today, without Part 14 support.

    hypernova bridge-opcua atlas/dcs/atca/crate1/env atlas/dcs/demo/env \
        --endpoint opc.tcp://0.0.0.0:4840

Each publication becomes an object node (slashes → dots); each field a
variable updated with value, status and source timestamp on every received
DataSetMessage. Requires the [bridge] extra (asyncua).
"""

from __future__ import annotations

import asyncio
import logging

from hypernova.client import Subscriber
from hypernova.wire import FieldValue

logger = logging.getLogger("hypernova.bridge_opcua")

__all__ = ["run_bridge"]


def _status_to_ua(status: int):
    from asyncua import ua
    return ua.StatusCode(ua.StatusCodes.Good if status == 0 else status)


def _field_to_datavalue(field: FieldValue):
    from asyncua import ua
    value = field.value
    variant = ua.Variant(list(value) if isinstance(value, (list, tuple)) else value)
    return ua.DataValue(
        variant,
        StatusCode=_status_to_ua(field.status),
        SourceTimestamp=field.source_datetime.replace(tzinfo=None)
        if field.source_datetime else None,
    )


async def run_bridge(names: list[str], *, endpoint: str, registry: str | None = None,
                     network: str | None = None, ready_event: asyncio.Event | None = None,
                     verify_key: bytes | None = None) -> None:
    try:
        from asyncua import Server, ua
    except ImportError:
        raise SystemExit("bridge-opcua needs the [bridge] extra: pip install 'hypernova[bridge]'")

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("hypernova OPC UA bridge")
    namespace = await server.register_namespace("urn:hypernova:bridge")

    subscribers: list[Subscriber] = []
    variables: dict[tuple[str, str], object] = {}

    objects = server.nodes.objects
    for name in names:
        subscriber = await asyncio.to_thread(
            lambda n=name: Subscriber(n, registry=registry, network=network,
                                      verify_key=verify_key))
        subscribers.append(subscriber)
        node = await objects.add_object(namespace, name.replace("/", "."))
        for field in subscriber._coords.get("fields", []):
            variable = await node.add_variable(
                namespace, field["name"], 0.0 if "DOUBLE" in str(field.get("type", ""))
                else ua.Variant(None))
            await variable.set_writable(False)
            variables[(name, field["name"])] = variable

    async def pump(subscriber: Subscriber) -> None:
        loop = asyncio.get_running_loop()
        subscriber.start()
        while True:
            update = await loop.run_in_executor(None, _next_update, subscriber)
            if update is None:
                continue
            for field_name, field in update.values.items():
                variable = variables.get((subscriber.name, field_name))
                if variable is None:
                    continue
                try:
                    await server.write_attribute_value(
                        variable.nodeid, _field_to_datavalue(field))
                except Exception as error:  # noqa: BLE001 — one bad value must not kill the pump
                    logger.warning("bridge write %s.%s failed: %s",
                                   subscriber.name, field_name, error)

    async with server:
        logger.info("bridge serving %d publication(s) at %s", len(names), endpoint)
        if ready_event is not None:
            ready_event.set()
        await asyncio.gather(*(pump(subscriber) for subscriber in subscribers))


def _next_update(subscriber: Subscriber):
    try:
        return subscriber.get(timeout=1.0)
    except TimeoutError:
        return None
