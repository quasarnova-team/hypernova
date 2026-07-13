#!/bin/bash
# Live FX end-to-end driven by hypernova's connection-manager API.
#   run_fx.sh <pub_backend> <sub_backend>      (backends: o6 | uasdk)
# Brings up two supernova FX servers as docker cells on one bridge network,
# runs interop/fx_manager_e2e.py as the connection manager over their opc.tcp
# endpoints, and verifies the whole loop. The data plane is unicast UDP from
# the publisher container to the subscriber container (docker-proof: no
# multicast assumption). Cleans up the containers it starts.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PUBB="${1:?pub backend (o6|uasdk)}"
SUBB="${2:?sub backend (o6|uasdk)}"
BENCH="${FX_BENCH:-$HOME/code/quasarnova-team/.supernova-bench/fx-clean}"
PY="${FX_PY:-$HOME/code/quasarnova-team/hypernova/.venv/bin/python}"
IMAGE="ghcr.io/quasar-team/quasar-uasdk:alma10"
NET="fxadopt-net"
PUBPORT=48521
SUBPORT=48522
NAME="fx-$PUBB-to-$SUBB"

for b in "$PUBB" "$SUBB"; do
  [ -x "$BENCH/cells/fx-$b/supernova/build/bin/OpcUaServer" ] \
    || { echo "$NAME: FAIL (cell fx-$b not built at $BENCH)"; exit 1; }
done

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
docker rm -f fxadopt-pub fxadopt-sub >/dev/null 2>&1 || true

start() { # name backend hostport
  docker run -d --rm --name "$1" --network "$NET" --platform linux/amd64 \
    -p "$3:4841" \
    -v "$BENCH/cells/fx-$2/supernova:/cell/supernova" \
    -w /cell/supernova/build/bin \
    "$IMAGE" ./OpcUaServer >/dev/null
}

cleanup() { docker rm -f fxadopt-pub fxadopt-sub >/dev/null 2>&1 || true; }
trap cleanup EXIT

start fxadopt-sub "$SUBB" "$SUBPORT"
start fxadopt-pub "$PUBB" "$PUBPORT"

for i in $(seq 1 60); do
  if nc -z localhost "$PUBPORT" 2>/dev/null && nc -z localhost "$SUBPORT" 2>/dev/null; then break; fi
  sleep 1
  [ "$i" = 60 ] && { echo "$NAME: FAIL (servers did not come up)"; docker logs fxadopt-pub 2>&1 | tail -10; exit 1; }
done
sleep 2

SUBIP=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" fxadopt-sub)
[ -n "$SUBIP" ] || { echo "$NAME: FAIL (no subscriber IP)"; exit 1; }

echo "=== $NAME (data plane opc.udp://$SUBIP:14840) ==="
if "$PY" "$HERE/fx_manager_e2e.py" \
    --pub "opc.tcp://localhost:$PUBPORT" --sub "opc.tcp://localhost:$SUBPORT" \
    --data-address "opc.udp://$SUBIP:14840"; then
  echo "$NAME: PASS"
else
  echo "$NAME: FAIL"
  echo "--- pub log tail ---"; docker logs fxadopt-pub 2>&1 | tail -15
  echo "--- sub log tail ---"; docker logs fxadopt-sub 2>&1 | tail -15
  exit 1
fi
