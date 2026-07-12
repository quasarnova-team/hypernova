"""Relay: raw datagram forwarding between two loopback 'networks', counters,
health endpoint, config validation."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hypernova import transport
from hypernova.relay import Relay, Route, create_health_app, load_config
from hypernova.wire import (
    BuiltinType,
    DataSetMessage,
    FieldValue,
    NetworkMessage,
    decode_network_message,
    encode_network_message,
)


def sample_wire(value: int) -> bytes:
    return encode_network_message(NetworkMessage(
        publisher_id=7, writer_group_id=1, group_sequence_number=value,
        messages=[DataSetMessage(dataset_writer_id=1, sequence_number=value,
                                 fields=[FieldValue(BuiltinType.INT32, value)])]))


async def test_forwards_datagrams_byte_identically():
    received: list[bytes] = []
    sink = await transport.create_receiver(
        "127.0.0.1", 24871, lambda data, addr: received.append(data))

    relay = Relay([Route(name="t", source="opc.udp://127.0.0.1:24870",
                         targets=["opc.udp://127.0.0.1:24871"])])
    await relay.start()

    sender = transport.open_send_socket("127.0.0.1")
    for value in range(3):
        sender.sendto(sample_wire(value), ("127.0.0.1", 24870))
        await asyncio.sleep(0.05)
    sender.close()
    await asyncio.sleep(0.2)

    relay.stop()
    sink.close()

    assert len(received) == 3
    for index, wire in enumerate(received):
        assert wire == sample_wire(index)
        assert decode_network_message(wire).messages[0].fields[0].value == index

    stats = relay.stats()["routes"][0]
    assert stats["datagrams"] == 3
    assert stats["bytes"] == sum(len(sample_wire(v)) for v in range(3))


async def test_fan_out_to_multiple_targets():
    received_a: list[bytes] = []
    received_b: list[bytes] = []
    sink_a = await transport.create_receiver("127.0.0.1", 24873,
                                             lambda d, a: received_a.append(d))
    sink_b = await transport.create_receiver("127.0.0.1", 24874,
                                             lambda d, a: received_b.append(d))
    relay = Relay([Route(name="fan", source="opc.udp://127.0.0.1:24872",
                         targets=["opc.udp://127.0.0.1:24873",
                                  "opc.udp://127.0.0.1:24874"])])
    await relay.start()
    sender = transport.open_send_socket("127.0.0.1")
    sender.sendto(sample_wire(9), ("127.0.0.1", 24872))
    sender.close()
    await asyncio.sleep(0.2)
    relay.stop()
    sink_a.close()
    sink_b.close()
    assert received_a == [sample_wire(9)]
    assert received_b == [sample_wire(9)]


async def test_health_endpoint():
    relay = Relay([Route(name="h", source="opc.udp://127.0.0.1:24875",
                         targets=["opc.udp://127.0.0.1:24876"])])
    await relay.start()
    client = TestClient(TestServer(create_health_app(relay)))
    await client.start_server()
    payload = await (await client.get("/api/health")).json()
    assert payload["service"] == "hypernova-relay"
    assert payload["routes"][0]["name"] == "h"
    assert payload["routes"][0]["datagrams"] == 0
    await client.close()
    relay.stop()


def test_config_validation(tmp_path):
    good = tmp_path / "relay.json"
    good.write_text(json.dumps({
        "routes": [{"name": "x", "from": "opc.udp://239.1.1.1:4840",
                    "to": ["opc.udp://10.0.0.1:4840"], "ttl": 4}],
        "health_port": 4860,
    }))
    routes, health_port = load_config(good)
    assert routes[0].ttl == 4
    assert health_port == 4860

    empty = tmp_path / "empty.json"
    empty.write_text('{"routes": []}')
    with pytest.raises(ValueError, match="no routes"):
        load_config(empty)

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"routes": [{"from": "opc.udp://1.2.3.4:1", "to": []}]}))
    with pytest.raises(ValueError, match="no targets"):
        load_config(bad)
