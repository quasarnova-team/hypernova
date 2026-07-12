"""Client library: the DIP-flat loop — publish by name, subscribe by name,
values with quality and time; plus registry-down behaviour."""

import asyncio
import threading

import pytest
from aiohttp.test_utils import TestServer

from hypernova.client import Publisher, RegistryError, Subscriber
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


async def test_publish_subscribe_by_name(registry_url):
    publisher = await asyncio.to_thread(lambda: Publisher(
        "atlas/dcs/demo/env",
        fields={"temperature": "DOUBLE", "counter": "INT32", "label": "STRING"},
        address="opc.udp://127.0.0.1:24851",
        publisher_id=42, writer_group_id=100, dataset_writer_id=1,
        registry=registry_url, description="loop test"))
    assert publisher.registered

    def run_subscriber(results):
        with Subscriber("atlas/dcs/demo/env", registry=registry_url) as subscriber:
            results.append(subscriber.get(timeout=5.0))

    results: list = []
    thread = threading.Thread(target=run_subscriber, args=(results,))
    thread.start()
    await asyncio.sleep(0.4)
    for tick in range(5):
        publisher.send(temperature=20.0 + tick, counter=tick,
                       label="ok", _status={"counter": STATUS_BAD})
        await asyncio.sleep(0.05)
    thread.join(timeout=6)
    publisher.close()

    assert results, "subscriber received nothing"
    update = results[0]
    assert update.name == "atlas/dcs/demo/env"
    assert set(update.values) == {"temperature", "counter", "label"}
    assert update.values["label"].value == "ok"
    assert update.values["temperature"].is_good
    assert not update.values["counter"].is_good
    assert update.values["temperature"].source_timestamp is not None


async def test_lookup_cache_survives_registry_death(registry_url, tmp_path):
    await asyncio.to_thread(lambda: Publisher(
        "a/b", fields={"v": "INT32"}, address="opc.udp://127.0.0.1:24852",
        publisher_id=1, writer_group_id=1, dataset_writer_id=1,
        registry=registry_url).close())

    subscriber = await asyncio.to_thread(lambda: Subscriber("a/b", registry=registry_url))
    assert subscriber._coords["address"] == "opc.udp://127.0.0.1:24852"

    dead_registry = "http://127.0.0.1:1"
    cached = Subscriber("a/b", registry=dead_registry)
    assert cached._coords["address"] == "opc.udp://127.0.0.1:24852"

    with pytest.raises(RegistryError, match="no cached coordinates"):
        Subscriber("never/registered", registry=dead_registry)


async def test_explicit_coordinates_need_no_registry():
    subscriber = Subscriber(
        "explicit/stream", registry="http://127.0.0.1:1",
        address="opc.udp://127.0.0.1:24853", publisher_id=7,
        writer_group_id=2, dataset_writer_id=3, field_names=["x"])
    publisher = Publisher(
        "explicit/stream", fields={"x": "INT32"},
        address="opc.udp://127.0.0.1:24853",
        publisher_id=7, writer_group_id=2, dataset_writer_id=3,
        registry="http://127.0.0.1:1", register=False)
    assert not publisher.registered
    with subscriber:
        await asyncio.sleep(0.2)
        publisher.send(x=11)
        update = subscriber.get(timeout=5.0)
    publisher.close()
    assert update.values["x"].value == 11


async def test_publisher_field_mismatch_raises(registry_url):
    publisher = await asyncio.to_thread(lambda: Publisher(
        "m/m", fields={"x": "INT32"},
        address="opc.udp://127.0.0.1:24854",
        publisher_id=2, writer_group_id=2, dataset_writer_id=2,
        registry=registry_url))
    with pytest.raises(ValueError, match="missing"):
        publisher.send()
    with pytest.raises(ValueError, match="unknown"):
        publisher.send(x=1, y=2)
    publisher.close()


async def test_subscriber_filters_foreign_streams(registry_url):
    await asyncio.to_thread(lambda: Publisher(
        "filt/mine", fields={"v": "INT32"}, address="opc.udp://127.0.0.1:24855",
        publisher_id=10, writer_group_id=5, dataset_writer_id=1,
        registry=registry_url).close())
    mine = Publisher("filt/mine", fields={"v": "INT32"},
                     address="opc.udp://127.0.0.1:24855",
                     publisher_id=10, writer_group_id=5, dataset_writer_id=1,
                     registry=registry_url, register=False)
    foreign = Publisher("filt/other", fields={"v": "INT32"},
                        address="opc.udp://127.0.0.1:24855",
                        publisher_id=99, writer_group_id=5, dataset_writer_id=1,
                        registry=registry_url, register=False)
    subscriber = await asyncio.to_thread(lambda: Subscriber("filt/mine", registry=registry_url))
    with subscriber:
        await asyncio.sleep(0.2)
        foreign.send(v=666)
        mine.send(v=1)
        update = subscriber.get(timeout=5.0)
        assert update.values["v"].value == 1
    mine.close()
    foreign.close()


async def test_registry_failover_lookup_and_register(registry_url, tmp_path):
    registries = f"http://127.0.0.1:1,{registry_url}"
    publisher = await asyncio.to_thread(lambda: Publisher(
        "fo/pub", fields={"v": "INT32"}, address="opc.udp://239.10.0.77:24863",
        publisher_id=3, writer_group_id=3, dataset_writer_id=3,
        registry=registries))
    assert publisher.registered, "registration must survive one dead registry"
    publisher.close()
    subscriber = await asyncio.to_thread(
        lambda: Subscriber("fo/pub", registry=registries))
    assert subscriber._coords["address"] == "opc.udp://239.10.0.77:24863"
