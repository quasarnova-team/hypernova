/* © Copyright CERN, 2026. BSD-2-Clause.
 * hypernova Java client — UADP wire codec for the hypernova/supernova profile:
 * NetworkMessage v1, publisher id, group header, payload header, data-key-frame
 * DataSetMessages with Variant or DataValue field encoding, scalars and
 * one-dimensional arrays, optional HMAC-SHA256 signature (hypernova signing
 * profile v1). Dependency-free (JDK 11+). */
package ch.quasarnova.hypernova;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public final class Wire {

    public static final int SIGNATURE_LENGTH = 32;
    public static final long STATUS_GOOD = 0x00000000L;

    public static class WireException extends RuntimeException {
        public WireException(String message) { super(message); }
    }

    public enum BuiltinType {
        NULL(0), BOOLEAN(1), SBYTE(2), BYTE(3), INT16(4), UINT16(5), INT32(6),
        UINT32(7), INT64(8), UINT64(9), FLOAT(10), DOUBLE(11), STRING(12), DATETIME(13);

        public final int id;
        BuiltinType(int id) { this.id = id; }

        static BuiltinType from(int id) {
            for (BuiltinType type : values()) if (type.id == id) return type;
            throw new WireException("unsupported variant type " + id);
        }
    }

    /** One dataset field: value (Object or List for arrays), quality, source time. */
    public static final class FieldValue {
        public final BuiltinType type;
        public final Object value;          // Boolean/Long/Double/String or List<Object>
        public final long status;           // OPC UA StatusCode
        public final Long sourceTimestamp;  // OPC UA ticks, nullable

        public FieldValue(BuiltinType type, Object value, long status, Long sourceTimestamp) {
            this.type = type;
            this.value = value;
            this.status = status;
            this.sourceTimestamp = sourceTimestamp;
        }

        public boolean isGood() { return (status & 0xC0000000L) == 0; }
        public boolean isArray() { return value instanceof List; }
    }

    public static final class DataSetMessage {
        public int dataSetWriterId;
        public Integer sequenceNumber;
        public boolean keepAlive;
        public final List<FieldValue> fields = new ArrayList<>();
    }

    public static final class NetworkMessage {
        public long publisherId;
        public int publisherIdType = 1; // UINT16
        public Integer writerGroupId;
        public Integer groupSequenceNumber;
        public boolean signed;
        public Boolean verified;
        public final List<DataSetMessage> messages = new ArrayList<>();
    }

    private static final class Reader {
        private final ByteBuffer buffer;
        private int end;

        Reader(byte[] data) {
            this.buffer = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);
            this.end = data.length;
        }

        void limit(int newEnd) {
            if (newEnd < buffer.position()) throw new WireException("truncated: signature overlaps headers");
            end = newEnd;
        }

        private void need(int n, String what) {
            if (buffer.position() + n > end) throw new WireException("truncated datagram: " + what);
        }

        int u8(String what) { need(1, what); return buffer.get() & 0xFF; }
        int u16(String what) { need(2, what); return buffer.getShort() & 0xFFFF; }
        long u32(String what) { need(4, what); return buffer.getInt() & 0xFFFFFFFFL; }
        long i64(String what) { need(8, what); return buffer.getLong(); }
        float f32(String what) { need(4, what); return buffer.getFloat(); }
        double f64(String what) { need(8, what); return buffer.getDouble(); }

        String string() {
            need(4, "string length");
            int length = buffer.getInt();
            if (length <= 0) return "";
            need(length, "string body");
            byte[] raw = new byte[length];
            buffer.get(raw);
            return new String(raw, StandardCharsets.UTF_8);
        }

        void skip(int n, String what) { need(n, what); buffer.position(buffer.position() + n); }
    }

    private static Object decodeScalarBody(Reader reader, BuiltinType type) {
        switch (type) {
            case BOOLEAN: return reader.u8("Boolean") != 0;
            case SBYTE: return (long) (byte) reader.u8("SByte");
            case BYTE: return (long) reader.u8("Byte");
            case INT16: return (long) (short) reader.u16("Int16");
            case UINT16: return (long) reader.u16("UInt16");
            case INT32: return (long) (int) reader.u32("Int32");
            case UINT32: return reader.u32("UInt32");
            case INT64: case DATETIME: return reader.i64(type.name());
            case UINT64: return reader.i64("UInt64"); // caller treats as unsigned bits
            case FLOAT: return (double) reader.f32("Float");
            case DOUBLE: return reader.f64("Double");
            case STRING: return reader.string();
            default: throw new WireException("unsupported scalar type " + type);
        }
    }

    private static FieldValue decodeVariant(Reader reader) {
        int mask = reader.u8("variant mask");
        if ((mask & 0x40) != 0) throw new WireException("multi-dimensional arrays are not supported");
        BuiltinType type = BuiltinType.from(mask & 0x3F);
        if (type == BuiltinType.NULL) return new FieldValue(type, null, STATUS_GOOD, null);
        if ((mask & 0x80) != 0) {
            int length = (int) reader.u32("array length");
            List<Object> elements = new ArrayList<>();
            if (length > 0) {
                if (length > 1_000_000) throw new WireException("array length is implausible");
                for (int i = 0; i < length; i++) elements.add(decodeScalarBody(reader, type));
            }
            return new FieldValue(type, elements, STATUS_GOOD, null);
        }
        return new FieldValue(type, decodeScalarBody(reader, type), STATUS_GOOD, null);
    }

    private static FieldValue decodeDataValue(Reader reader) {
        int mask = reader.u8("DataValue mask");
        FieldValue inner = new FieldValue(BuiltinType.NULL, null, STATUS_GOOD, null);
        if ((mask & 0x01) != 0) inner = decodeVariant(reader);
        long status = STATUS_GOOD;
        Long sourceTimestamp = null;
        if ((mask & 0x02) != 0) status = reader.u32("DataValue status");
        if ((mask & 0x04) != 0) sourceTimestamp = reader.i64("source timestamp");
        if ((mask & 0x08) != 0) reader.skip(8, "server timestamp");
        if ((mask & 0x10) != 0) reader.skip(2, "source picoseconds");
        if ((mask & 0x20) != 0) reader.skip(2, "server picoseconds");
        return new FieldValue(inner.type, inner.value, status, sourceTimestamp);
    }

    private static DataSetMessage decodeDataSetMessage(Reader reader) {
        int flags1 = reader.u8("DataSetFlags1");
        int encoding = (flags1 & 0x06) >> 1;
        int messageType = 0;
        boolean timestamp = false, picoseconds = false;
        if ((flags1 & 0x80) != 0) {
            int flags2 = reader.u8("DataSetFlags2");
            messageType = flags2 & 0x0F;
            timestamp = (flags2 & 0x10) != 0;
            picoseconds = (flags2 & 0x20) != 0;
        }
        DataSetMessage message = new DataSetMessage();
        if ((flags1 & 0x08) != 0) message.sequenceNumber = reader.u16("sequence number");
        if (timestamp) reader.skip(8, "DSM timestamp");
        if (picoseconds) reader.skip(2, "DSM picoseconds");
        if ((flags1 & 0x10) != 0) reader.skip(2, "DSM status");
        if ((flags1 & 0x20) != 0) reader.skip(4, "config major");
        if ((flags1 & 0x40) != 0) reader.skip(4, "config minor");
        if (messageType == 3) { message.keepAlive = true; return message; }
        if (messageType != 0) throw new WireException("unsupported DataSetMessage type " + messageType);
        if (encoding == 1) throw new WireException("RawData field encoding is not supported");
        int count = reader.u16("field count");
        for (int i = 0; i < count; i++) {
            message.fields.add(encoding == 0 ? decodeVariant(reader) : decodeDataValue(reader));
        }
        return message;
    }

    /** Decode a datagram; verifyKey null = accept unsigned and signed-unverified. */
    public static NetworkMessage decode(byte[] data, byte[] verifyKey, boolean requireSigned) {
        if (requireSigned && verifyKey == null) {
            throw new WireException("require signed needs a verify key: the signed flag alone is unauthenticated");
        }
        Reader reader = new Reader(data);
        int flags = reader.u8("header flags");
        if ((flags & 0x0F) != 1) throw new WireException("unsupported UADP version " + (flags & 0x0F));
        boolean publisherIdEnabled = (flags & 0x10) != 0;
        boolean groupHeader = (flags & 0x20) != 0;
        boolean payloadHeader = (flags & 0x40) != 0;

        int pidType = 0;
        boolean classId = false, security = false, timestamp = false, picoseconds = false, promoted = false;
        if ((flags & 0x80) != 0) {
            int ext1 = reader.u8("ExtendedFlags1");
            pidType = ext1 & 0x07;
            classId = (ext1 & 0x08) != 0;
            security = (ext1 & 0x10) != 0;
            timestamp = (ext1 & 0x20) != 0;
            picoseconds = (ext1 & 0x40) != 0;
            if ((ext1 & 0x80) != 0) {
                int ext2 = reader.u8("ExtendedFlags2");
                if ((ext2 & 0x01) != 0) throw new WireException("chunked NetworkMessages are not supported");
                promoted = (ext2 & 0x02) != 0;
                if ((ext2 & 0x1C) != 0) throw new WireException("not a DataSetMessage payload");
            }
        }

        NetworkMessage message = new NetworkMessage();
        message.publisherIdType = pidType;
        if (publisherIdEnabled) {
            switch (pidType) {
                case 0: message.publisherId = reader.u8("publisher id"); break;
                case 1: message.publisherId = reader.u16("publisher id"); break;
                case 2: message.publisherId = reader.u32("publisher id"); break;
                case 3: message.publisherId = reader.i64("publisher id"); break;
                default: throw new WireException("unsupported publisher id type " + pidType);
            }
        }
        if (classId) reader.skip(16, "DataSetClassId");
        if (groupHeader) {
            int gh = reader.u8("GroupHeader flags");
            if ((gh & 0x01) != 0) message.writerGroupId = reader.u16("writer group id");
            if ((gh & 0x02) != 0) reader.skip(4, "group version");
            if ((gh & 0x04) != 0) reader.skip(2, "network message number");
            if ((gh & 0x08) != 0) message.groupSequenceNumber = reader.u16("group sequence number");
        }
        int count = 1;
        List<Integer> writerIds = new ArrayList<>();
        if (payloadHeader) {
            count = reader.u8("payload count");
            if (count == 0) throw new WireException("payload header declares zero DataSetMessages");
            for (int i = 0; i < count; i++) writerIds.add(reader.u16("payload writer ids"));
        }
        if (timestamp) reader.skip(8, "NM timestamp");
        if (picoseconds) reader.skip(2, "NM picoseconds");
        if (promoted) reader.skip(reader.u16("promoted size"), "promoted fields");

        if (security) {
            int secFlags = reader.u8("SecurityHeader flags");
            if ((secFlags & 0x02) != 0) throw new WireException("encrypted NetworkMessages are not supported");
            if ((secFlags & 0x01) == 0) throw new WireException("SecurityHeader without signed flag");
            reader.skip(4, "security token id");
            reader.skip(reader.u8("nonce length"), "security nonce");
            if ((secFlags & 0x04) != 0) reader.skip(2, "security footer size");
            message.signed = true;
            if (data.length < SIGNATURE_LENGTH + 2) throw new WireException("signed frame shorter than signature");
            reader.limit(data.length - SIGNATURE_LENGTH);
            if (verifyKey != null) {
                byte[] expected = hmac(verifyKey, data, data.length - SIGNATURE_LENGTH);
                byte[] actual = new byte[SIGNATURE_LENGTH];
                System.arraycopy(data, data.length - SIGNATURE_LENGTH, actual, 0, SIGNATURE_LENGTH);
                if (!MessageDigest.isEqual(expected, actual))
                    throw new WireException("signature verification failed (wrong key or tampered frame)");
                message.verified = Boolean.TRUE;
            }
        }
        if (requireSigned && !Boolean.TRUE.equals(message.verified)) {
            throw new WireException("frame is not cryptographically verified (require signed is set)");
        }

        if (payloadHeader && count > 1) {
            for (int i = 0; i < count; i++) reader.u16("DataSetMessage sizes");
        }
        for (int i = 0; i < count; i++) {
            DataSetMessage dsm = decodeDataSetMessage(reader);
            dsm.dataSetWriterId = writerIds.isEmpty() ? 0 : writerIds.get(i);
            message.messages.add(dsm);
        }
        return message;
    }

    private static void requireRange(long value, long min, long max, String type) {
        if (value < min || value > max) {
            throw new WireException("value " + value + " does not fit " + type);
        }
    }

    private static void encodeScalarBody(ByteBuffer out, BuiltinType type, Object value) {
        switch (type) {
            case BOOLEAN: out.put((byte) (((Boolean) value) ? 1 : 0)); break;
            case SBYTE: requireRange(((Number) value).longValue(), -128, 127, "SByte");
                out.put((byte) ((Number) value).longValue()); break;
            case BYTE: requireRange(((Number) value).longValue(), 0, 255, "Byte");
                out.put((byte) ((Number) value).longValue()); break;
            case INT16: requireRange(((Number) value).longValue(), -32768, 32767, "Int16");
                out.putShort((short) ((Number) value).longValue()); break;
            case UINT16: requireRange(((Number) value).longValue(), 0, 65535, "UInt16");
                out.putShort((short) ((Number) value).longValue()); break;
            case INT32: requireRange(((Number) value).longValue(), -2147483648L, 2147483647L, "Int32");
                out.putInt((int) ((Number) value).longValue()); break;
            case UINT32: requireRange(((Number) value).longValue(), 0, 4294967295L, "UInt32");
                out.putInt((int) ((Number) value).longValue()); break;
            case INT64: case UINT64: case DATETIME: out.putLong(((Number) value).longValue()); break;
            case FLOAT: out.putFloat(((Number) value).floatValue()); break;
            case DOUBLE: out.putDouble(((Number) value).doubleValue()); break;
            case STRING: {
                byte[] raw = ((String) value).getBytes(StandardCharsets.UTF_8);
                out.putInt(raw.length);
                out.put(raw);
                break;
            }
            default: throw new WireException("unsupported scalar type " + type);
        }
    }

    private static void encodeVariant(ByteBuffer out, FieldValue field) {
        if (field.type == BuiltinType.NULL || field.value == null) { out.put((byte) 0); return; }
        if (field.isArray()) {
            List<?> elements = (List<?>) field.value;
            out.put((byte) (field.type.id | 0x80));
            out.putInt(elements.size());
            for (Object element : elements) encodeScalarBody(out, field.type, element);
            return;
        }
        out.put((byte) field.type.id);
        encodeScalarBody(out, field.type, field.value);
    }

    private static void encodeDataValue(ByteBuffer out, FieldValue field) {
        int mask = 0;
        if (field.type != BuiltinType.NULL && field.value != null) mask |= 0x01;
        if (field.status != STATUS_GOOD) mask |= 0x02;
        if (field.sourceTimestamp != null) mask |= 0x04;
        out.put((byte) mask);
        if ((mask & 0x01) != 0) encodeVariant(out, field);
        if ((mask & 0x02) != 0) out.putInt((int) field.status);
        if ((mask & 0x04) != 0) out.putLong(field.sourceTimestamp);
    }

    private static int payloadElements(NetworkMessage message) {
        int elements = 0;
        for (DataSetMessage dsm : message.messages) {
            for (FieldValue field : dsm.fields) {
                elements += field.isArray() ? ((java.util.List<?>) field.value).size() + 1 : 1;
            }
        }
        return elements;
    }

    /** Encode; dataValueFields carries quality+timestamp per field. */
    public static byte[] encode(NetworkMessage message, boolean dataValueFields, byte[] signKey) {
        if (message.messages.isEmpty()) throw new WireException("a NetworkMessage needs at least one DataSetMessage");
        int capacity = Math.max(65536, 128 + payloadElements(message) * 16);
        ByteBuffer out = ByteBuffer.allocate(capacity).order(ByteOrder.LITTLE_ENDIAN);
        boolean groupHeader = message.writerGroupId != null || message.groupSequenceNumber != null;
        boolean ext1Needed = message.publisherIdType != 0 || signKey != null;
        int flags = 1 | 0x10 | 0x40 | (groupHeader ? 0x20 : 0) | (ext1Needed ? 0x80 : 0);
        out.put((byte) flags);
        if (ext1Needed) out.put((byte) (message.publisherIdType | (signKey != null ? 0x10 : 0)));
        switch (message.publisherIdType) {
            case 0: requireRange(message.publisherId, 0, 255, "Byte publisher id");
                out.put((byte) message.publisherId); break;
            case 1: requireRange(message.publisherId, 0, 65535, "UInt16 publisher id");
                out.putShort((short) message.publisherId); break;
            case 2: requireRange(message.publisherId, 0, 4294967295L, "UInt32 publisher id");
                out.putInt((int) message.publisherId); break;
            case 3: out.putLong(message.publisherId); break;
            default: throw new WireException("unsupported publisher id type");
        }
        if (groupHeader) {
            int gh = (message.writerGroupId != null ? 0x01 : 0)
                   | (message.groupSequenceNumber != null ? 0x08 : 0);
            out.put((byte) gh);
            if (message.writerGroupId != null) out.putShort(message.writerGroupId.shortValue());
            if (message.groupSequenceNumber != null) out.putShort(message.groupSequenceNumber.shortValue());
        }
        out.put((byte) message.messages.size());
        for (DataSetMessage dsm : message.messages) out.putShort((short) dsm.dataSetWriterId);
        if (signKey != null) {
            out.put((byte) 0x01);           // signed
            out.putInt(1);                   // security token id
            byte[] nonce = new byte[8];
            new java.security.SecureRandom().nextBytes(nonce);
            out.put((byte) nonce.length);
            out.put(nonce);
        }
        List<byte[]> bodies = new ArrayList<>();
        for (DataSetMessage dsm : message.messages) {
            ByteBuffer body = ByteBuffer.allocate(capacity).order(ByteOrder.LITTLE_ENDIAN);
            int flags1 = 0x01 | ((dataValueFields ? 2 : 0) << 1)
                       | (dsm.sequenceNumber != null ? 0x08 : 0);
            body.put((byte) flags1);
            if (dsm.sequenceNumber != null) body.putShort(dsm.sequenceNumber.shortValue());
            body.putShort((short) dsm.fields.size());
            for (FieldValue field : dsm.fields) {
                if (dataValueFields) encodeDataValue(body, field);
                else encodeVariant(body, field);
            }
            byte[] raw = new byte[body.position()];
            body.rewind();
            body.get(raw);
            bodies.add(raw);
        }
        if (bodies.size() > 1) for (byte[] body : bodies) out.putShort((short) body.length);
        for (byte[] body : bodies) out.put(body);
        byte[] wire = new byte[out.position()];
        out.rewind();
        out.get(wire);
        if (signKey != null) {
            byte[] signature = hmac(signKey, wire, wire.length);
            byte[] signed = new byte[wire.length + SIGNATURE_LENGTH];
            System.arraycopy(wire, 0, signed, 0, wire.length);
            System.arraycopy(signature, 0, signed, wire.length, SIGNATURE_LENGTH);
            return signed;
        }
        return wire;
    }

    private static byte[] hmac(byte[] key, byte[] data, int length) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            mac.update(data, 0, length);
            return mac.doFinal();
        } catch (Exception error) {
            throw new WireException("HMAC failure: " + error);
        }
    }

    private Wire() { }
}
