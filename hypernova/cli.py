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


def _cmd_fx_connect(args) -> int:
    import asyncio
    import json as _json
    import logging
    from hypernova.fx import connect_pair
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(connect_pair(
        publisher_url=args.publisher, publisher_component=args.pub_component,
        publisher_entity=args.pub_entity, publisher_dataset=args.pub_dataset,
        subscriber_url=args.subscriber, subscriber_component=args.sub_component,
        subscriber_entity=args.sub_entity, subscriber_dataset=args.sub_dataset,
        address=args.address, interval=args.interval, name=args.name, ttl=args.ttl,
        register=args.register, register_as=args.register_as, network=args.network))
    print(_json.dumps(result, indent=2))
    return 0


def _cmd_fx_status(args) -> int:
    import asyncio
    from hypernova.fx import endpoints
    found = asyncio.run(endpoints(args.url, component=args.component))
    if not found:
        print("no connection endpoints")
        return 0
    for record in found:
        line = (f"{record['component']}/{record['entity']}/{record['connection']}: "
                f"{record.get('status', '?')}")
        extras = {k: v for k, v in record.items()
                  if k not in ("component", "entity", "connection", "status")}
        if extras:
            line += "  " + "  ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        print(line)
    return 0


def _cmd_fx_close(args) -> int:
    import asyncio
    from hypernova.fx import close
    detail = asyncio.run(close(args.url, component=args.component,
                               connection_id=args.connection_id))
    print(detail.get("status", "closed"))
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

    p = sub.add_parser("fx", help="OPC UA FX connection manager: wire two FX servers "
                                  "together (needs the [bridge] extra)")
    fx_sub = p.add_subparsers(dest="fx_command", required=True)

    q = fx_sub.add_parser("connect", help="establish publisher side, then subscriber "
                                          "side with the publisher's coordinates")
    q.add_argument("--publisher", required=True, metavar="OPC_TCP_URL")
    q.add_argument("--pub-entity", required=True)
    q.add_argument("--pub-dataset", required=True)
    q.add_argument("--pub-component", help="AutomationComponent name (default: discover)")
    q.add_argument("--subscriber", required=True, metavar="OPC_TCP_URL")
    q.add_argument("--sub-entity", required=True)
    q.add_argument("--sub-dataset", required=True)
    q.add_argument("--sub-component", help="AutomationComponent name (default: discover)")
    q.add_argument("--address", required=True, help="opc.udp:// data-plane address")
    q.add_argument("--interval", type=float, help="publishing interval in ms")
    q.add_argument("--name", help="connection name (default: server-assigned)")
    q.add_argument("--ttl", type=int)
    q.add_argument("--register", metavar="REGISTRY_URL",
                   help="also register the stream in a hypernova registry")
    q.add_argument("--register-as", metavar="NAME", help="publication name for --register")
    q.add_argument("--network", help="network label for the registry endpoint")
    q.set_defaults(func=_cmd_fx_connect)

    q = fx_sub.add_parser("status", help="list the server's connection endpoints")
    q.add_argument("url", metavar="OPC_TCP_URL")
    q.add_argument("--component", help="AutomationComponent name (default: discover)")
    q.set_defaults(func=_cmd_fx_status)

    q = fx_sub.add_parser("close", help="close a connection by id")
    q.add_argument("url", metavar="OPC_TCP_URL")
    q.add_argument("connection_id")
    q.add_argument("--component", help="AutomationComponent name (default: discover)")
    q.set_defaults(func=_cmd_fx_close)

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
