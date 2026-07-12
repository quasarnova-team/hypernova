"""Cross-implementation parity: vectors in golden/vectors.json were produced
by supernova's C++ PubSubWire encoder (see golden/gen_golden.cpp). The Python
codec must decode them to the exact values AND re-encode them byte-for-byte
identically."""

import json
from pathlib import Path

import pytest

from hypernova.wire import (
    BuiltinType,
    PublisherIdType,
    decode_network_message,
    encode_network_message,
)

VECTORS = {
    entry["name"]: bytes.fromhex(entry["hex"])
    for entry in json.loads((Path(__file__).parent / "golden" / "vectors.json").read_text())
}


def test_vectors_present():
    assert set(VECTORS) == {
        "single_int32", "all_scalars", "three_writers_byte_pid", "uint64_pid_utf8",
    }


def test_single_int32_decodes():
    message = decode_network_message(VECTORS["single_int32"])
    assert message.publisher_id == 2234
    assert message.publisher_id_type == PublisherIdType.UINT16
    assert message.writer_group_id == 100
    assert message.group_sequence_number == 1
    (dsm,) = message.messages
    assert dsm.dataset_writer_id == 62541
    assert dsm.sequence_number == 1
    (field,) = dsm.fields
    assert field.type == BuiltinType.INT32
    assert field.value == 7


def test_all_scalars_decode():
    message = decode_network_message(VECTORS["all_scalars"])
    (dsm,) = message.messages
    expected = [
        (BuiltinType.NULL, None),
        (BuiltinType.BOOLEAN, True),
        (BuiltinType.SBYTE, -5),
        (BuiltinType.BYTE, 200),
        (BuiltinType.INT16, -30000),
        (BuiltinType.UINT16, 60000),
        (BuiltinType.INT32, -2000000000),
        (BuiltinType.UINT32, 4000000000),
        (BuiltinType.INT64, -9000000000000000000),
        (BuiltinType.UINT64, 18000000000000000000),
        (BuiltinType.FLOAT, 3.5),
        (BuiltinType.DOUBLE, -2.25e-10),
        (BuiltinType.STRING, "supernova"),
        (BuiltinType.STRING, ""),
        (BuiltinType.DATETIME, 133774531200000000),
    ]
    assert [(f.type, f.value) for f in dsm.fields] == expected


def test_three_writers_byte_pid():
    message = decode_network_message(VECTORS["three_writers_byte_pid"])
    assert message.publisher_id == 9
    assert message.publisher_id_type == PublisherIdType.BYTE
    assert message.writer_group_id == 300
    assert [dsm.dataset_writer_id for dsm in message.messages] == [1000, 1001, 1002]
    for index, dsm in enumerate(message.messages):
        assert dsm.sequence_number == index
        assert dsm.fields[0].value == index * 11
        assert dsm.fields[1].value == "x" * (index + 1)


def test_uint64_pid_utf8():
    message = decode_network_message(VECTORS["uint64_pid_utf8"])
    assert message.publisher_id == 12345678901234
    assert message.publisher_id_type == PublisherIdType.UINT64
    (dsm,) = message.messages
    assert dsm.sequence_number is None
    assert dsm.fields[0].value == 21.75
    assert dsm.fields[1].value == "boundary ✓ utf8"


@pytest.mark.parametrize("name", sorted(VECTORS))
def test_reencode_byte_identical(name):
    original = VECTORS[name]
    decoded = decode_network_message(original)
    reencoded = encode_network_message(decoded, datavalue_fields=False)
    assert reencoded == original, f"{name}: python encoder diverges from C++ encoder"
