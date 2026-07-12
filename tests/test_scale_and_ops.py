"""Scale (DIP-cardinality registry), metrics, and mirror convergence."""

import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hypernova.registry.service import create_app
from hypernova.registry.store import FieldSpec, Publication, Store


def make_publication(index: int) -> Publication:
    return Publication(
        name=f"lhc/system{index // 1000}/device{index}/env",
        address=f"opc.udp://239.{(index // 65000) + 10}.{(index // 250) % 250}.{index % 250 + 1}:14840",
        publisher_id=index + 1, writer_group_id=(index % 65535) + 1,
        dataset_writer_id=(index // 65535) + 1,
        fields=[FieldSpec("value", "DOUBLE"), FieldSpec("status", "INT32")],
    )


class TestDipScale:
    N = 55_000

    def test_55k_publications_register_lookup_and_match(self, tmp_path):
        store = Store(None)  # in-memory: measure the data structures, not disk
        started = time.perf_counter()
        for index in range(self.N):
            store.register(make_publication(index))
        register_seconds = time.perf_counter() - started
        assert len(store) == self.N

        started = time.perf_counter()
        for index in range(0, self.N, 7):
            record = make_publication(index)
            assert store.get(record.name) is not None
            found = store.find_stream(record.publisher_id, record.writer_group_id,
                                      record.dataset_writer_id)
            assert found is not None and found.name == record.name
        lookup_seconds = time.perf_counter() - started

        per_match = lookup_seconds / (self.N / 7) / 2
        print(f"\n55k registrations in {register_seconds:.2f}s; "
              f"name+stream lookup {per_match * 1e6:.1f} us each")
        assert register_seconds < 60, "registration throughput collapsed"
        assert per_match < 0.001, "datagram matching must stay sub-millisecond at DIP scale"

    def test_55k_persistence_round_trip(self, tmp_path):
        path = tmp_path / "big.json"
        store = Store(None)
        for index in range(self.N):
            store._by_name[make_publication(index).name] = make_publication(index)
            store._by_stream[make_publication(index).stream_key] = make_publication(index)
        store._path = path
        started = time.perf_counter()
        store._persist()
        persist_seconds = time.perf_counter() - started
        started = time.perf_counter()
        reloaded = Store(path)
        load_seconds = time.perf_counter() - started
        assert len(reloaded) == self.N
        assert reloaded.load_error is None
        print(f"\npersist {persist_seconds:.2f}s, reload {load_seconds:.2f}s "
              f"({path.stat().st_size // 1_000_000} MB)")
        assert persist_seconds < 30 and load_seconds < 30


REGISTRATION = {
    "address": "opc.udp://239.10.0.50:14840",
    "publisherId": 42, "publisherIdType": "UINT16",
    "writerGroupId": 100, "dataSetWriterId": 1,
    "fields": [{"name": "v", "type": "INT32"}],
}


class TestMetrics:
    async def test_prometheus_endpoint(self, tmp_path):
        app = create_app(Store(tmp_path / "reg.json"), listen=False)
        client = TestClient(TestServer(app))
        await client.start_server()
        await client.put("/api/publications/m/pub", json=REGISTRATION)
        response = await client.get("/metrics")
        assert response.status == 200
        body = await response.text()
        assert "hypernova_publications 1" in body
        assert 'hypernova_publication_stale{name="m/pub"} 1' in body
        assert "text/plain" in response.headers["Content-Type"]
        await client.close()


class TestMirror:
    async def test_follower_converges_from_primary(self, tmp_path, monkeypatch):
        primary_app = create_app(Store(tmp_path / "primary.json"), listen=False)
        primary = TestServer(primary_app)
        await primary.start_server()
        primary_client = TestClient(primary)
        await primary_client.start_server()
        await primary_client.put("/api/publications/mirrored/pub", json=REGISTRATION)

        monkeypatch.setattr("hypernova.registry.service.asyncio.sleep",
                            _fast_sleep)
        follower_app = create_app(Store(tmp_path / "follower.json"), listen=False,
                                  mirror_of=f"http://{primary.host}:{primary.port}")
        follower_client = TestClient(TestServer(follower_app))
        await follower_client.start_server()

        for _ in range(50):
            response = await follower_client.get("/api/lookup/mirrored/pub")
            if response.status == 200:
                break
            await asyncio.sleep(0.1)
        assert response.status == 200
        looked_up = await response.json()
        assert looked_up["address"] == REGISTRATION["address"]

        health = await (await follower_client.get("/api/health")).json()
        assert health["mirrorOf"].startswith("http://")
        assert health["mirrorError"] is None
        await follower_client.close()
        await primary_client.close()


async def _fast_sleep(seconds):
    await _real_sleep(min(seconds, 0.05))

_real_sleep = asyncio.sleep
