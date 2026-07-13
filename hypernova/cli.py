"""The hypernova command line.

    hypernova registry [--port 4850] [--store registry.json]
    hypernova relay <config.json>
    hypernova browse [--registry URL]
    hypernova sub <name> [--registry URL] [--count N]
    hypernova pub <name> --address A --publisher-id P --writer-group-id W \
                  --dataset-writer-id D --field name=TYPE... --value name=V... \
                  [--interval SECONDS] [--count N]
    hypernova register <name> --address ... (register without publishing)
    hypernova fx describe <opc.tcp-url>
    hypernova fx link <pub-url> <entity/dataset> <sub-url> <entity/dataset> --address <opc.udp>
    hypernova fx status <opc.tcp-url>...
    hypernova fx unlink <connection> <opc.tcp-url>...
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def _cmd_registry(args) -> int:
    from hypernova.registry.service import run
    print(f"hypernova-registry: http://{args.bind}:{args.port} "
          f"(store: {args.store or 'in-memory only'})")
    run(host=args.bind, port=args.port, store_path=args.store, mirror_of=args.mirror_of)
    return 0


def _cmd_relay(args) -> int:
    from hypernova.relay import run
    run(args.config)
    return 0


def _cmd_browse(args) -> int:
    from hypernova.client import _registry_call, default_registry_url
    registry = args.registry or default_registry_url()
    publications = _registry_call("GET", f"{registry}/api/publications")
    if not publications:
        print("no publications registered")
        return 0
    width = max(len(p["name"]) for p in publications)
    for p in publications:
        state = "stale" if p["live"]["stale"] else f"{p['live']['rateHz']:g} Hz"
        fields = ", ".join(f["name"] for f in p["fields"])
        print(f"{p['name']:<{width}}  {state:>9}  {p['address']}  [{fields}]")
    return 0


def _parse_fields(items) -> dict:
    fields = {}
    for item in items or []:
        name, _, type_name = item.partition("=")
        if not type_name:
            raise SystemExit(f"--field wants name=TYPE, got {item!r}")
        fields[name] = type_name.upper()
    return fields


def _parse_values(items, field_types) -> dict:
    from hypernova.wire import BuiltinType

    def convert(builtin, raw):
        if builtin.name in ("FLOAT", "DOUBLE"):
            return float(raw)
        if builtin.name == "STRING":
            return raw
        if builtin.name == "BOOLEAN":
            return raw.lower() in ("1", "true", "yes")
        return int(raw)

    values = {}
    for item in items or []:
        name, _, raw = item.partition("=")
        if name not in field_types:
            raise SystemExit(f"--value {name!r} is not a declared field")
        type_name = field_types[name]
        builtin = BuiltinType[type_name.removesuffix("[]")]
        if type_name.endswith("[]"):
            values[name] = [convert(builtin, part) for part in raw.split(",")] if raw else []
        else:
            values[name] = convert(builtin, raw)
    return values


def _cmd_pub(args) -> int:
    from hypernova.client import Publisher
    fields = _parse_fields(args.field)
    if not fields:
        raise SystemExit("declare at least one --field name=TYPE")
    values = _parse_values(args.value, fields)
    missing = set(fields) - set(values)
    if missing:
        raise SystemExit(f"--value missing for {sorted(missing)}")
    sign_key = None
    if args.sign_key_file:
        from hypernova.keys import load_key
        sign_key = load_key(args.sign_key_file)
    with Publisher(args.name, fields=fields, address=args.address,
                   publisher_id=args.publisher_id,
                   writer_group_id=args.writer_group_id,
                   dataset_writer_id=args.dataset_writer_id,
                   registry=args.registry, description=args.description,
                   interface=args.interface, sign_key=sign_key) as publisher:
        if not publisher.registered:
            print("note: registry unreachable — publishing unregistered", file=sys.stderr)
        sent = 0
        while args.count == 0 or sent < args.count:
            publisher.send(**values)
            sent += 1
            if args.ramp:
                for key, value in list(values.items()):
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        values[key] = value + 1
            if args.count == 0 or sent < args.count:
                time.sleep(args.interval)
        print(f"published {sent} sample(s) of {args.name}")
    return 0


def _cmd_sub(args) -> int:
    from hypernova.client import RegistryError, Subscriber
    deadline = time.time() + args.timeout
    while True:
        try:
            verify_key = None
            if args.verify_key_file:
                from hypernova.keys import load_key
                verify_key = load_key(args.verify_key_file)
            subscriber = Subscriber(args.name, registry=args.registry,
                                    network=args.network, interface=args.interface,
                                    verify_key=verify_key,
                                    require_signed=args.require_signed)
            break
        except RegistryError as error:
            if time.time() >= deadline:
                raise
            print(f"waiting for {args.name!r} ({error})", file=sys.stderr)
            time.sleep(2)
    with subscriber:
        received = 0
        for update in subscriber.updates(timeout=args.timeout):
            fields = "  ".join(
                f"{name}={fv.value!r}{'' if fv.is_good else ' (BAD)'}"
                for name, fv in update.values.items())
            stamp = time.strftime("%H:%M:%S", time.localtime(update.received_at))
            print(f"{stamp}  {update.name}  seq={update.sequence_number}  {fields}", flush=True)
            received += 1
            if args.count and received >= args.count:
                break
        if received == 0:
            print(f"no data for {args.name!r} within {args.timeout}s", file=sys.stderr)
            return 1
    return 0


def _cmd_bridge_opcua(args) -> int:
    import asyncio
    import logging
    from hypernova.bridge_opcua import run_bridge
    logging.basicConfig(level=logging.INFO)
    verify_key = None
    if args.verify_key_file:
        from hypernova.keys import load_key
        verify_key = load_key(args.verify_key_file)
    asyncio.run(run_bridge(args.names, endpoint=args.endpoint, registry=args.registry,
                           network=args.network, verify_key=verify_key))
    return 0


def _cmd_bridge_dip(args) -> int:
    from hypernova.bridge_dip import run
    run(args.config)
    return 0


def _cmd_register(args) -> int:
    from hypernova.client import _registry_call, default_registry_url
    fields = _parse_fields(args.field)
    if not fields:
        raise SystemExit("declare at least one --field name=TYPE")
    registry = args.registry or default_registry_url()
    result = _registry_call("PUT", f"{registry}/api/publications/{args.name}", {
        "address": args.address,
        "publisherId": args.publisher_id,
        "publisherIdType": "UINT16",
        "writerGroupId": args.writer_group_id,
        "dataSetWriterId": args.dataset_writer_id,
        "description": args.description,
        "endpoints": dict(e.split("=", 1) for e in args.endpoint or []),
        "fields": [{"name": n, "type": t} for n, t in fields.items()],
        "replace": args.replace,
    })
    print(f"registered {result['name']} at {result['address']}")
    return 0


def _split_dataset_ref(ref: str) -> tuple[str, str]:
    entity, _, dataset = ref.partition("/")
    if not entity or not dataset:
        raise SystemExit(f"expected entity/dataset (e.g. control/env), got {ref!r}")
    return entity, dataset


def _print_endpoints(endpoints, indent="  ") -> None:
    if not endpoints:
        print(f"{indent}(no connections)")
        return
    width = max(len(e.name) for e in endpoints)
    for endpoint in endpoints:
        print(f"{indent}{endpoint.name:<{width}}  {endpoint.status_name:<14}  "
              f"{endpoint.dataset}  {endpoint.address}")


def _cmd_fx_describe(args) -> int:
    import asyncio
    from hypernova import fx

    async def run():
        async with fx.connect(args.url) as server:
            print(await server.describe())
    asyncio.run(run())
    return 0


# Distinct nonzero exit codes so scripts can tell these apart from a hard error (1).
FX_EXIT_NOT_OPERATIONAL = 3   # link established but not Operational within --timeout
FX_EXIT_REGISTRY_FAILED = 4   # link established but naming it in the registry failed
FX_EXIT_SWEEP_FAILED = 5      # one or more servers in a status/unlink sweep failed


def _cmd_fx_link(args) -> int:
    import asyncio
    from hypernova import fx
    from hypernova.client import RegistryError, _registry_call, _registry_urls

    pub_entity, pub_dataset = _split_dataset_ref(args.publisher_dataset)
    sub_entity, sub_dataset = _split_dataset_ref(args.subscriber_dataset)

    async def run() -> int:
        not_operational = False
        registry_failed = False
        async with fx.connect(args.pub_url) as a, fx.connect(args.sub_url) as b:
            link = await fx.link(
                a.publisher(pub_entity, pub_dataset),
                b.subscriber(sub_entity, sub_dataset),
                address=args.address, listen_address=args.listen_address,
                name=args.name, publishing_interval_ms=args.interval, ttl=args.ttl)
            print(f"linked {args.pub_url} {pub_entity}/{pub_dataset}  ->  "
                  f"{args.sub_url} {sub_entity}/{sub_dataset}")
            print(f"  connection: {link.name}")
            if args.wait:
                try:
                    await link.wait_operational(timeout=args.timeout)
                except TimeoutError as error:
                    print(f"  note: {error}", file=sys.stderr)
                    not_operational = True
            state = await link.status()
            for role in ("publisher", "subscriber"):
                endpoint = state[role]
                print(f"  {role:<10} {endpoint.status_name if endpoint else 'missing'}")
            # The link is up: always give the undo command before anything that
            # might fail, so it is printed even if naming the stream fails.
            print(f"  undo: hypernova fx unlink {link.name} {args.pub_url} {args.sub_url}")
            if args.register is not None:
                registry_name = args.register or link.name
                try:
                    payload = await fx.registry_payload(link, name=registry_name)
                    registries = _registry_urls(args.registry)
                    failures = []
                    for registry in registries:
                        try:
                            await asyncio.to_thread(
                                lambda r=registry: _registry_call(
                                    "PUT", f"{r}/api/publications/{registry_name}", payload))
                        except RegistryError as error:
                            failures.append(f"{registry}: {error}")
                    if failures and len(failures) == len(registries):
                        raise RegistryError("; ".join(failures))
                    print(f"  registered stream {registry_name!r} in the registry "
                          f"({len(registries) - len(failures)}/{len(registries)}) — "
                          "browsable and subscribable by name")
                    if failures:
                        print(f"  (some registries failed: {'; '.join(failures)})", file=sys.stderr)
                except (RegistryError, fx.FxError) as error:
                    # the wiring is live and undoable; only the naming failed
                    print(f"  link established; registry naming failed: {error}", file=sys.stderr)
                    registry_failed = True
        # operational state trumps naming: a not-Operational link (3) outranks a
        # registry-naming failure (4) when both happen in one command.
        if not_operational:
            return FX_EXIT_NOT_OPERATIONAL
        if registry_failed:
            return FX_EXIT_REGISTRY_FAILED
        return 0

    return asyncio.run(run())


def _cmd_fx_status(args) -> int:
    import asyncio
    from hypernova import fx

    async def run() -> int:
        failed = False
        for url in args.urls:
            print(url)
            try:
                async with fx.connect(url) as server:
                    if args.entity is not None:
                        component = await server.describe()
                        if component.entity(args.entity) is None:
                            offered = ", ".join(e.name for e in component.entities) or "(none)"
                            print(f"  no functional entity {args.entity!r}; it offers: {offered}")
                            failed = True
                            continue
                    _print_endpoints(await server.endpoints(args.entity))
            except Exception as error:  # noqa: BLE001 — one bad server must not abort the sweep
                print(f"  unreachable: {error}")
                failed = True
        return FX_EXIT_SWEEP_FAILED if failed else 0

    return asyncio.run(run())


def _cmd_fx_unlink(args) -> int:
    import asyncio
    from hypernova import fx

    async def run() -> int:
        failed = False
        for url in args.urls:
            try:
                async with fx.connect(url) as server:
                    try:
                        await server.close_connection(args.connection)
                        print(f"{url}: closed {args.connection!r}")
                    except fx.FxRefused as error:
                        # confirm "already closed" by reading the endpoint back,
                        # not by string-matching the server's refusal prose
                        endpoint = await server.find_endpoint(args.connection)
                        if endpoint is None or endpoint.status == 0:
                            print(f"{url}: {args.connection!r} already closed")
                        else:
                            print(f"{url}: refused ({error})")
                            failed = True
            except Exception as error:  # noqa: BLE001 — attempt every server, report the unreachable
                print(f"{url}: unreachable: {error}")
                failed = True
        return FX_EXIT_SWEEP_FAILED if failed else 0

    return asyncio.run(run())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hypernova",
        description="The next era of DIP — data interchange on OPC UA Pub/Sub.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("registry", help="run the registry + live browser")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=4850)
    p.add_argument("--store", default="registry.json",
                   help="JSON persistence file ('' for in-memory)")
    p.add_argument("--mirror-of", help="primary registry URL to converge from (secondary mode)")
    p.set_defaults(func=_cmd_registry)

    p = sub.add_parser("relay", help="run the boundary relay")
    p.add_argument("config", help="relay config JSON")
    p.set_defaults(func=_cmd_relay)

    p = sub.add_parser("browse", help="list publications and their live state")
    p.add_argument("--registry")
    p.set_defaults(func=_cmd_browse)

    p = sub.add_parser("sub", help="subscribe to a publication by name")
    p.add_argument("name")
    p.add_argument("--registry")
    p.add_argument("--network", help="ask the registry for this network's endpoint")
    p.add_argument("--count", type=int, default=0, help="stop after N updates (0 = forever)")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="give up after this many silent seconds")
    p.add_argument("--interface", help="local address to receive on (dual-homed hosts)")
    p.add_argument("--verify-key-file", help="hex key file; verifies frame signatures")
    p.add_argument("--require-signed", action="store_true",
                   help="reject unsigned frames (default when a key is given)")
    p.set_defaults(func=_cmd_sub)

    p = sub.add_parser("pub", help="publish samples (also registers the name)")
    p.add_argument("name")
    p.add_argument("--address", required=True)
    p.add_argument("--publisher-id", type=int, required=True)
    p.add_argument("--writer-group-id", type=int, required=True)
    p.add_argument("--dataset-writer-id", type=int, required=True)
    p.add_argument("--field", action="append", metavar="NAME=TYPE")
    p.add_argument("--value", action="append", metavar="NAME=VALUE")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--count", type=int, default=0, help="stop after N samples (0 = forever)")
    p.add_argument("--ramp", action="store_true", help="increment numeric values each sample")
    p.add_argument("--interface", help="local address for multicast egress (dual-homed hosts)")
    p.add_argument("--sign-key-file", help="hex key file; signs every frame")
    p.add_argument("--registry")
    p.add_argument("--description", default="")
    p.set_defaults(func=_cmd_pub)

    p = sub.add_parser("bridge-opcua", help="serve publications as a classic OPC UA "
                                            "server (WinCC OA-consumable)")
    p.add_argument("names", nargs="+", help="publication names to bridge")
    p.add_argument("--endpoint", default="opc.tcp://0.0.0.0:4840")
    p.add_argument("--registry")
    p.add_argument("--network")
    p.add_argument("--verify-key-file")
    p.set_defaults(func=_cmd_bridge_opcua)

    p = sub.add_parser("bridge-dip", help="republish DIP publications as hypernova "
                                          "streams (migration bridge; needs CERN DIP bindings)")
    p.add_argument("config", help="bridge config JSON")
    p.set_defaults(func=_cmd_bridge_dip)

    p = sub.add_parser("register", help="register a publication without publishing "
                                        "(e.g. for a supernova server)")
    p.add_argument("name")
    p.add_argument("--address", required=True)
    p.add_argument("--publisher-id", type=int, required=True)
    p.add_argument("--writer-group-id", type=int, required=True)
    p.add_argument("--dataset-writer-id", type=int, required=True)
    p.add_argument("--field", action="append", metavar="NAME=TYPE")
    p.add_argument("--endpoint", action="append", metavar="NETWORK=ADDRESS",
                   help="extra per-network endpoint (relayed copies)")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--registry")
    p.add_argument("--description", default="")
    p.set_defaults(func=_cmd_register)

    p = sub.add_parser("fx", help="OPC UA FX: wire two servers together at runtime")
    fx_sub = p.add_subparsers(dest="fx_command", required=True)

    q = fx_sub.add_parser("describe", help="show what an FX server offers "
                                           "(entities, datasets, live connections)")
    q.add_argument("url", help="opc.tcp:// endpoint of the FX server")
    q.set_defaults(func=_cmd_fx_describe)

    q = fx_sub.add_parser("link", help="wire a publisher dataset on A to a "
                                       "subscriber dataset on B")
    q.add_argument("pub_url", metavar="PUB_URL", help="opc.tcp:// of the publishing server")
    q.add_argument("publisher_dataset", metavar="ENTITY/DATASET", help="its output dataset")
    q.add_argument("sub_url", metavar="SUB_URL", help="opc.tcp:// of the subscribing server")
    q.add_argument("subscriber_dataset", metavar="ENTITY/DATASET", help="its input dataset")
    q.add_argument("--address", required=True,
                   help="opc.udp:// the publisher sends to (a multicast group, or the "
                        "subscriber's reachable address for unicast)")
    q.add_argument("--listen-address",
                   help="opc.udp:// the subscriber binds (default: --address; for unicast "
                        "use opc.udp://0.0.0.0:PORT)")
    q.add_argument("--name", help="connection name on both servers (default: <out>-to-<in>)")
    q.add_argument("--interval", type=int, metavar="MS",
                   help="publishing interval override in milliseconds")
    q.add_argument("--ttl", type=int, help="multicast time-to-live (0-255)")
    q.add_argument("--wait", action="store_true",
                   help="wait until both endpoints report Operational")
    q.add_argument("--timeout", type=float, default=10.0)
    q.add_argument("--register", nargs="?", const="", metavar="NAME",
                   help="also name the created stream in the registry (multicast); "
                        "NAME defaults to the connection name")
    q.add_argument("--registry")
    q.set_defaults(func=_cmd_fx_link)

    q = fx_sub.add_parser("status", help="live connection endpoints on FX server(s)")
    q.add_argument("urls", nargs="+", metavar="URL")
    q.add_argument("--entity", help="only this functional entity")
    q.set_defaults(func=_cmd_fx_status)

    q = fx_sub.add_parser("unlink", help="close a connection by name on FX server(s)")
    q.add_argument("connection", help="the connection name to close")
    q.add_argument("urls", nargs="+", metavar="URL", help="the server(s) holding it (both link sides)")
    q.set_defaults(func=_cmd_fx_unlink)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # surfaced as one clean line, not a traceback
        print(f"hypernova: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
