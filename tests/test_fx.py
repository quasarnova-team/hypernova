"""The fx ConnectionManager's own logic — everything that does not need a
live OPC UA server (the server loop is covered by the supernova bench e2e):
CLI wiring, the type map, and connect_pair's orchestration semantics
(publisher first, coordinates handed to the subscriber, rollback on failure).
"""

from __future__ import annotations

import pytest

import hypernova.fx as fx
from hypernova.cli import main as cli_main


def test_cli_fx_subcommands_are_wired(capsys):
    for argv in (["fx", "--help"],
                 ["fx", "connect", "--help"],
                 ["fx", "status", "--help"],
                 ["fx", "close", "--help"]):
        with pytest.raises(SystemExit) as raised:
            cli_main(argv)
        assert raised.value.code == 0


def test_type_map_covers_the_wire_scalars():
    expected = {"Boolean", "SByte", "Byte", "Int16", "UInt16", "Int32", "UInt32",
                "Int64", "UInt64", "Float", "Double", "String", "DateTime"}
    assert expected == set(fx._UA_TO_HYPERNOVA_TYPE)
    assert all(v == v.upper() for v in fx._UA_TO_HYPERNOVA_TYPE.values())


async def test_connect_pair_hands_publisher_coordinates_to_subscriber(monkeypatch):
    calls = []

    async def fake_establish(url, *, component, entity, role, dataset, address,
                             interval=None, peer=None, name=None, ttl=None):
        calls.append({"url": url, "role": role, "peer": peer, "dataset": dataset})
        if role == "publisher":
            return "cep-1", {"status": "Operational", "address": address,
                             "coordinates": {"publisherIdType": "UInt16", "publisherId": 91,
                                             "writerGroupId": 200, "dataSetWriterId": 1}}
        return "cep-1", {"status": "Operational", "address": address}

    monkeypatch.setattr(fx, "establish", fake_establish)

    result = await fx.connect_pair(
        publisher_url="opc.tcp://a:4841", publisher_component="CellA",
        publisher_entity="control", publisher_dataset="env",
        subscriber_url="opc.tcp://b:4841", subscriber_component="CellB",
        subscriber_entity="control", subscriber_dataset="mirror",
        address="opc.udp://239.192.0.31:14860")

    assert [c["role"] for c in calls] == ["publisher", "subscriber"]
    assert calls[1]["peer"] == {"publisherIdType": "UInt16", "publisherId": 91,
                                "writerGroupId": 200, "dataSetWriterId": 1}
    assert result["publisher"]["connectionId"] == "cep-1"
    assert result["subscriber"]["connectionId"] == "cep-1"
    assert result["coordinates"]["publisherId"] == 91


async def test_connect_pair_rolls_back_publisher_when_subscriber_fails(monkeypatch):
    closed = []

    async def fake_establish(url, *, component, entity, role, dataset, address,
                             interval=None, peer=None, name=None, ttl=None):
        if role == "publisher":
            return "cep-7", {"status": "Operational", "address": address,
                             "coordinates": {"publisherId": 91, "writerGroupId": 200,
                                             "dataSetWriterId": 1}}
        raise RuntimeError("subscriber refused")

    async def fake_close(url, *, component, connection_id):
        closed.append((url, connection_id))
        return {"status": "Initial"}

    monkeypatch.setattr(fx, "establish", fake_establish)
    monkeypatch.setattr(fx, "close", fake_close)

    with pytest.raises(RuntimeError, match="subscriber refused"):
        await fx.connect_pair(
            publisher_url="opc.tcp://a:4841", publisher_component="CellA",
            publisher_entity="control", publisher_dataset="env",
            subscriber_url="opc.tcp://b:4841", subscriber_component="CellB",
            subscriber_entity="control", subscriber_dataset="mirror",
            address="opc.udp://239.192.0.31:14860")

    assert closed == [("opc.tcp://a:4841", "cep-7")]


async def test_connect_pair_refuses_publisher_without_coordinates(monkeypatch):
    async def fake_establish(url, *, component, entity, role, dataset, address,
                             interval=None, peer=None, name=None, ttl=None):
        return "cep-1", {"status": "Operational"}

    monkeypatch.setattr(fx, "establish", fake_establish)

    with pytest.raises(SystemExit, match="no coordinates"):
        await fx.connect_pair(
            publisher_url="opc.tcp://a:4841", publisher_component="CellA",
            publisher_entity="control", publisher_dataset="env",
            subscriber_url="opc.tcp://b:4841", subscriber_component="CellB",
            subscriber_entity="control", subscriber_dataset="mirror",
            address="opc.udp://239.192.0.31:14860")
