/* © Copyright CERN, 2026. BSD-2-Clause.
 * Publish a hypernova dataset from Java:
 *
 *     try (Publisher pub = new Publisher("opc.udp://239.10.0.1:14840",
 *                                        42, 100, 1, null)) {
 *         Map<String, Wire.FieldValue> sample = new LinkedHashMap<>();
 *         sample.put("temperature", Publisher.doubleValue(21.5));
 *         pub.send(sample);
 *     }
 *
 * Registration with the registry is a one-time operational act (CLI or REST);
 * this class only moves data. */
package ch.quasarnova.hypernova;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.URI;
import java.time.Instant;
import java.util.Map;

public final class Publisher implements AutoCloseable {

    private static final long OPC_EPOCH_OFFSET = 116444736000000000L;

    private final DatagramSocket socket;
    private final InetAddress host;
    private final int port;
    private final long publisherId;
    private final int writerGroupId;
    private final int dataSetWriterId;
    private final byte[] signKey;
    private int sequence = 0;

    public Publisher(String address, long publisherId, int writerGroupId,
                     int dataSetWriterId, byte[] signKey) throws IOException {
        URI uri = URI.create(address);
        this.host = InetAddress.getByName(uri.getHost());
        this.port = uri.getPort();
        this.publisherId = publisherId;
        this.writerGroupId = writerGroupId;
        this.dataSetWriterId = dataSetWriterId;
        this.signKey = signKey;
        this.socket = new DatagramSocket();
    }

    public static Wire.FieldValue doubleValue(double value) {
        return new Wire.FieldValue(Wire.BuiltinType.DOUBLE, value, Wire.STATUS_GOOD, nowTicks());
    }

    public static Wire.FieldValue int32Value(int value) {
        return new Wire.FieldValue(Wire.BuiltinType.INT32, (long) value, Wire.STATUS_GOOD, nowTicks());
    }

    public static Wire.FieldValue stringValue(String value) {
        return new Wire.FieldValue(Wire.BuiltinType.STRING, value, Wire.STATUS_GOOD, nowTicks());
    }

    public static long nowTicks() {
        Instant now = Instant.now();
        return now.getEpochSecond() * 10_000_000L + now.getNano() / 100 + OPC_EPOCH_OFFSET;
    }

    /** Send one DataSetMessage; iteration order of the map = wire field order. */
    public synchronized void send(Map<String, Wire.FieldValue> fields) throws IOException {
        sequence = (sequence + 1) & 0xFFFF;
        Wire.NetworkMessage message = new Wire.NetworkMessage();
        message.publisherId = publisherId;
        message.publisherIdType = 1;
        message.writerGroupId = writerGroupId;
        message.groupSequenceNumber = sequence;
        Wire.DataSetMessage dsm = new Wire.DataSetMessage();
        dsm.dataSetWriterId = dataSetWriterId;
        dsm.sequenceNumber = sequence;
        dsm.fields.addAll(fields.values());
        message.messages.add(dsm);
        byte[] wire = Wire.encode(message, true, signKey);
        socket.send(new DatagramPacket(wire, wire.length, host, port));
    }

    @Override
    public void close() {
        socket.close();
    }
}
