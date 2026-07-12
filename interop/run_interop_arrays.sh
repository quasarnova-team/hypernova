#!/bin/bash
# Array round-trip THROUGH a supernova C++ server:
#   hypernova publishes {mirror: INT32, samples: DOUBLE[]}
#     -> supernova DataSetReader lands both in its address space
#     -> supernova WriterGroup re-publishes {counter, samples}
#     -> hypernova subscriber verifies the array came back intact.
# Uses the built tree from the supernova pubsub-arrays smoke cell (config.xml
# is runtime-only, so no rebuild).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HYPERNOVA="$(dirname "$HERE")"
BENCH="${SUPERNOVA_BENCH:-$HOME/code/quasarnova-team/.supernova-bench}"
TREE="$BENCH/cells/arrays-o6/supernova"
NET=hn-arrays
IMAGE_SNOVA=ghcr.io/quasar-team/quasar-uasdk:latest
IMAGE_PY=python:3.12-slim

[ -x "$TREE/build/bin/OpcUaServer" ] || { echo "run the arrays-o6 smoke cell first"; exit 2; }

cleanup() {
  docker rm -f hn-arr-snova hn-arr-py >/dev/null 2>&1
  docker network rm $NET >/dev/null 2>&1
}
trap cleanup EXIT
cleanup 2>/dev/null
docker network create $NET >/dev/null

rm -f "$TREE/build/bin/config.xml"
cat > "$TREE/build/bin/config.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<configuration xmlns="http://cern.ch/quasar/Configuration" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://cern.ch/quasar/Configuration ../Configuration/Configuration.xsd ">
	<PubSub publisherId="99" publisherIdType="UInt16">
		<Connection address="opc.udp://239.0.0.21:4844" ttl="2" loopback="true">
			<WriterGroup id="100" publishingIntervalMs="100">
				<DataSetWriter id="2">
					<Field source="PS1.counter"/>
					<Field source="PS1.samples"/>
				</DataSetWriter>
			</WriterGroup>
			<DataSetReader publisherId="77" publisherIdType="UInt16" writerGroupId="300" dataSetWriterId="9">
				<Field target="PS1.mirror"/>
				<Field target="PS1.samples"/>
			</DataSetReader>
		</Connection>
	</PubSub>
	<PubSubTester name="PS1"/>
</configuration>
XML

docker run -d --name hn-arr-snova --network $NET --platform linux/amd64 \
  -v "$TREE:/cell/supernova" -w /cell/supernova/build/bin $IMAGE_SNOVA ./OpcUaServer >/dev/null

for i in $(seq 1 30); do
  docker logs hn-arr-snova 2>&1 | grep "PubSub: engine started" >/dev/null && break
  sleep 1
done
docker logs hn-arr-snova 2>&1 | grep "PubSub: engine started" >/dev/null || {
  echo "ARRAYS FAIL: supernova never started"; docker logs hn-arr-snova 2>&1 | tail -15; exit 1; }

docker run -d --name hn-arr-py --network $NET -v "$HYPERNOVA:/hypernova" -w /hypernova \
  $IMAGE_PY sleep 600 >/dev/null
docker exec hn-arr-py pip install -q -e . >/dev/null 2>&1

docker exec -i hn-arr-py python3 - <<'EOF'
import threading, time
from hypernova.client import Publisher, Subscriber

SAMPLES = [1.5, -2.25, 3.125, 1e-7]

publisher = Publisher(
    "arr/in", fields={"mirror": "INT32", "samples": "DOUBLE[]"},
    address="opc.udp://239.0.0.21:4844", publisher_id=77,
    writer_group_id=300, dataset_writer_id=9,
    registry="http://127.0.0.1:1", register=False, ttl=2)

subscriber = Subscriber(
    "arr/out", registry="http://127.0.0.1:1",
    address="opc.udp://239.0.0.21:4844", publisher_id=99,
    writer_group_id=100, dataset_writer_id=2,
    field_names=["counter", "samples"])

received = []
def feed():
    for tick in range(60):
        publisher.send(mirror=4000 + tick, samples=SAMPLES)
        time.sleep(0.1)

with subscriber:
    thread = threading.Thread(target=feed)
    thread.start()
    deadline = time.time() + 12
    while time.time() < deadline and len(received) < 10:
        try:
            update = subscriber.get(timeout=2.0)
        except TimeoutError:
            continue
        values = update.values
        if values["samples"].value:  # skip pre-feed Null publishes
            received.append(values)
    thread.join()
publisher.close()

assert len(received) >= 10, f"only {len(received)} array-bearing frames back from C++"
for values in received:
    got = values["samples"].value
    assert got == SAMPLES, f"array mangled through the C++ address space: {got}"
print(f"ARRAYS OK: {len(received)} frames; DOUBLE[4] survived "
      f"python->C++ reader->address space->C++ writer->python bit-exact")
EOF
RC=$?
[ $RC -eq 0 ] && echo "ARRAY INTEROP: PASS" || { echo "ARRAY INTEROP: FAIL"; docker logs hn-arr-snova 2>&1 | tail -10; }
exit $RC
