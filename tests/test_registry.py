"""Registry: store semantics (collisions, persistence, leases) and the REST
API including the live path fed by real datagrams on the loopback."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hypernova.registry.store import FieldSpec, Publication, Store, StoreError
from hypernova.registry.service import create_app
from hypernova import transport
from hypernova.wire import (
    STATUS_UNCERTAIN,
    BuiltinType,
    DataSetMessage,
    FieldValue,
    NetworkMessage,
    encode_network_message,
    datetime_to_opc,
)
from datetime import datetime, timezone


def publication(name="atlas/dcs/test/env", address="opc.udp://239.10.0.1:14840",
                pid=42, wg=100, dsw=1, fields=None) -> Publication:
    return Publication(
        name=name, address=address, publisher_id=pid, writer_group_id=wg,
        dataset_writer_id=dsw,
        fields=fields or [FieldSpec("temperature", "DOUBLE"), FieldSpec("counter", "INT32")],
    )


class TestStore:
    def test_register_get_list(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        store.register(publication())
        assert store.get("atlas/dcs/test/env").writer_group_id == 100
        assert [p.name for p in store.list()] == ["atlas/dcs/test/env"]

    def test_name_collision_refused(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        store.register(publication())
        with pytest.raises(StoreError, match="already registered"):
            store.register(publication(dsw=2))
        store.register(publication(dsw=2), replace=True)

    def test_stream_collision_refused(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        store.register(publication())
        with pytest.raises(StoreError, match="stream collision"):
            store.register(publication(name="other/name"))

    def test_validation_messages_are_precise(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        with pytest.raises(StoreError, match="invalid publication name"):
            store.register(publication(name="bad name with spaces"))
        with pytest.raises(StoreError, match="expected opc.udp"):
            store.register(publication(address="udp://x:1"))
        with pytest.raises(StoreError, match="unknown type"):
            store.register(publication(fields=[FieldSpec("x", "FLOAT128")]))
        with pytest.raises(StoreError, match="writer_group_id out of range"):
            store.register(publication(wg=70000))
        with pytest.raises(StoreError, match="duplicate field"):
            store.register(publication(fields=[FieldSpec("x", "INT32"), FieldSpec("x", "INT32")]))

    def test_persistence_round_trip(self, tmp_path):
        path = tmp_path / "reg.json"
        Store(path).register(publication(name="a/b", fields=[FieldSpec("v", "STRING")]))
        reloaded = Store(path)
        assert reloaded.get("a/b").fields[0].type == "STRING"
        assert json.loads(path.read_text())[0]["name"] == "a/b"

    def test_remove_and_renew(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        store.register(publication(name="a/b"))
        before = store.get("a/b").renewed_at
        store.renew("a/b")
        assert store.get("a/b").renewed_at >= before
        store.remove("a/b")
        assert store.get("a/b") is None
        with pytest.raises(StoreError):
            store.renew("a/b")

    def test_lookup_per_network(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        record = publication()
        record.endpoints = {"gpn": "opc.udp://gateway.cern.ch:24840"}
        store.register(record)
        stored = store.get("atlas/dcs/test/env")
        assert stored.address_for(None) == "opc.udp://239.10.0.1:14840"
        assert stored.address_for("gpn") == "opc.udp://gateway.cern.ch:24840"
        assert stored.address_for("unknown") == "opc.udp://239.10.0.1:14840"


REGISTRATION = {
    "address": "opc.udp://127.0.0.1:24841",
    "publisherId": 42,
    "publisherIdType": "UINT16",
    "writerGroupId": 100,
    "dataSetWriterId": 1,
    "description": "test publication",
    "fields": [
        {"name": "temperature", "type": "DOUBLE"},
        {"name": "label", "type": "STRING"},
    ],
}


@pytest.fixture
async def client(tmp_path):
    app = create_app(Store(tmp_path / "reg.json"))
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    yield test_client
    await test_client.close()


class TestApi:
    async def test_register_lookup_browse(self, client):
        response = await client.put("/api/publications/atlas/dcs/test/env", json=REGISTRATION)
        assert response.status == 201

        response = await client.get("/api/lookup/atlas/dcs/test/env")
        assert response.status == 200
        looked_up = await response.json()
        assert looked_up["address"] == "opc.udp://127.0.0.1:24841"
        assert [f["name"] for f in looked_up["fields"]] == ["temperature", "label"]

        response = await client.get("/api/publications")
        listed = await response.json()
        assert len(listed) == 1
        assert listed[0]["live"]["stale"] is True

        response = await client.get("/api/health")
        assert (await response.json())["publications"] == 1

    async def test_collisions_via_api(self, client):
        assert (await client.put("/api/publications/a/b", json=REGISTRATION)).status == 201
        assert (await client.put("/api/publications/a/b", json=REGISTRATION)).status == 409
        other = dict(REGISTRATION)
        assert (await client.put("/api/publications/c/d", json=other)).status == 409
        replacing = dict(REGISTRATION, replace=True)
        assert (await client.put("/api/publications/a/b", json=replacing)).status == 201

    async def test_malformed_registration_is_400(self, client):
        response = await client.put("/api/publications/a/b", json={"address": "x"})
        assert response.status == 400
        assert "malformed" in (await response.json())["error"]

    async def test_unknown_publication_is_404(self, client):
        assert (await client.get("/api/lookup/nope")).status == 404
        assert (await client.get("/api/publications/nope")).status == 404
        assert (await client.delete("/api/publications/nope")).status == 404

    async def test_live_values_flow_into_browser(self, client):
        await client.put("/api/publications/atlas/dcs/test/env", json=REGISTRATION)

        stamp = datetime_to_opc(datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc))
        message = NetworkMessage(
            publisher_id=42, writer_group_id=100, group_sequence_number=1,
            messages=[DataSetMessage(dataset_writer_id=1, sequence_number=1, fields=[
                FieldValue(BuiltinType.DOUBLE, 21.5, STATUS_UNCERTAIN, stamp),
                FieldValue(BuiltinType.STRING, "hello"),
            ])])
        sock = transport.open_send_socket("127.0.0.1")
        sock.sendto(encode_network_message(message), ("127.0.0.1", 24841))
        sock.close()
        await asyncio.sleep(0.2)

        response = await client.get("/api/publications/atlas/dcs/test/env")
        detail = await response.json()
        assert detail["live"]["stale"] is False
        assert detail["live"]["messages"] == 1
        values = {v["name"]: v for v in detail["values"]}
        assert values["temperature"]["value"] == 21.5
        assert values["temperature"]["good"] is False
        assert values["temperature"]["sourceTime"].startswith("2026-07-12T15:00")
        assert values["label"]["value"] == "hello"
        assert values["label"]["good"] is True

    async def test_ui_served(self, client):
        response = await client.get("/")
        assert response.status == 200
        assert "hypernova registry" in await response.text()
