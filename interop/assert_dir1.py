#!/usr/bin/env python3
"""Direction 1: the hypernova CLI subscriber must have printed ramping
supernova values, all fields resolved by name."""
import re
import sys

LINE = re.compile(r"seq=(\d+)\s+counter=(-?\d+)\s+temperature=([0-9.e+-]+)\s+label='supernova'")


def main(path):
    counters = []
    with open(path) as handle:
        for line in handle:
            match = LINE.search(line)
            if match:
                counters.append(int(match.group(2)))
                temperature = float(match.group(3))
                counter = int(match.group(2))
                if abs(temperature - counter * 0.5) > 0.5 + 1e-9:
                    print(f"ASSERTION FAILED: temperature {temperature} vs counter {counter}")
                    return 1
    if len(counters) < 10:
        print(f"ASSERTION FAILED: only {len(counters)} well-formed updates")
        return 1
    if not all(b >= a for a, b in zip(counters, counters[1:])):
        print("ASSERTION FAILED: counter not monotonic")
        return 1
    print(f"hypernova subscriber: {len(counters)} updates, counter {counters[0]} -> {counters[-1]}, "
          "temperature ratio and label verified on every line")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
