/* © Copyright CERN, 2026. BSD-2-Clause.
 * Subscribe to a hypernova publication by name:
 *
 *     try (Subscriber sub = Subscriber.byName("http://registry:4850",
 *                                             "atlas/dcs/atca/crate1/env", null)) {
 *         Subscriber.Update update = sub.take(5000);
 *         System.out.println(update.values.get("temperature").value);
 *     }
 */
package ch.quasarnova.hypernova;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.MulticastSocket;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

public final class Subscriber implements AutoCloseable {

    public static final class Update {
        public final String name;
        public final Integer sequenceNumber;
        public final Map<String, Wire.FieldValue> values = new LinkedHashMap<>();

        Update(String name, Integer sequenceNumber) {
            this.name = name;
            this.sequenceNumber = sequenceNumber;
        }
    }

    private final String name;
    private final Registry.Coordinates coordinates;
    private final DatagramSocket socket;
    private final Thread thread;
    private final BlockingQueue<Update> queue = new LinkedBlockingQueue<>(1000);
    private final byte[] verifyKey;
    private final boolean requireSigned;
    private volatile boolean running = true;

    public static Subscriber byName(String registries, String name, String network)
            throws IOException {
        return new Subscriber(name, Registry.lookup(registries, name, network), null, false);
    }

    public static Subscriber byName(String registries, String name, String network,
                                    byte[] verifyKey) throws IOException {
        return new Subscriber(name, Registry.lookup(registries, name, network),
                              verifyKey, verifyKey != null);
    }

    public Subscriber(String name, Registry.Coordinates coordinates,
                      byte[] verifyKey, boolean requireSigned) throws IOException {
        this.name = name;
        this.coordinates = coordinates;
        this.verifyKey = verifyKey;
        this.requireSigned = requireSigned;
        URI uri = URI.create(coordinates.address);
        InetAddress host = InetAddress.getByName(uri.getHost());
        if (host.isMulticastAddress()) {
            MulticastSocket multicast = new MulticastSocket(uri.getPort());
            multicast.joinGroup(new InetSocketAddress(host, uri.getPort()), null);
            this.socket = multicast;
        } else {
            this.socket = new DatagramSocket(uri.getPort());
        }
        this.socket.setSoTimeout(250);
        this.thread = new Thread(this::run, "hypernova-sub-" + name);
        this.thread.setDaemon(true);
        this.thread.start();
    }

    /** Blocks for the next update; TimeoutException after timeoutMillis. */
    public Update take(long timeoutMillis) throws InterruptedException, TimeoutException {
        Update update = queue.poll(timeoutMillis, TimeUnit.MILLISECONDS);
        if (update == null) throw new TimeoutException("no update for " + name);
        return update;
    }

    private void run() {
        byte[] buffer = new byte[65536];
        while (running) {
            DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
            try {
                socket.receive(packet);
            } catch (SocketTimeoutException timeout) {
                continue;
            } catch (IOException closed) {
                return;
            }
            byte[] datagram = new byte[packet.getLength()];
            System.arraycopy(packet.getData(), 0, datagram, 0, packet.getLength());
            Wire.NetworkMessage message;
            try {
                message = Wire.decode(datagram, verifyKey, requireSigned);
            } catch (Wire.WireException undecodable) {
                continue;
            }
            if (message.publisherId != coordinates.publisherId) continue;
            int group = message.writerGroupId == null ? 0 : message.writerGroupId;
            if (group != coordinates.writerGroupId) continue;
            for (Wire.DataSetMessage dsm : message.messages) {
                if (dsm.keepAlive || dsm.dataSetWriterId != coordinates.dataSetWriterId) continue;
                Update update = new Update(name, dsm.sequenceNumber);
                for (int i = 0; i < dsm.fields.size(); i++) {
                    String fieldName = i < coordinates.fieldNames.size()
                            ? coordinates.fieldNames.get(i) : "field" + i;
                    update.values.put(fieldName, dsm.fields.get(i));
                }
                queue.offer(update);
            }
        }
    }

    @Override
    public void close() {
        running = false;
        socket.close();
        try {
            thread.join(2000);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }
}
