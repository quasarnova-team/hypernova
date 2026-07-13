#!/bin/bash
# The registry browser, profiting from FX: two supernova FX servers and the
# hypernova registry on one docker bridge; one multicast `fx link --register`;
# the registry HEARS the resulting stream and the browser shows it live and
# FX-marked. Verifies live state over the API and screenshots the real UI.
#
#   run_fx_browser.sh            # needs docker + the hypernova:fxdemo image
#   build the image first:  docker build -f demo/Dockerfile -t hypernova:fxdemo .
#
# Multicast works within a single docker bridge network; the registry container
# joins the group and receives exactly like any Part 14 subscriber.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WT="$(cd "$HERE/.." && pwd)"
BENCH="${FX_BENCH:-$HOME/code/quasarnova-team/.supernova-bench/fx-clean}"
PY="${FX_PY:-$HOME/code/quasarnova-team/hypernova/.venv/bin/python}"
CHROME="${FX_CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
IMAGE="ghcr.io/quasar-team/quasar-uasdk:alma10"
NET="fxmc-net"; GROUP="opc.udp://239.0.0.7:14840"; NAME="atlas/dcs/fx/env"
OUT="$WT/interop/artifacts"; mkdir -p "$OUT"
export PYTHONPATH="$WT"; cd "$WT"
HN(){ "$PY" -m hypernova.cli "$@"; }

cleanup(){ docker rm -f fxmc-pub fxmc-sub fxmc-reg >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker image inspect hypernova:fxdemo >/dev/null 2>&1 \
  || { echo "FAIL: build the image first — docker build -f demo/Dockerfile -t hypernova:fxdemo ."; exit 1; }
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
cleanup

srv(){ docker run -d --rm --name "$1" --network "$NET" --platform linux/amd64 -p "$2:4841" \
  -v "$BENCH/cells/fx-o6/supernova:/cell/supernova" -w /cell/supernova/build/bin \
  "$IMAGE" ./OpcUaServer >/dev/null; }
srv fxmc-sub 48522; srv fxmc-pub 48521
docker run -d --rm --name fxmc-reg --network "$NET" -p 4850:4850 \
  hypernova:fxdemo registry --store '' --bind 0.0.0.0 >/dev/null

for i in $(seq 1 60); do
  nc -z localhost 48521 2>/dev/null && nc -z localhost 48522 2>/dev/null \
    && curl -sf http://localhost:4850/api/health >/dev/null 2>&1 && break
  sleep 1; [ "$i" = 60 ] && { echo "FAIL: services did not come up"; exit 1; }
done
sleep 2

echo "=== fx link --register on the multicast group $GROUP ==="
HN fx link opc.tcp://localhost:48521 control/env opc.tcp://localhost:48522 control/setpoints \
   --address "$GROUP" --name fxdemo --wait --register "$NAME" --registry http://localhost:4850 || exit 1

echo "=== poll the registry until it hears the multicast stream ==="
live=""
for i in $(seq 1 30); do
  curl -s "http://localhost:4850/api/publications/$NAME" > "$OUT/fx_browser_api.json"
  read -r stale rate msgs fxconn < <("$PY" - "$OUT/fx_browser_api.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print(d["live"]["stale"], round(d["live"]["rateHz"],1), d["live"]["messages"], d.get("fx",{}).get("connection","NONE"))
PY
)
  echo "  [$i] stale=$stale rate=${rate}Hz messages=$msgs fx=$fxconn"
  [ "$stale" = "False" ] && [ "$fxconn" = "fxdemo" ] && { live=1; break; }
  sleep 1
done

echo "=== screenshot the real browser UI ==="
HASH=$("$PY" -c "import urllib.parse;print(urllib.parse.quote('$NAME',safe=''))")
"$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1440,900 --force-device-scale-factor=2 --virtual-time-budget=6000 \
  --screenshot="$OUT/fx_browser.png" "http://localhost:4850/#$HASH" >/dev/null 2>&1 || true
curl -s http://localhost:4850/ > "$OUT/fx_browser_index.html"

if [ -n "$live" ]; then
  echo "PASS: registry heard the multicast FX stream live and marked it FX"
  echo "  evidence: $OUT/fx_browser.png, fx_browser_api.json, fx_browser_index.html"
else
  echo "FAIL: registry did not hear the multicast stream (see $OUT/fx_browser_api.json)"; exit 1
fi
