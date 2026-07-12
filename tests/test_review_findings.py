"""Regression tests for the adversarial-review findings: every scenario here
once bricked, crashed or corrupted something. They must stay failing-proof."""

import json
from datetime import datetime, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hypernova.client import _cache_path
from hypernova.registry.service import create_app
from hypernova.registry.store import FieldSpec, Publication, Store, StoreError
from hypernova.transport import parse_address
from hypernova.wire import (
    BuiltinType,
    DataSetMessage,
    FieldValue,
    NetworkMessage,
    datetime_to_opc,
    encode_network_message,
    opc_to_datetime,
)
from hypernova import transport


def publication(**overrides):
    base = dict(name="a/b", address="opc.udp://239.10.0.1:14840",
                publisher_id=1, writer_group_id=1, dataset_writer_id=1,
                fields=[FieldSpec("v", "INT32")])
    base.update(overrides)
    return Publication(**base)


class TestFinding1PortValidation:
    @pytest.mark.parametrize("address", [
        "opc.udp://239.0.0.9:70000",
        "opc.udp://239.0.0.9:0",
        "opc.udp://239.0.0.9:99999",
    ])
    def test_bad_ports_refused_and_never_persisted(self, tmp_path, address):
        path = tmp_path / "reg.json"
        store = Store(path)
        with pytest.raises(StoreError):
            store.register(publication(address=address))
        assert not path.exists() or json.loads(path.read_text()) == []
        assert len(Store(path)) == 0

    def test_bad_endpoint_ports_also_refused(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        record = publication()
        record.endpoints = {"gpn": "opc.udp://10.0.0.1:70000"}
        with pytest.raises(StoreError, match="gpn"):
            store.register(record)


class TestFinding2SyncIsolation:
    async def test_one_bad_endpoint_never_kills_service_or_others(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        store.register(publication(name="ok/pub", address="opc.udp://127.0.0.1:24861",
                                   dataset_writer_id=1))
        store.register(publication(name="clash/pub", address="opc.udp://127.0.0.9:24861",
                                   dataset_writer_id=2))
        app = create_app(store)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        health = await (await client.get("/api/health")).json()
        assert health["publications"] == 2
        assert len(health["endpointErrors"]) == 1
        await client.close()


class TestFinding3CorruptStore:
    def test_truncated_file_quarantined_and_service_starts(self, tmp_path):
        path = tmp_path / "reg.json"
        path.write_text('[{"name": "trunc')
        store = Store(path)
        assert len(store) == 0
        assert store.load_error and "starting empty" in store.load_error
        assert (tmp_path / "reg.json.corrupt").exists()

    def test_forward_schema_tolerated(self, tmp_path):
        path = tmp_path / "reg.json"
        store = Store(path)
        store.register(publication())
        data = json.loads(path.read_text())
        data[0]["NEW_FIELD_FROM_v2"] = {"future": True}
        path.write_text(json.dumps(data))
        reloaded = Store(path)
        assert reloaded.load_error is None
        assert reloaded.get("a/b").publisher_id == 1


class TestFinding4TimestampOverflow:
    def test_hostile_source_timestamp_never_raises(self):
        for ticks in (0x7FFFFFFFFFFFFFFF, -0x8000000000000000, 0, 1):
            moment = opc_to_datetime(ticks)
            assert moment.tzinfo is not None
        fv = FieldValue(BuiltinType.INT32, 1, source_timestamp=0x7FFFFFFFFFFFFFFF)
        assert fv.source_datetime.year == 9999


class TestFinding5NaNJson:
    async def test_nan_payload_still_serves_valid_json(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        store.register(publication(name="nan/pub", address="opc.udp://127.0.0.1:24862",
                                   fields=[FieldSpec("x", "DOUBLE"),
                                           FieldSpec("xs", "DOUBLE[]")]))
        app = create_app(store)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()

        wire = encode_network_message(NetworkMessage(
            publisher_id=1, writer_group_id=1, group_sequence_number=1,
            messages=[DataSetMessage(dataset_writer_id=1, sequence_number=1, fields=[
                FieldValue(BuiltinType.DOUBLE, float("nan")),
                FieldValue(BuiltinType.DOUBLE, [1.0, float("inf")]),
            ])]))
        sock = transport.open_send_socket("127.0.0.1")
        sock.sendto(wire, ("127.0.0.1", 24862))
        sock.close()
        import asyncio
        await asyncio.sleep(0.2)

        raw = await (await client.get("/api/publications/nan/pub")).text()
        detail = json.loads(raw)  # strict parse: would fail on a bare NaN token
        values = {v["name"]: v["value"] for v in detail["values"]}
        assert values["x"] == "nan"
        assert values["xs"] == [1.0, "inf"]
        await client.close()


class TestFinding7TickExactness:
    def test_datetime_conversion_is_tick_exact(self):
        moment = datetime(2026, 7, 12, 15, 30, 12, 345678, tzinfo=timezone.utc)
        ticks = datetime_to_opc(moment)
        assert datetime_to_opc(opc_to_datetime(ticks)) == ticks
        assert opc_to_datetime(ticks) == moment


class TestFinding8MalformedBodies:
    async def test_non_json_and_non_object_bodies_are_400(self, tmp_path):
        app = create_app(Store(tmp_path / "reg.json"), listen=False)
        client = TestClient(TestServer(app))
        await client.start_server()
        response = await client.put("/api/publications/a/b", data=b"not json at all")
        assert response.status == 400
        assert "JSON" in (await response.json())["error"]
        response = await client.put("/api/publications/a/b", json=[1, 2, 3])
        assert response.status == 400
        await client.close()


class TestFinding9CachePath:
    def test_cache_filenames_never_escape_the_cache_dir(self):
        for name in ("a/b", "..", "a/../../b", "win\\dows", "x:y", "nul?"):
            path = _cache_path(name)
            assert path.parent == _cache_path("safe").parent
            assert "/" not in path.name and "\\" not in path.name


class TestFinding10AddressGrammar:
    def test_ipv6_rejected_with_clear_message(self):
        with pytest.raises(ValueError, match="IPv6"):
            parse_address("opc.udp://[::1]:4840")

    def test_hostname_interface_rejected_with_clear_message(self):
        with pytest.raises(ValueError, match="IPv4 address"):
            transport.open_send_socket("239.0.0.5", interface="not-an-ip")
