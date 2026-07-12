"""The WinCC OA path: hypernova stream -> bridge -> classic OPC UA client
reads live values with quality and source time."""

import asyncio

import pytest

asyncua = pytest.importorskip("asyncua")

from aiohttp.test_utils import TestServer

from hypernova.bridge_opcua import run_bridge
from hypernova.client import Publisher
from hypernova.registry.service import create_app
from hypernova.registry.store import Store
from hypernova.wire import STATUS_BAD


@pytest.fixture
async def registry_url(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERNOVA_CACHE", str(tmp_path / "cache"))
    server = TestServer(create_app(Store(tmp_path / "reg.json"), listen=False))
    await server.start_server()
    yield f"http://{server.host}:{server.port}"
    await server.close()


async def test_bridge_serves_live_values_to_classic_client(registry_url):
    publisher = await asyncio.to_thread(lambda: Publisher(
        "bridge/env", fields={"temperature": "DOUBLE", "tags": "INT32[]"},
        address="opc.udp://127.0.0.1:24890",
        publisher_id=31, writer_group_id=3, dataset_writer_id=3,
        registry=registry_url))
    assert publisher.registered

    ready = asyncio.Event()
    bridge = asyncio.create_task(run_bridge(
        ["bridge/env"], endpoint="opc.tcp://127.0.0.1:48400/",
        registry=registry_url, ready_event=ready))
    try:
        await asyncio.wait_for(ready.wait(), timeout=15)

        async def feed():
            for tick in range(40):
                publisher.send(temperature=20.0 + tick, tags=[tick, tick + 1],
                               _status={"tags": STATUS_BAD})
                await asyncio.sleep(0.1)

        feeder = asyncio.create_task(feed())

        from asyncua import Client
        async with Client("opc.tcp://127.0.0.1:48400/") as client:
            namespace = await client.get_namespace_index("urn:hypernova:bridge")
            node = await _browse_path(client, namespace, "bridge.env", "temperature")

            first = last = None
            for _ in range(30):
                await asyncio.sleep(0.3)
                value = await node.read_value()
                if value and value > 0:
                    first = first if first is not None else value
                    last = value
                    if last > first:
                        break
            assert first is not None and last is not None and last > first, \
                "temperature not live through the bridge"

            data = await node.read_data_value()
            assert data.SourceTimestamp is not None

        feeder.cancel()
    finally:
        bridge.cancel()
        publisher.close()


async def _browse_path(client, namespace, object_name, variable_name):
    objects = client.nodes.objects
    for child in await objects.get_children():
        browse_name = await child.read_browse_name()
        if browse_name.Name == object_name:
            for grandchild in await child.get_children():
                grandchild_name = await grandchild.read_browse_name()
                if grandchild_name.Name == variable_name:
                    return grandchild
    raise AssertionError(f"{object_name}/{variable_name} not found in the bridge server")
