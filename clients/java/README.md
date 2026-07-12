# hypernova Java client

Dependency-free (JDK 11+), one package, four classes:

| Class | Role |
|---|---|
| `Wire` | UADP codec — the exact profile of the Python client and supernova's C++ engine, held **byte-identical** to both by the shared C++-generated golden vectors (`GoldenTest`). Scalars, 1-D arrays, DataValue quality+timestamps, HMAC-SHA256 signature verify/sign |
| `Registry` | Name lookup over the registry REST API, with comma-separated primary/secondary failover |
| `Subscriber` | `Subscriber.byName(registry, "atlas/dcs/...", network)` → blocking `take(timeout)` of name-resolved updates |
| `Publisher` | Send datasets (Part 14 UADP) with per-field quality and source time |

## Use

No build system required — vendor the four files, or:

```bash
javac --release 11 -d build $(find src/main -name "*.java")
```

```java
try (Subscriber sub = Subscriber.byName("http://registry:4850",
                                        "atlas/dcs/atca/crate1/env", null)) {
    while (true) {
        Subscriber.Update update = sub.take(5000);
        Wire.FieldValue temperature = update.values.get("temperature");
        System.out.printf("%s %s good=%b%n",
            update.sequenceNumber, temperature.value, temperature.isGood());
    }
}
```

Signed streams: pass the key bytes to `Subscriber.byName(..., key)` — frames
then *must* be signed and verify, exactly like the Python client.

## Test

```bash
javac --release 11 -d build $(find src -name "*.java")
java -cp build ch.quasarnova.hypernova.GoldenTest ../../tests/golden/vectors.json
```

Runs in CI next to the Python suite, including a live Python-publisher →
Java-subscriber loop.
