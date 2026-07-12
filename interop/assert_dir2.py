#!/usr/bin/env python3
"""Direction 2: supernova's MIRROR log lines must show the hypernova-published
ramp landing in the C++ server's address space."""
import re
import sys

MIRROR = re.compile(r"MIRROR value=(-?\d+) temperature=([0-9.e+-]+)")


def main(path):
    received = []
    with open(path) as handle:
        for line in handle:
            match = MIRROR.search(line)
            if match:
                value = int(match.group(1))
                if value != -1:
                    received.append((value, float(match.group(2))))
    if len(received) < 5:
        print(f"ASSERTION FAILED: only {len(received)} received samples in supernova")
        return 1
    values = [v for v, _ in received]
    if not all(b >= a for a, b in zip(values, values[1:])):
        print("ASSERTION FAILED: ramp not monotonic in supernova's address space")
        return 1
    if not all(5000 <= v < 5040 for v in values):
        print(f"ASSERTION FAILED: values outside the published ramp: {values[:5]}...")
        return 1
    for value, temperature in received:
        if abs(temperature - (2500 + (value - 5000))) > 1e-6:
            print(f"ASSERTION FAILED: temperature {temperature} vs mirror {value}")
            return 1
    print(f"supernova address space: {len(values)} samples, ramp {values[0]} -> {values[-1]}, "
          "both fields consistent (DataValue-encoded fields accepted by the C++ decoder)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
