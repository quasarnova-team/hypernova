"""Codec behaviour beyond the golden vectors: DataValue quality/timestamps,
truncation safety, foreign-header tolerance, rejection diagnostics."""

from datetime import datetime, timezone

import pytest

from hypernova.wire import (
    STATUS_BAD,
    STATUS_GOOD,
    STATUS_UNCERTAIN,
    BuiltinType,
    DataSetMessage,
    FieldValue,
    NetworkMessage,
    PublisherIdType,
    WireError,
    datetime_to_opc,
    decode_network_message,
    encode_network_message,
    opc_to_datetime,
)


def make_message(fields, *, pid=7, pid_type=PublisherIdType.UINT16, wg=1, seq=1):
    dsm = DataSetMessage(dataset_writer_id=5, sequence_number=seq, fields=fields)
    return NetworkMessage(
        publisher_id=pid, publisher_id_type=pid_type,
        writer_group_id=wg, group_sequence_number=seq, messages=[dsm],
    )


def test_datavalue_round_trip_preserves_quality_and_time():
    stamp = datetime_to_opc(datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc))
    fields = [
        FieldValue(BuiltinType.DOUBLE, 21.5, STATUS_GOOD, stamp),
        FieldValue(BuiltinType.INT32, -4, STATUS_BAD, stamp),
        FieldValue(BuiltinType.STRING, "warm", STATUS_UNCERTAIN, None),
        FieldValue(BuiltinType.NULL, None, STATUS_BAD, None),
    ]
    wire = encode_network_message(make_message(fields))
    decoded = decode_network_message(wire)
    got = decoded.messages[0].fields
    assert [(f.type, f.value, f.status, f.source_timestamp) for f in got] == [
        (BuiltinType.DOUBLE, 21.5, STATUS_GOOD, stamp),
        (BuiltinType.INT32, -4, STATUS_BAD, stamp),
        (BuiltinType.STRING, "warm", STATUS_UNCERTAIN, None),
        (BuiltinType.NULL, None, STATUS_BAD, None),
    ]
    assert got[0].is_good and not got[1].is_good and not got[2].is_good
    assert got[0].source_datetime == datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)


def test_datetime_conversion_round_trip():
    moment = datetime(2026, 7, 12, 12, 0, 0, 500000, tzinfo=timezone.utc)
    assert opc_to_datetime(datetime_to_opc(moment)) == moment


def test_variant_encoding_omits_quality():
    fields = [FieldValue(BuiltinType.INT32, 3, STATUS_BAD, 12345)]
    wire = encode_network_message(make_message(fields), datavalue_fields=False)
    decoded = decode_network_message(wire)
    field = decoded.messages[0].fields[0]
    assert field.value == 3
    assert field.status == STATUS_GOOD
    assert field.source_timestamp is None


def test_truncation_never_crashes_and_always_raises():
    fields = [
        FieldValue(BuiltinType.DOUBLE, 1.5, STATUS_UNCERTAIN, 77),
        FieldValue(BuiltinType.STRING, "truncation"),
    ]
    message = make_message(fields, pid=12345678901234, pid_type=PublisherIdType.UINT64)
    message.messages.append(DataSetMessage(dataset_writer_id=6, sequence_number=4,
                                           fields=list(fields)))
    wire = encode_network_message(message)
    assert decode_network_message(wire).messages[1].fields[1].value == "truncation"
    for cut in range(len(wire)):
        with pytest.raises(WireError):
            decode_network_message(wire[:cut])


def test_bad_values_raise_wire_error():
    with pytest.raises(WireError):
        encode_network_message(NetworkMessage(messages=[]))
    with pytest.raises(WireError):
        encode_network_message(make_message([FieldValue(BuiltinType.BYTE, 300)]))
    with pytest.raises(WireError):
        encode_network_message(make_message([FieldValue(BuiltinType.INT32, 5)],
                                            pid=70000, pid_type=PublisherIdType.UINT16))


def test_rejects_encrypted_and_chunked():
    encrypted = bytes([0xF1, 0x01 | 0x10, 0x2A, 0x00,       # flags, ext1(sec), pid
                       0x01, 0x64, 0x00,                     # group header: wgid 100
                       0x01, 0x01, 0x00,                     # payload count 1, writer 1
                       0x02])                                # SecurityHeader: encrypted
    with pytest.raises(WireError, match="encrypted"):
        decode_network_message(encrypted)
    chunked = bytes([0xF1, 0x01 | 0x80, 0x01, 0x2A, 0x00])
    with pytest.raises(WireError, match="chunked"):
        decode_network_message(chunked)
    with pytest.raises(WireError, match="version"):
        decode_network_message(bytes([0x72]))


def test_keepalive_and_foreign_headers_tolerated():
    wire = bytes([
        0x71,             # version 1, pid, group header, payload header
        0x07,             # pid (Byte)
        0x01, 0x64, 0x00, # group header: writer group id 100
        0x01,             # payload count 1
        0x01, 0x00,       # writer id 1
        0x81,             # DSM flags1: valid + flags2
        0x03,             # DSM flags2: keep-alive
    ])
    decoded = decode_network_message(wire)
    assert decoded.messages[0].keep_alive
    assert decoded.messages[0].fields == []


def test_array_round_trip_all_encodings():
    fields = [
        FieldValue(BuiltinType.INT32, [1, -2, 3]),
        FieldValue(BuiltinType.DOUBLE, [0.5, -1.25]),
        FieldValue(BuiltinType.STRING, ["alpha", "", "γάμμα"]),
        FieldValue(BuiltinType.BOOLEAN, [True, False, True]),
        FieldValue(BuiltinType.UINT64, []),
    ]
    for datavalue in (False, True):
        wire = encode_network_message(make_message(list(fields)), datavalue_fields=datavalue)
        decoded = decode_network_message(wire).messages[0].fields
        assert [(f.type, f.value) for f in decoded] == [(f.type, list(f.value)) for f in fields]


def test_array_wire_format_is_spec_exact():
    wire = encode_network_message(
        make_message([FieldValue(BuiltinType.INT32, [7, 8])],
                     pid=9, pid_type=PublisherIdType.BYTE, wg=1, seq=1),
        datavalue_fields=False)
    body = wire[-(1 + 4 + 8):]
    assert body[0] == 0x86            # Int32 (6) | array-of-values (0x80)
    assert body[1:5] == b"\x02\x00\x00\x00"
    assert body[5:9] == b"\x07\x00\x00\x00"
    assert body[9:13] == b"\x08\x00\x00\x00"


def test_array_truncation_and_bounds():
    wire = encode_network_message(
        make_message([FieldValue(BuiltinType.INT32, list(range(10)))]))
    for cut in range(len(wire)):
        with pytest.raises(WireError):
            decode_network_message(wire[:cut])
    absurd = bytearray(encode_network_message(
        make_message([FieldValue(BuiltinType.INT32, [1])], pid_type=PublisherIdType.BYTE)))
    with pytest.raises(WireError):
        decode_network_message(bytes(absurd)[:14] + b"\xff\xff\xff\x7f" + bytes(absurd)[18:])


class TestSigning:
    KEY = b"0123456789abcdef0123456789abcdef"

    def wire(self, **kw):
        return encode_network_message(
            make_message([FieldValue(BuiltinType.INT32, 7),
                          FieldValue(BuiltinType.STRING, "signed")]), **kw)

    def test_sign_verify_round_trip(self):
        wire = self.wire(sign_key=self.KEY, security_token_id=5)
        decoded = decode_network_message(wire, verify_key=self.KEY)
        assert decoded.signed and decoded.verified is True
        assert decoded.security_token_id == 5
        assert decoded.messages[0].fields[0].value == 7

    def test_unverified_parse_without_key(self):
        decoded = decode_network_message(self.wire(sign_key=self.KEY))
        assert decoded.signed and decoded.verified is None
        assert decoded.messages[0].fields[1].value == "signed"

    def test_wrong_key_rejected(self):
        with pytest.raises(WireError, match="signature verification failed"):
            decode_network_message(self.wire(sign_key=self.KEY), verify_key=b"x" * 32)

    def test_every_single_bit_flip_detected(self):
        wire = bytearray(self.wire(sign_key=self.KEY))
        for index in range(len(wire)):
            wire[index] ^= 0x01
            try:
                decode_network_message(bytes(wire), verify_key=self.KEY)
                assert False, f"tamper at byte {index} went undetected"
            except WireError:
                pass
            wire[index] ^= 0x01

    def test_require_signed_rejects_unsigned(self):
        with pytest.raises(WireError, match="unsigned frame rejected"):
            decode_network_message(self.wire(), require_signed=True)
        decoded = decode_network_message(self.wire(sign_key=self.KEY),
                                         verify_key=self.KEY, require_signed=True)
        assert decoded.verified

    def test_signed_truncation_fuzz(self):
        wire = self.wire(sign_key=self.KEY)
        for cut in range(len(wire)):
            with pytest.raises(WireError):
                decode_network_message(wire[:cut], verify_key=self.KEY)

    def test_short_key_refused(self):
        with pytest.raises(WireError, match="at least 16 bytes"):
            self.wire(sign_key=b"short")

    def test_unsigned_frames_unchanged_by_feature(self):
        plain = self.wire()
        decoded = decode_network_message(plain)
        assert not decoded.signed and decoded.verified is None
