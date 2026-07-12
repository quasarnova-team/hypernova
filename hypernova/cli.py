"""The hypernova command line.

    hypernova registry [--port 4850] [--store registry.json]
    hypernova relay <config.json>
    hypernova browse [--registry URL]
    hypernova sub <name> [--registry URL] [--count N]
    hypernova pub <name> --address A --publisher-id P --writer-group-id W \
                  --dataset-writer-id D --field name=TYPE... --value name=V... \
                  [--interval SECONDS] [--count N]
    hypernova register <name> --address ... (register without publishing)
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
    run(host=args.bind, port=args.port, store_path=args.store)
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
    values = {}
    for item in items or []:
        name, _, raw = item.partition("=")
        if name not in field_types:
            raise SystemExit(f"--value {name!r} is not a declared field")
        builtin = BuiltinType[field_types[name]]
        if builtin.name in ("FLOAT", "DOUBLE"):
            values[name] = float(raw)
        elif builtin.name == "STRING":
            values[name] = raw
        elif builtin.name == "BOOLEAN":
            values[name] = raw.lower() in ("1", "true", "yes")
        else:
            values[name] = int(raw)
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
    with Publisher(args.name, fields=fields, address=args.address,
                   publisher_id=args.publisher_id,
                   writer_group_id=args.writer_group_id,
                   dataset_writer_id=args.dataset_writer_id,
                   registry=args.registry, description=args.description) as publisher:
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
    from hypernova.client import Subscriber
    with Subscriber(args.name, registry=args.registry, network=args.network) as subscriber:
        received = 0
        for update in subscriber.updates(timeout=args.timeout):
            fields = "  ".join(
                f"{name}={fv.value!r}{'' if fv.is_good else ' (BAD)'}"
                for name, fv in update.values.items())
            stamp = time.strftime("%H:%M:%S", time.localtime(update.received_at))
            print(f"{stamp}  {update.name}  seq={update.sequence_number}  {fields}")
            received += 1
            if args.count and received >= args.count:
                break
        if received == 0:
            print(f"no data for {args.name!r} within {args.timeout}s", file=sys.stderr)
            return 1
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
    p.add_argument("--registry")
    p.add_argument("--description", default="")
    p.set_defaults(func=_cmd_pub)

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
