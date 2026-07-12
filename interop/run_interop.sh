#!/bin/bash
# supernova (C++) <-> hypernova (Python) interop, both directions, on a real
# docker network with real multicast.
#
# Direction 1: supernova server publishes (Variant fields) ->
#   hypernova registry registers the name, subscribes by name, and the
#   registry's own live-browser path must see the values too.
# Direction 2: hypernova Publisher publishes (DataValue fields) ->
#   supernova server's DataSetReader lands them in its address space
#   (observed via its MIRROR log lines).
#
# Requires the supernova bench cells built by the supernova campaign:
#   $BENCH/cells/e2e-pub-uasdk  (publisher tree, ticking mainLoop)
#   $BENCH/cells/e2e-sub-o6     (subscriber tree, MIRROR-logging mainLoop)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HYPERNOVA="$(dirname "$HERE")"
BENCH="${SUPERNOVA_BENCH:-$HOME/code/quasarnova-team/.supernova-bench}"
NET=hn-interop
IMAGE_SNOVA=ghcr.io/quasar-team/quasar-uasdk:latest
IMAGE_PY=python:3.12-slim

cleanup() {
  docker rm -f hn-snova-pub hn-snova-sub hn-py >/dev/null 2>&1
  docker network rm $NET >/dev/null 2>&1
}
trap cleanup EXIT
cleanup 2>/dev/null
docker network create $NET >/dev/null

echo "=== direction 1: supernova C++ publisher -> hypernova subscriber-by-name ==="
docker run -d --name hn-snova-pub --network $NET --platform linux/amd64 \
  -v "$BENCH/cells/e2e-pub-uasdk/supernova:/cell/supernova" \
  -w /cell/supernova/build/bin $IMAGE_SNOVA ./OpcUaServer >/dev/null

docker run -d --name hn-py --network $NET \
  -v "$HYPERNOVA:/hypernova" -w /hypernova $IMAGE_PY sleep 3600 >/dev/null
docker exec hn-py pip install -q -e . >/dev/null 2>&1

docker exec -d hn-py hypernova registry --store ''
sleep 2

docker exec hn-py hypernova register site/area1/interop/env \
  --address opc.udp://239.0.0.5:4840 --publisher-id 42 \
  --writer-group-id 100 --dataset-writer-id 1 \
  --field counter=INT32 --field temperature=DOUBLE --field label=STRING \
  --registry http://localhost:4850 \
  --description "supernova e2e publisher, interop test" || exit 1

docker exec hn-py hypernova sub site/area1/interop/env \
  --registry http://localhost:4850 --count 12 --timeout 20 > "$HERE/dir1_sub.log" || {
    echo "DIRECTION 1 FAIL: no data"; cat "$HERE/dir1_sub.log"; exit 1; }

sleep 1
docker exec hn-py python3 - <<'EOF' > "$HERE/dir1_registry.log" || exit 1
import json, urllib.request
detail = json.loads(urllib.request.urlopen(
    "http://localhost:4850/api/publications/site/area1/interop/env", timeout=5).read())
live = detail["live"]
values = {v["name"]: v for v in detail["values"]}
assert not live["stale"], "registry browser sees the stream as stale"
assert live["messages"] > 5, f"registry saw only {live['messages']} messages"
assert values["label"]["value"] == "supernova", values
assert abs(values["temperature"]["value"] - values["counter"]["value"] * 0.5) <= 0.5 + 1e-9
print(f"registry live path: {live['messages']} msgs at {live['rateHz']} Hz, "
      f"counter={values['counter']['value']}, ratio OK")
EOF
cat "$HERE/dir1_registry.log"

python3 "$HERE/assert_dir1.py" "$HERE/dir1_sub.log" || exit 1
docker rm -f hn-snova-pub >/dev/null

echo "=== direction 2: hypernova publisher -> supernova C++ DataSetReader ==="
SUBTREE="$BENCH/cells/e2e-sub-o6/supernova"
cp "$HERE/supernova_reader_config.xml" "$SUBTREE/bin/config.xml"
rm -f "$SUBTREE/build/bin/config.xml"
cp "$HERE/supernova_reader_config.xml" "$SUBTREE/build/bin/config.xml"
docker run -d --name hn-snova-sub --network $NET --platform linux/amd64 \
  -v "$BENCH/cells/e2e-sub-o6/supernova:/cell/supernova" \
  -w /cell/supernova/build/bin $IMAGE_SNOVA ./OpcUaServer >/dev/null

for i in $(seq 1 30); do
  docker logs hn-snova-sub 2>&1 | grep "PubSub: engine started" >/dev/null && break
  sleep 1
done
docker logs hn-snova-sub 2>&1 | grep "PubSub: engine started" >/dev/null || {
  echo "DIRECTION 2 FAIL: supernova reader never started"; docker logs hn-snova-sub | tail -20; exit 1; }

docker exec hn-py hypernova pub site/area1/interop/reverse \
  --address opc.udp://239.0.0.9:4842 --publisher-id 77 \
  --writer-group-id 300 --dataset-writer-id 9 \
  --field mirror=INT32 --field temperature=DOUBLE \
  --value mirror=5000 --value temperature=2500 \
  --interval 0.1 --count 40 --ramp \
  --registry http://localhost:4850 || exit 1

sleep 1
docker logs hn-snova-sub 2>&1 | grep "MIRROR" > "$HERE/dir2_mirror.log"
python3 "$HERE/assert_dir2.py" "$HERE/dir2_mirror.log" || exit 1

echo "INTEROP: PASS (both directions)"
