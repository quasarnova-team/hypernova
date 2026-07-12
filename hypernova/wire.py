"""OPC UA Part 14 UADP wire codec — the profile spoken by supernova's C++
PubSub engine and open62541: NetworkMessage version 1, publisher id, group
header (writer group id + sequence number), payload header, data-key-frame
DataSetMessages with Variant or DataValue field encoding.

hypernova-native publishers use DataValue field encoding so every field
carries (value, status code, source timestamp); Variant encoding is emitted
on request and always accepted on reception.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum

__all__ = [
    "BuiltinType",
    "PublisherIdType",
    "FieldValue",
    "DataSetMessage",
    "NetworkMessage",
    "WireError",
    "encode_network_message",
    "decode_network_message",
    "datetime_to_opc",
    "opc_to_datetime",
    "STATUS_GOOD",
    "STATUS_BAD",
    "STATUS_UNCERTAIN",
]

STATUS_GOOD = 0x00000000
STATUS_UNCERTAIN = 0x40000000
STATUS_BAD = 0x80000000

_OPC_EPOCH_OFFSET_100NS = 116444736000000000  # 1601-01-01 -> 1970-01-01


class WireError(ValueError):
    """Raised when a datagram cannot be encoded or decoded."""


class BuiltinType(IntEnum):
    NULL = 0
    BOOLEAN = 1
    SBYTE = 2
    BYTE = 3
    INT16 = 4
    UINT16 = 5
    INT32 = 6
    UINT32 = 7
    INT64 = 8
    UINT64 = 9
    FLOAT = 10
    DOUBLE = 11
    STRING = 12
    DATETIME = 13


class PublisherIdType(IntEnum):
    BYTE = 0
    UINT16 = 1
    UINT32 = 2
    UINT64 = 3


_SCALAR_STRUCT = {
    BuiltinType.BOOLEAN: struct.Struct("<B"),
    BuiltinType.SBYTE: struct.Struct("<b"),
    BuiltinType.BYTE: struct.Struct("<B"),
    BuiltinType.INT16: struct.Struct("<h"),
    BuiltinType.UINT16: struct.Struct("<H"),
    BuiltinType.INT32: struct.Struct("<i"),
    BuiltinType.UINT32: struct.Struct("<I"),
    BuiltinType.INT64: struct.Struct("<q"),
    BuiltinType.UINT64: struct.Struct("<Q"),
    BuiltinType.FLOAT: struct.Struct("<f"),
    BuiltinType.DOUBLE: struct.Struct("<d"),
    BuiltinType.DATETIME: struct.Struct("<q"),
}

_PID_STRUCT = {
    PublisherIdType.BYTE: struct.Struct("<B"),
    PublisherIdType.UINT16: struct.Struct("<H"),
    PublisherIdType.UINT32: struct.Struct("<I"),
    PublisherIdType.UINT64: struct.Struct("<Q"),
}

# NetworkMessage header flags (byte 1)
_UADP_VERSION = 1
_NM_PUBLISHER_ID = 0x10
_NM_GROUP_HEADER = 0x20
_NM_PAYLOAD_HEADER = 0x40
_NM_EXTENDED_FLAGS_1 = 0x80

# ExtendedFlags1
_EXT1_PID_TYPE_MASK = 0x07
_EXT1_DATASET_CLASS_ID = 0x08
_EXT1_SECURITY = 0x10
_EXT1_TIMESTAMP = 0x20
_EXT1_PICOSECONDS = 0x40
_EXT1_EXTENDED_FLAGS_2 = 0x80

# ExtendedFlags2
_EXT2_CHUNK = 0x01
_EXT2_PROMOTED_FIELDS = 0x02
_EXT2_NM_TYPE_MASK = 0x1C

# GroupHeader flags
_GH_WRITER_GROUP_ID = 0x01
_GH_GROUP_VERSION = 0x02
_GH_NM_NUMBER = 0x04
_GH_SEQUENCE_NUMBER = 0x08

# DataSetFlags1
_DSM_VALID = 0x01
_DSM_FIELD_ENCODING_MASK = 0x06
_DSM_SEQUENCE_NUMBER = 0x08
_DSM_STATUS = 0x10
_DSM_CFG_MAJOR = 0x20
_DSM_CFG_MINOR = 0x40
_DSM_FLAGS_2 = 0x80

_FIELD_ENCODING_VARIANT = 0
_FIELD_ENCODING_RAW = 1
_FIELD_ENCODING_DATAVALUE = 2

# DataSetFlags2
_DSM2_TYPE_MASK = 0x0F
_DSM2_TIMESTAMP = 0x10
_DSM2_PICOSECONDS = 0x20

_DSM_TYPE_KEY_FRAME = 0
_DSM_TYPE_KEEP_ALIVE = 3

# DataValue encoding mask
_DV_VALUE = 0x01
_DV_STATUS = 0x02
_DV_SOURCE_TS = 0x04
_DV_SERVER_TS = 0x08
_DV_SOURCE_PICO = 0x10
_DV_SERVER_PICO = 0x20

_VARIANT_ARRAY_DIMENSIONS = 0x40
_VARIANT_ARRAY_VALUES = 0x80


def datetime_to_opc(moment: datetime) -> int:
    """UTC datetime -> OPC UA DateTime (100 ns ticks since 1601-01-01)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 10_000_000) + _OPC_EPOCH_OFFSET_100NS


def opc_to_datetime(ticks: int) -> datetime:
    """OPC UA DateTime -> UTC datetime (clamped to the unix-representable range)."""
    seconds = (ticks - _OPC_EPOCH_OFFSET_100NS) / 10_000_000
    return datetime.fromtimestamp(max(seconds, 0.0), tz=timezone.utc)


@dataclass
class FieldValue:
    """One dataset field: a typed value with OPC UA quality and source time."""

    type: BuiltinType
    value: object = None
    status: int = STATUS_GOOD
    source_timestamp: int | None = None  # OPC UA DateTime ticks

    @property
    def is_good(self) -> bool:
        return (self.status & 0xC0000000) == 0

    @property
    def source_datetime(self) -> datetime | None:
        if self.source_timestamp is None:
            return None
        return opc_to_datetime(self.source_timestamp)


@dataclass
class DataSetMessage:
    dataset_writer_id: int = 0
    sequence_number: int | None = None
    keep_alive: bool = False
    fields: list[FieldValue] = field(default_factory=list)


@dataclass
class NetworkMessage:
    publisher_id: int = 0
    publisher_id_type: PublisherIdType = PublisherIdType.UINT16
    writer_group_id: int | None = None
    group_sequence_number: int | None = None
    messages: list[DataSetMessage] = field(default_factory=list)


class _Writer:
    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def u8(self, v: int) -> None:
        self._parts.append(struct.pack("<B", v))

    def u16(self, v: int) -> None:
        self._parts.append(struct.pack("<H", v))

    def raw(self, data: bytes) -> None:
        self._parts.append(data)

    def getvalue(self) -> bytes:
        return b"".join(self._parts)


def _encode_string(out: _Writer, text: str) -> None:
    data = text.encode("utf-8")
    out.raw(struct.pack("<i", len(data)))
    out.raw(data)


def _encode_scalar(out: _Writer, fv: FieldValue) -> None:
    if fv.type == BuiltinType.STRING:
        _encode_string(out, "" if fv.value is None else str(fv.value))
        return
    packer = _SCALAR_STRUCT.get(fv.type)
    if packer is None:
        raise WireError(f"unsupported field type {fv.type!r}")
    value = fv.value
    if fv.type == BuiltinType.BOOLEAN:
        value = 1 if value else 0
    try:
        out.raw(packer.pack(value))
    except struct.error as error:
        raise WireError(f"value {value!r} does not fit {fv.type.name}: {error}") from None


def _encode_variant(out: _Writer, fv: FieldValue) -> None:
    if fv.type == BuiltinType.NULL or fv.value is None:
        out.u8(0)
        return
    out.u8(int(fv.type))
    _encode_scalar(out, fv)


def _encode_datavalue(out: _Writer, fv: FieldValue) -> None:
    mask = 0
    if fv.type != BuiltinType.NULL and fv.value is not None:
        mask |= _DV_VALUE
    if fv.status != STATUS_GOOD:
        mask |= _DV_STATUS
    if fv.source_timestamp is not None:
        mask |= _DV_SOURCE_TS
    out.u8(mask)
    if mask & _DV_VALUE:
        _encode_variant(out, fv)
    if mask & _DV_STATUS:
        out.raw(struct.pack("<I", fv.status))
    if mask & _DV_SOURCE_TS:
        out.raw(struct.pack("<q", fv.source_timestamp))


def _encode_dataset_message(message: DataSetMessage, datavalue_fields: bool) -> bytes:
    out = _Writer()
    encoding = _FIELD_ENCODING_DATAVALUE if datavalue_fields else _FIELD_ENCODING_VARIANT
    flags1 = _DSM_VALID | (encoding << 1)
    if message.sequence_number is not None:
        flags1 |= _DSM_SEQUENCE_NUMBER
    out.u8(flags1)
    if message.sequence_number is not None:
        out.u16(message.sequence_number & 0xFFFF)
    out.u16(len(message.fields))
    for fv in message.fields:
        if datavalue_fields:
            _encode_datavalue(out, fv)
        else:
            _encode_variant(out, fv)
    return out.getvalue()


def encode_network_message(message: NetworkMessage, *, datavalue_fields: bool = True) -> bytes:
    """Encode to UADP bytes. With ``datavalue_fields`` every field carries
    status + source timestamp (hypernova-native); without, plain Variant
    fields byte-compatible with supernova's C++ publisher."""
    if not message.messages:
        raise WireError("a NetworkMessage needs at least one DataSetMessage")
    if len(message.messages) > 255:
        raise WireError("too many DataSetMessages for one NetworkMessage")

    out = _Writer()
    group_header = message.writer_group_id is not None or message.group_sequence_number is not None
    extended1 = message.publisher_id_type != PublisherIdType.BYTE

    flags = _UADP_VERSION | _NM_PUBLISHER_ID | _NM_PAYLOAD_HEADER
    if group_header:
        flags |= _NM_GROUP_HEADER
    if extended1:
        flags |= _NM_EXTENDED_FLAGS_1
    out.u8(flags)
    if extended1:
        out.u8(int(message.publisher_id_type))
    try:
        out.raw(_PID_STRUCT[message.publisher_id_type].pack(message.publisher_id))
    except struct.error as error:
        raise WireError(f"publisher id {message.publisher_id} does not fit "
                        f"{message.publisher_id_type.name}: {error}") from None

    if group_header:
        gh_flags = 0
        if message.writer_group_id is not None:
            gh_flags |= _GH_WRITER_GROUP_ID
        if message.group_sequence_number is not None:
            gh_flags |= _GH_SEQUENCE_NUMBER
        out.u8(gh_flags)
        if message.writer_group_id is not None:
            out.u16(message.writer_group_id)
        if message.group_sequence_number is not None:
            out.u16(message.group_sequence_number & 0xFFFF)

    out.u8(len(message.messages))
    for dsm in message.messages:
        out.u16(dsm.dataset_writer_id)

    bodies = [_encode_dataset_message(dsm, datavalue_fields) for dsm in message.messages]
    if len(bodies) > 1:
        for body in bodies:
            if len(body) > 0xFFFF:
                raise WireError("DataSetMessage too large")
            out.u16(len(body))
    for body in bodies:
        out.raw(body)
    return out.getvalue()


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def _take(self, n: int, what: str) -> bytes:
        if self._pos + n > len(self._data):
            raise WireError(f"truncated datagram: {what}")
        chunk = self._data[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def u8(self, what: str = "byte") -> int:
        return self._take(1, what)[0]

    def u16(self, what: str = "uint16") -> int:
        return struct.unpack("<H", self._take(2, what))[0]

    def u32(self, what: str = "uint32") -> int:
        return struct.unpack("<I", self._take(4, what))[0]

    def i64(self, what: str = "int64") -> int:
        return struct.unpack("<q", self._take(8, what))[0]

    def unpack(self, packer: struct.Struct, what: str):
        return packer.unpack(self._take(packer.size, what))[0]

    def string(self) -> str:
        length = struct.unpack("<i", self._take(4, "string length"))[0]
        if length <= 0:
            return ""
        return self._take(length, "string body").decode("utf-8", errors="replace")

    def skip(self, n: int, what: str) -> None:
        self._take(n, what)


def _decode_variant(reader: _Reader) -> FieldValue:
    mask = reader.u8("variant mask")
    if mask & (_VARIANT_ARRAY_VALUES | _VARIANT_ARRAY_DIMENSIONS):
        raise WireError("array-valued fields are not supported")
    type_id = mask & 0x3F
    try:
        builtin = BuiltinType(type_id)
    except ValueError:
        raise WireError(f"unsupported variant type {type_id}") from None
    if builtin == BuiltinType.NULL:
        return FieldValue(BuiltinType.NULL, None)
    if builtin == BuiltinType.STRING:
        return FieldValue(builtin, reader.string())
    value = reader.unpack(_SCALAR_STRUCT[builtin], builtin.name)
    if builtin == BuiltinType.BOOLEAN:
        value = bool(value)
    return FieldValue(builtin, value)


def _decode_datavalue(reader: _Reader) -> FieldValue:
    mask = reader.u8("DataValue mask")
    fv = FieldValue(BuiltinType.NULL, None)
    if mask & _DV_VALUE:
        fv = _decode_variant(reader)
    if mask & _DV_STATUS:
        fv.status = reader.u32("DataValue status")
    if mask & _DV_SOURCE_TS:
        fv.source_timestamp = reader.i64("DataValue source timestamp")
    if mask & _DV_SERVER_TS:
        reader.skip(8, "DataValue server timestamp")
    if mask & _DV_SOURCE_PICO:
        reader.skip(2, "DataValue source picoseconds")
    if mask & _DV_SERVER_PICO:
        reader.skip(2, "DataValue server picoseconds")
    return fv


def _decode_dataset_message(reader: _Reader) -> DataSetMessage:
    flags1 = reader.u8("DataSetFlags1")
    encoding = (flags1 & _DSM_FIELD_ENCODING_MASK) >> 1
    message_type = _DSM_TYPE_KEY_FRAME
    timestamp = False
    picoseconds = False
    if flags1 & _DSM_FLAGS_2:
        flags2 = reader.u8("DataSetFlags2")
        message_type = flags2 & _DSM2_TYPE_MASK
        timestamp = bool(flags2 & _DSM2_TIMESTAMP)
        picoseconds = bool(flags2 & _DSM2_PICOSECONDS)

    dsm = DataSetMessage()
    if flags1 & _DSM_SEQUENCE_NUMBER:
        dsm.sequence_number = reader.u16("DataSetMessage sequence number")
    if timestamp:
        reader.skip(8, "DataSetMessage timestamp")
    if picoseconds:
        reader.skip(2, "DataSetMessage picoseconds")
    if flags1 & _DSM_STATUS:
        reader.skip(2, "DataSetMessage status")
    if flags1 & _DSM_CFG_MAJOR:
        reader.skip(4, "config major version")
    if flags1 & _DSM_CFG_MINOR:
        reader.skip(4, "config minor version")

    if message_type == _DSM_TYPE_KEEP_ALIVE:
        dsm.keep_alive = True
        return dsm
    if message_type != _DSM_TYPE_KEY_FRAME:
        raise WireError(f"unsupported DataSetMessage type {message_type}")
    if encoding == _FIELD_ENCODING_RAW:
        raise WireError("RawData field encoding is not supported")

    count = reader.u16("field count")
    for _ in range(count):
        if encoding == _FIELD_ENCODING_VARIANT:
            dsm.fields.append(_decode_variant(reader))
        else:
            dsm.fields.append(_decode_datavalue(reader))
    return dsm


def decode_network_message(data: bytes) -> NetworkMessage:
    """Decode a UADP datagram; raises WireError with a diagnostic on anything
    unsupported (chunked, secured, promoted-only, arrays, raw encoding)."""
    reader = _Reader(data)
    flags = reader.u8("header flags")
    if flags & 0x0F != _UADP_VERSION:
        raise WireError(f"unsupported UADP version {flags & 0x0F}")

    publisher_id_enabled = bool(flags & _NM_PUBLISHER_ID)
    group_header_enabled = bool(flags & _NM_GROUP_HEADER)
    payload_header_enabled = bool(flags & _NM_PAYLOAD_HEADER)

    pid_type = PublisherIdType.BYTE
    dataset_class_id = security = timestamp = picoseconds = promoted = False
    if flags & _NM_EXTENDED_FLAGS_1:
        ext1 = reader.u8("ExtendedFlags1")
        try:
            pid_type = PublisherIdType(ext1 & _EXT1_PID_TYPE_MASK)
        except ValueError:
            raise WireError(f"unsupported publisher id type {ext1 & _EXT1_PID_TYPE_MASK}") from None
        dataset_class_id = bool(ext1 & _EXT1_DATASET_CLASS_ID)
        security = bool(ext1 & _EXT1_SECURITY)
        timestamp = bool(ext1 & _EXT1_TIMESTAMP)
        picoseconds = bool(ext1 & _EXT1_PICOSECONDS)
        if ext1 & _EXT1_EXTENDED_FLAGS_2:
            ext2 = reader.u8("ExtendedFlags2")
            if ext2 & _EXT2_CHUNK:
                raise WireError("chunked NetworkMessages are not supported")
            promoted = bool(ext2 & _EXT2_PROMOTED_FIELDS)
            if ext2 & _EXT2_NM_TYPE_MASK:
                raise WireError("not a DataSetMessage payload")

    message = NetworkMessage(publisher_id_type=pid_type)
    if publisher_id_enabled:
        message.publisher_id = reader.unpack(_PID_STRUCT[pid_type], "publisher id")

    if dataset_class_id:
        reader.skip(16, "DataSetClassId")

    if group_header_enabled:
        gh_flags = reader.u8("GroupHeader flags")
        if gh_flags & _GH_WRITER_GROUP_ID:
            message.writer_group_id = reader.u16("writer group id")
        if gh_flags & _GH_GROUP_VERSION:
            reader.skip(4, "group version")
        if gh_flags & _GH_NM_NUMBER:
            reader.skip(2, "network message number")
        if gh_flags & _GH_SEQUENCE_NUMBER:
            message.group_sequence_number = reader.u16("group sequence number")

    count = 1
    writer_ids: list[int] = []
    if payload_header_enabled:
        count = reader.u8("payload header count")
        if count == 0:
            raise WireError("payload header declares zero DataSetMessages")
        writer_ids = [reader.u16("payload writer ids") for _ in range(count)]

    if timestamp:
        reader.skip(8, "NetworkMessage timestamp")
    if picoseconds:
        reader.skip(2, "NetworkMessage picoseconds")
    if promoted:
        size = reader.u16("promoted fields size")
        reader.skip(size, "promoted fields")
    if security:
        raise WireError("secured NetworkMessages are not supported")

    if payload_header_enabled and count > 1:
        for _ in range(count):
            reader.u16("DataSetMessage sizes")

    for index in range(count):
        dsm = _decode_dataset_message(reader)
        dsm.dataset_writer_id = writer_ids[index] if writer_ids else 0
        message.messages.append(dsm)
    return message
