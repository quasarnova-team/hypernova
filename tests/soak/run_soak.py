#!/usr/bin/env python3
"""Soak: registry + N publishers (some signed) + subscriber + churn, at rates
that cross the 16-bit sequence wrap, sampling RSS and fds throughout.

    .venv/bin/python tests/soak/run_soak.py --minutes 60

Exit 0 only if: no process died, no memory growth beyond tolerance, no fd
leak, subscriber saw < 0.5% loss, registry stayed responsive, sequence wraps
crossed without phantom loss. Results are printed as a JSON report.
"""

import argparse
import json
import os
import resource
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hypernova.client import Publisher, Subscriber  # noqa: E402

REGISTRY_PORT = 4867
GROUP = "opc.udp://239.10.7.{n}:24867"
KEY = bytes(range(32))


def rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / (1024 * 1024 if sys.platform == "darwin" else 1024)


def open_fds() -> int:
    import glob
    if sys.platform == "darwin":
        out = subprocess.run(["lsof", "-p", str(os.getpid())], capture_output=True, text=True)
        return max(len(out.stdout.splitlines()) - 1, 0)
    return len(glob.glob("/proc/self/fd/*"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--publishers", type=int, default=10)
    parser.add_argument("--rate-hz", type=float, default=50.0)
    args = parser.parse_args()

    registry = subprocess.Popen(
        [sys.executable, "-m", "hypernova.cli", "registry",
         "--port", str(REGISTRY_PORT), "--store", ""],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[2]))
    time.sleep(2)
    registry_url = f"http://localhost:{REGISTRY_PORT}"

    publishers = []
    for index in range(args.publishers):
        publishers.append(Publisher(
            f"soak/stream{index}",
            fields={"counter": "INT32", "level": "DOUBLE", "tags": "DOUBLE[]"},
            address=GROUP.format(n=index % 4 + 1),
            publisher_id=100 + index, writer_group_id=1, dataset_writer_id=1,
            registry=registry_url, interface="127.0.0.1",
            sign_key=KEY if index % 3 == 0 else None))

    subscriber = Subscriber("soak/stream0", registry=registry_url,
                            interface="127.0.0.1", verify_key=KEY)
    subscriber.start()

    stop = threading.Event()
    sent = [0] * args.publishers

    def publish_loop(index: int) -> None:
        publisher = publishers[index]
        interval = 1.0 / args.rate_hz
        while not stop.is_set():
            publisher.send(counter=sent[index] & 0x7FFFFFFF,
                           level=sent[index] * 0.5, tags=[1.5, 2.5, 3.5])
            sent[index] += 1
            time.sleep(interval)

    threads = [threading.Thread(target=publish_loop, args=(i,), daemon=True)
               for i in range(args.publishers)]
    for thread in threads:
        thread.start()

    received = 0
    last_sequence = None
    phantom_loss = 0
    gaps = 0

    def consume() -> None:
        nonlocal received, last_sequence, phantom_loss, gaps
        while not stop.is_set():
            try:
                update = subscriber.get(timeout=1.0)
            except TimeoutError:
                continue
            received += 1
            seq = update.sequence_number
            if last_sequence is not None and seq is not None:
                gap = (seq - last_sequence) & 0xFFFF
                if gap == 0:
                    phantom_loss += 1
                elif gap > 1 and gap < 0x8000:
                    gaps += gap - 1
            last_sequence = seq

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()

    samples = []
    deadline = time.time() + args.minutes * 60
    baseline_rss = None
    while time.time() < deadline:
        time.sleep(30)
        health = json.loads(urllib.request.urlopen(
            f"{registry_url}/api/health", timeout=10).read())
        churn = Publisher("soak/churn", fields={"x": "INT32"},
                          address=GROUP.format(n=9), publisher_id=990,
                          writer_group_id=9, dataset_writer_id=9,
                          registry=registry_url, interface="127.0.0.1")
        churn.send(x=1)
        churn.close()
        sample = {"t": round(time.time() - (deadline - args.minutes * 60), 1),
                  "rssMB": round(rss_mb(), 1), "fds": open_fds(),
                  "received": received, "sent0": sent[0],
                  "registryPublications": health["publications"],
                  "registryUndecodable": health["undecodableDatagrams"]}
        if baseline_rss is None and sample["t"] > 120:
            baseline_rss = sample["rssMB"]
        samples.append(sample)
        print(json.dumps(sample), flush=True)

    stop.set()
    time.sleep(2)

    failures = []
    if registry.poll() is not None:
        failures.append("registry process died")
    loss = 1 - received / max(sent[0], 1)
    if loss > 0.005:
        failures.append(f"subscriber loss {loss:.2%} (limit 0.5%)")
    if phantom_loss:
        failures.append(f"{phantom_loss} duplicate/phantom sequence events")
    if sent[0] < 65536:
        failures.append(f"sequence wrap never crossed ({sent[0]} < 65536) — run longer")
    if baseline_rss and samples and samples[-1]["rssMB"] > baseline_rss * 1.5 + 30:
        failures.append(f"RSS grew {baseline_rss} -> {samples[-1]['rssMB']} MB")
    if len(samples) >= 4 and samples[-1]["fds"] > samples[1]["fds"] + 20:
        failures.append(f"fd leak: {samples[1]['fds']} -> {samples[-1]['fds']}")

    report = {"minutes": args.minutes, "publishers": args.publishers,
              "rateHz": args.rate_hz, "sentPerPublisher": sent[0],
              "received": received, "lossFraction": round(loss, 5),
              "gapsObserved": gaps, "sequenceWraps": sent[0] // 65536,
              "finalRssMB": samples[-1]["rssMB"] if samples else None,
              "finalFds": samples[-1]["fds"] if samples else None,
              "failures": failures}
    print("SOAK REPORT " + json.dumps(report), flush=True)

    registry.terminate()
    subscriber.stop()
    for publisher in publishers:
        publisher.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
