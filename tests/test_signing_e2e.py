"""Signing above the codec: signed pub/sub by name, key files, and the
boundary relay's signing mode (unsigned inside -> signed across)."""

import asyncio

import pytest

from hypernova import transport
from hypernova.client import Publisher, Subscriber
from hypernova.keys import load_key
from hypernova.relay import Relay, Route
from hypernova.wire import (
    BuiltinType,
    DataSetMessage,
    FieldValue,
    NetworkMessage,
    decode_network_message,
    encode_network_message,
)

KEY = bytes(range(32))


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "stream.key"
    path.write_text(KEY.hex() + "\n")
    return path


def test_load_key(key_file, tmp_path):
    assert load_key(key_file) == KEY
    bad = tmp_path / "bad.key"
    bad.write_text("not hex!")
    with pytest.raises(ValueError, match="not hex"):
        load_key(bad)
    short = tmp_path / "short.key"
    short.write_text("aabb")
    with pytest.raises(ValueError, match=">= 16"):
        load_key(short)
    with pytest.raises(ValueError, match="cannot read"):
        load_key(tmp_path / "missing.key")


async def test_signed_pub_sub_and_forgery_rejection():
    coords = dict(address="opc.udp://127.0.0.1:24880", publisher_id=9,
                  writer_group_id=4, dataset_writer_id=4)
    publisher = Publisher("sec/stream", fields={"v": "INT32"}, registry="http://127.0.0.1:1",
                          register=False, sign_key=KEY, **coords)
    forger = Publisher("sec/forged", fields={"v": "INT32"}, registry="http://127.0.0.1:1",
                       register=False, **coords)  # right ids, no key
    subscriber = Subscriber("sec/stream", registry="http://127.0.0.1:1",
                            field_names=["v"], verify_key=KEY, **coords)
    with subscriber:
        await asyncio.sleep(0.2)
        forger.send(v=666)
        publisher.send(v=1)
        update = subscriber.get(timeout=5.0)
        assert update.values["v"].value == 1, "forged unsigned frame was accepted"
    assert subscriber.undecodable_datagrams >= 1
    publisher.close()
    forger.close()


async def test_relay_signing_mode_signs_at_the_boundary():
    inside = NetworkMessage(
        publisher_id=7, writer_group_id=2, group_sequence_number=3,
        messages=[DataSetMessage(dataset_writer_id=1, sequence_number=3,
                                 fields=[FieldValue(BuiltinType.DOUBLE, 2.5)])])
    received: list[bytes] = []
    sink = await transport.create_receiver("127.0.0.1", 24882,
                                           lambda d, a: received.append(d))
    relay = Relay([Route(name="sec", source="opc.udp://127.0.0.1:24881",
                         targets=["opc.udp://127.0.0.1:24882"], sign_key=KEY)])
    await relay.start()
    sender = transport.open_send_socket("127.0.0.1")
    sender.sendto(encode_network_message(inside, datavalue_fields=False), ("127.0.0.1", 24881))
    sender.sendto(b"garbage not uadp", ("127.0.0.1", 24881))
    await asyncio.sleep(0.3)
    relay.stop()
    sink.close()
    sender.close()

    assert len(received) == 1, "garbage must be dropped by a signing route"
    decoded = decode_network_message(received[0], verify_key=KEY, require_signed=True)
    assert decoded.verified is True
    assert decoded.messages[0].fields[0].value == 2.5
    assert relay.stats()["routes"][0]["invalidDropped"] == 1
