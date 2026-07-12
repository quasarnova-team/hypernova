#!/usr/bin/env python3
"""Self-verification of the running demo: every leg of the topology must be
demonstrably alive — registry live path, relay counters, and the consumer on
the far network actually printing named values."""

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=5).read())


def main():
    failures = []

    detail = get("http://localhost:4850/api/publications/site/area1/demo/env")
    live = detail["live"]
    if live["stale"]:
        failures.append("registry: stream is stale on atcn")
    elif live["rateHz"] < 5:
        failures.append(f"registry: unexpected rate {live['rateHz']} Hz")
    values = {v["name"]: v for v in detail.get("values", [])}
    if "counter" not in values:
        failures.append("registry: no live counter value")
    else:
        print(f"registry (atcn):  {live['messages']} msgs @ {live['rateHz']} Hz, "
              f"counter={values['counter']['value']}, label={values['label']['value']!r}")

    relay = get("http://localhost:4860/api/health")
    route = relay["routes"][0]
    if route["datagrams"] < 10:
        failures.append(f"relay: only {route['datagrams']} datagrams forwarded")
    else:
        print(f"relay (pinhole):  {route['datagrams']} datagrams -> {route['to'][0]}")

    logs = subprocess.run(
        ["docker", "compose", "-f", str(HERE / "compose.yaml"), "logs", "--no-log-prefix", "consumer"],
        capture_output=True, text=True).stdout
    counters = [int(m.group(1)) for m in re.finditer(r"counter=(\d+)", logs)]
    if len(counters) < 10:
        failures.append(f"consumer: only {len(counters)} updates on gpn")
    elif counters[-1] <= counters[0]:
        failures.append("consumer: counter not advancing on gpn")
    else:
        print(f"consumer (gpn):   {len(counters)} updates by name, "
              f"counter {counters[0]} -> {counters[-1]}")

    if failures:
        for failure in failures:
            print("FAIL:", failure)
        return 1
    print("\nDEMO VERIFIED: field server -> multicast -> [registry watching] "
          "-> relay pinhole -> consumer on the other network, all by name.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
