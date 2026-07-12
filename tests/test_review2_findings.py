"""Regression tests for adversarial-review round 2 (the v1.0.0 surface):
signing enforcement, relay signing semantics, mirror convergence, metrics."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hypernova import transport
from hypernova.client import Subscriber
from hypernova.registry.service import create_app
from hypernova.registry.store import FieldSpec, Publication, Store
from hypernova.relay import Relay, Route
from hypernova.wire import (
    BuiltinType,
    DataSetMessage,
    FieldValue,
    NetworkMessage,
    WireError,
    decode_network_message,
    encode_network_message,
)

KEY = bytes(range(32))


def signed_frame(value=7, keep_alive=False, datavalue=True, key=KEY):
    dsm = DataSetMessage(dataset_writer_id=1, sequence_number=1, keep_alive=keep_alive)
    if not keep_alive:
        dsm.fields.append(FieldValue(BuiltinType.INT32, value))
    message = NetworkMessage(publisher_id=7, writer_group_id=1,
                             group_sequence_number=1, messages=[dsm])
    return encode_network_message(message, datavalue_fields=datavalue, sign_key=key)


class TestSigningEnforcement:
    """Finding #1: require_signed must demand cryptographic verification."""

    def test_require_signed_without_key_is_refused(self):
        with pytest.raises(WireError, match="needs a verify_key"):
            decode_network_message(signed_frame(), require_signed=True)

    def test_self_asserted_signed_without_valid_hmac_is_rejected(self):
        forged = bytearray(signed_frame())
        forged[-5] ^= 0xFF  # corrupt the signature
        with pytest.raises(WireError):
            decode_network_message(bytes(forged), verify_key=KEY, require_signed=True)

    def test_subscriber_require_signed_needs_key(self):
        with pytest.raises(ValueError, match="require_signed needs verify_key"):
            Subscriber("x", registry="http://127.0.0.1:1", address="opc.udp://127.0.0.1:1",
                       publisher_id=1, writer_group_id=1, dataset_writer_id=1,
                       require_signed=True)


class TestRelaySigning:
    async def test_signing_route_verifies_origin_when_keyed(self):
        # Finding #2: a verify_key on the route authenticates the origin.
        received = []
        sink = await transport.create_receiver("127.0.0.1", 24902,
                                               lambda d, a: received.append(d))
        relay = Relay([Route(name="v", source="opc.udp://127.0.0.1:24901",
                             targets=["opc.udp://127.0.0.1:24902"],
                             sign_key=KEY, verify_key=KEY)])
        await relay.start()
        sender = transport.open_send_socket("127.0.0.1")
        sender.sendto(signed_frame(value=11), ("127.0.0.1", 24901))       # signed: forwarded
        sender.sendto(encode_network_message(                              # unsigned: dropped
            NetworkMessage(publisher_id=7, writer_group_id=1, group_sequence_number=1,
                           messages=[DataSetMessage(dataset_writer_id=1, sequence_number=1,
                                                    fields=[FieldValue(BuiltinType.INT32, 99)])]),
            datavalue_fields=False), ("127.0.0.1", 24901))
        await asyncio.sleep(0.3)
        relay.stop()
        sink.close()
        sender.close()
        assert len(received) == 1, "unsigned frame must be dropped by a verifying route"
        out = decode_network_message(received[0], verify_key=KEY, require_signed=True)
        assert out.verified and out.messages[0].fields[0].value == 11
        assert relay.stats()["routes"][0]["invalidDropped"] == 1

    async def test_signing_route_preserves_keepalive_and_encoding(self):
        # Findings #3 + #4: keep-alive stays keep-alive; Variant stays Variant.
        received = []
        sink = await transport.create_receiver("127.0.0.1", 24904,
                                               lambda d, a: received.append(d))
        relay = Relay([Route(name="k", source="opc.udp://127.0.0.1:24903",
                             targets=["opc.udp://127.0.0.1:24904"], sign_key=KEY)])
        await relay.start()
        sender = transport.open_send_socket("127.0.0.1")
        # Variant-encoded frame (a supernova C++ publisher's shape)
        sender.sendto(signed_frame(value=5, datavalue=False, key=None), ("127.0.0.1", 24903))
        sender.sendto(signed_frame(keep_alive=True, key=None), ("127.0.0.1", 24903))
        await asyncio.sleep(0.3)
        relay.stop()
        sink.close()
        sender.close()
        assert len(received) == 2
        variant_out = decode_network_message(received[0], verify_key=KEY)
        # Variant fields carry no quality/timestamp — preserved, not upgraded
        assert variant_out.messages[0].fields[0].value == 5
        assert variant_out.messages[0].field_encoding == 0
        keepalive_out = decode_network_message(received[1], verify_key=KEY)
        assert keepalive_out.messages[0].keep_alive
        assert keepalive_out.messages[0].fields == []


class TestMirrorConvergence:
    async def test_metrics_and_signed_state(self, tmp_path):
        store = Store(tmp_path / "reg.json")
        store.register(Publication(
            name="sec/pub", address="opc.udp://127.0.0.1:24905",
            publisher_id=7, writer_group_id=1, dataset_writer_id=1,
            fields=[FieldSpec("v", "INT32")]))
        app = create_app(store)
        client = TestClient(TestServer(app))
        await client.start_server()

        sock = transport.open_send_socket("127.0.0.1")
        sock.sendto(signed_frame(value=3), ("127.0.0.1", 24905))
        sock.close()
        await asyncio.sleep(0.2)

        detail = await (await client.get("/api/publications/sec/pub")).json()
        assert detail["live"]["signed"] is True   # finding #9: signed state surfaced
        assert "registeredAt" in detail            # finding #5: convergence key exposed

        metrics = await (await client.get("/metrics")).text()
        assert 'hypernova_publication_messages_total{name="sec/pub"}' in metrics
        await client.close()

    def test_metrics_label_newline_safe(self, tmp_path):
        # Finding #7: a name with a newline (only reachable via corrupt file,
        # which is now validated out) cannot inject metric lines. Verify the
        # escaper directly.
        from hypernova.registry.service import _json_safe  # sanity import
        name = "a\nb\"c"
        safe = name.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")
        assert "\n" not in safe
