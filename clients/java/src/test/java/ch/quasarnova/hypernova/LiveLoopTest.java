/* © Copyright CERN, 2026. BSD-2-Clause.
 * Live cross-language loop: subscribes (explicit coordinates, loopback
 * unicast) and expects a Python publisher to feed it — driven by CI.
 * args: port count. Exit 0 = received `count` consistent updates. */
package ch.quasarnova.hypernova;

import java.util.concurrent.TimeoutException;

public final class LiveLoopTest {

    public static void main(String[] arguments) throws Exception {
        int port = Integer.parseInt(arguments[0]);
        int wanted = Integer.parseInt(arguments[1]);

        Registry.Coordinates coordinates = new Registry.Coordinates();
        coordinates.address = "opc.udp://127.0.0.1:" + port;
        coordinates.publisherId = 21;
        coordinates.writerGroupId = 2;
        coordinates.dataSetWriterId = 2;
        coordinates.fieldNames.add("counter");
        coordinates.fieldNames.add("samples");

        int received = 0;
        Long lastCounter = null;
        try (Subscriber subscriber = new Subscriber("ci/javaloop", coordinates, null, false)) {
            long deadline = System.currentTimeMillis() + 30000;
            while (received < wanted && System.currentTimeMillis() < deadline) {
                Subscriber.Update update;
                try {
                    update = subscriber.take(2000);
                } catch (TimeoutException timeout) {
                    continue;
                }
                Wire.FieldValue counter = update.values.get("counter");
                Wire.FieldValue samples = update.values.get("samples");
                if (counter == null || samples == null || !samples.isArray()) {
                    System.out.println("FAIL: malformed update " + update.values.keySet());
                    System.exit(1);
                }
                long value = (Long) counter.value;
                if (lastCounter != null && value < lastCounter) {
                    System.out.println("FAIL: counter went backwards");
                    System.exit(1);
                }
                if (counter.sourceTimestamp == null || !counter.isGood()) {
                    System.out.println("FAIL: quality/timestamp missing");
                    System.exit(1);
                }
                lastCounter = value;
                received++;
            }
        }
        if (received < wanted) {
            System.out.println("FAIL: only " + received + " updates from the Python publisher");
            System.exit(1);
        }
        System.out.println("LIVE LOOP OK: " + received + " python->java updates, "
                + "arrays + quality + timestamps intact");
    }

    private LiveLoopTest() { }
}
