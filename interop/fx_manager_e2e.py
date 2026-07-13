#!/usr/bin/env python3
"""FX end-to-end, driven entirely through hypernova's connection-manager API.

Unlike a hand-rolled asyncua manager, this proves the *hypernova.fx* surface
against two live supernova servers: describe -> link (with rollback safety) ->
live status -> the value lands in the subscriber's address space -> unlink ->
Initial -> reuse the name. A plain asyncua client is used only to read the
subscriber's cache variable back, to prove data actually flowed on the wire.

  fx_manager_e2e.py --pub opc.tcp://host:port --sub opc.tcp://host:port \\
                    --data-address opc.udp://<sub-ip>:14840
"""
import argparse
import asyncio
import sys

from asyncua import Client

from hypernova import fx


def say(line):
    print(line, flush=True)


async def scenario(pub_url, sub_url, data_address, listen_address):
    failures = []

    def check(condition, label):
        say(("  ok   " if condition else "  FAIL ") + label)
        if not condition:
            failures.append(label)

    async with fx.connect(pub_url) as pub, fx.connect(sub_url) as sub:
        # 1 — self-description
        pub_component = await pub.describe()
        sub_component = await sub.describe()
        check(pub_component.entity("control") is not None, "publisher describes entity 'control'")
        check(pub_component.entity("control").dataset("env", "output") is not None,
              "publisher offers output dataset 'env'")
        check(sub_component.entity("control").dataset("setpoints", "input") is not None,
              "subscriber offers input dataset 'setpoints'")

        # 2 — pre-validation refuses an illegal wiring before touching a server
        try:
            await fx.link(pub.publisher("control", "nope"),
                          sub.subscriber("control", "setpoints"), address=data_address)
            check(False, "illegal dataset rejected before establish")
        except fx.FxError as error:
            check("available output datasets: env" in str(error),
                  "illegal dataset rejected with a teaching message")

        # 3 — wire it (atomic; rolls back if the subscriber side fails)
        link = await fx.link(
            pub.publisher("control", "env"),
            sub.subscriber("control", "setpoints"),
            address=data_address, listen_address=listen_address, name="link")
        check(link.coordinates.writer_group_id == 200, "publisher coordinates carry writerGroupId 200")

        state = await link.status()
        check(state["publisher"] is not None and state["publisher"].is_operational,
              "publisher endpoint Operational immediately")
        check(state["subscriber"] is not None and state["subscriber"].status_name == "PreOperational",
              "subscriber endpoint PreOperational before data")

        # 4 — data lands, subscriber goes Operational
        try:
            await link.wait_operational(timeout=15)
            operational = True
        except TimeoutError as error:
            say(f"       {error}")
            operational = False
        check(operational, "both endpoints Operational after first data")

        landed = False
        async with Client(url=sub_url) as reader:
            for _ in range(50):
                value = await reader.get_node("ns=2;s=FX1.setpoint").read_value()
                if value == 21.5:
                    landed = True
                    break
                await asyncio.sleep(0.1)
        check(landed, "published value landed in the subscriber's address space (21.5)")

        # 5 — undo, both sides return to Initial
        await fx.unlink(link)
        after = await link.status()
        check(after["publisher"].status_name == "Initial", "publisher back to Initial after unlink")
        check(after["subscriber"].status_name == "Initial", "subscriber back to Initial after unlink")

        # 6 — unlink is idempotent
        await fx.unlink(link)
        check(True, "second unlink is a no-op (idempotent)")

        # 7 — the endpoint name is reusable
        relink = await fx.link(
            pub.publisher("control", "env"),
            sub.subscriber("control", "setpoints"),
            address=data_address, listen_address=listen_address, name="link")
        check((await relink.status())["publisher"].is_operational,
              "reused endpoint Operational again")
        await fx.unlink(relink)

    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pub", required=True)
    parser.add_argument("--sub", required=True)
    parser.add_argument("--data-address", required=True,
                        help="opc.udp:// the publisher sends to (the subscriber's address)")
    parser.add_argument("--listen-address", default=None,
                        help="opc.udp:// the subscriber binds (default 0.0.0.0 on the data port)")
    args = parser.parse_args()
    listen = args.listen_address
    if listen is None:
        listen = f"opc.udp://0.0.0.0:{args.data_address.rsplit(':', 1)[1]}"

    failures = asyncio.run(
        asyncio.wait_for(scenario(args.pub, args.sub, args.data_address, listen), timeout=120))
    if failures:
        say(f"E2E FAIL ({len(failures)} failed check(s))")
        return 1
    say("E2E PASS (describe -> link -> Operational -> data landed -> unlink -> Initial -> reuse)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
