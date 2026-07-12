#!/bin/bash
# One command: the DIP-replacement topology live on your machine.
#   demo/run_demo.sh          bring up, verify end to end, leave running
#   demo/run_demo.sh down     tear down
#
# A supernova C++ server multicasts on the "fieldnet" network; the registry
# (feet in both networks) names and watches the stream; a relay pinholes it
# to the "officenet" network where a consumer subscribes by name. The browser is
# yours at http://localhost:4850 while it runs.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export SUPERNOVA_TREE="${SUPERNOVA_TREE:-$HOME/code/quasarnova-team/.supernova-bench/cells/e2e-pub-uasdk/supernova}"

if [ "${1:-}" = "down" ]; then
  docker compose -f "$HERE/compose.yaml" down
  exit 0
fi

if [ ! -x "$SUPERNOVA_TREE/build/bin/OpcUaServer" ]; then
  echo "SUPERNOVA_TREE ($SUPERNOVA_TREE) has no built OpcUaServer."
  echo "Point SUPERNOVA_TREE at a built supernova publisher tree (see interop/README note)."
  exit 2
fi

echo "== building hypernova image and starting the topology =="
docker compose -f "$HERE/compose.yaml" up -d --build --quiet-pull 2>&1 | tail -2

echo "== registering the publication (both networks) =="
for i in $(seq 1 20); do
  curl -sf http://localhost:4850/api/health >/dev/null && break
  sleep 1
done
curl -sf -X PUT http://localhost:4850/api/publications/site/area1/demo/env \
  -H 'Content-Type: application/json' -d '{
    "address": "opc.udp://239.0.0.5:4840",
    "endpoints": {"officenet": "opc.udp://172.29.0.20:24840"},
    "publisherId": 42, "publisherIdType": "UINT16",
    "writerGroupId": 100, "dataSetWriterId": 1,
    "description": "demo: supernova field server, relayed to officenet",
    "fields": [{"name": "counter", "type": "INT32"},
               {"name": "temperature", "type": "DOUBLE"},
               {"name": "label", "type": "STRING"}],
    "replace": true}' >/dev/null || { echo "registration failed"; exit 1; }
echo "registered site/area1/demo/env"

echo "== letting data flow for 6 seconds =="
sleep 6

python3 "$HERE/verify_demo.py" || {
  echo; echo "DEMO FAILED — container states:"; docker compose -f "$HERE/compose.yaml" ps; exit 1; }

cat <<EOF

DEMO UP — explore it:
  browser (live values, copy-paste snippets):  http://localhost:4850
  relay counters (the pinhole, auditable):     http://localhost:4860/api/health
  consumer on 'officenet' printing by name:          docker compose -f demo/compose.yaml logs -f consumer
Tear down with: demo/run_demo.sh down
EOF
