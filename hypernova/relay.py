"""The boundary relay: the firewall port-exception turned into a small,
auditable process. Each route joins one stream on one side and re-emits the
raw datagrams to explicit targets on the other. Plain routes forward bytes
untouched. A *signing* route decodes and re-signs each frame with the
boundary key (preserving field encoding and keep-alive); it authenticates
the relay unless a ``verify_key_file`` is also set, in which case it first
authenticates the origin and drops anything that does not verify.

Config (JSON):

    {
      "routes": [
        {
          "name": "atlas/dcs/atca/crate1/env",
          "from": "opc.udp://239.10.0.1:14840",
          "to": ["opc.udp://10.147.0.5:24840"],
          "ttl": 1
        }
      ],
      "health_port": 4860
    }
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web

from hypernova import __version__, transport
from hypernova.keys import load_key
from hypernova.wire import WireError, decode_network_message, encode_network_message

__all__ = ["Route", "Relay", "load_config", "run"]


@dataclass
class Route:
    name: str
    source: str
    targets: list[str]
    ttl: int = 1
    #: sign frames on re-emit (path to a key file in the config). Signing
    #: routes re-encode — and therefore validate — every frame; raw routes
    #: forward bytes untouched.
    sign_key: bytes | None = None
    #: verify inbound signatures before re-signing (authenticate the origin).
    verify_key: bytes | None = None

    datagrams: int = 0
    bytes: int = 0
    invalid: int = 0
    last_forwarded: float | None = None
    _sends: list = field(default_factory=list)


def load_config(path: str | Path) -> tuple[list[Route], int | None]:
    data = json.loads(Path(path).read_text())
    routes = []
    for entry in data.get("routes", []):
        if not entry.get("to"):
            raise ValueError(f"route {entry.get('name', '?')!r} has no targets")
        sign_key = load_key(entry["sign_key_file"]) if entry.get("sign_key_file") else None
        verify_key = load_key(entry["verify_key_file"]) if entry.get("verify_key_file") else None
        routes.append(Route(
            name=entry.get("name", entry["from"]),
            source=entry["from"],
            targets=list(entry["to"]),
            ttl=int(entry.get("ttl", 1)),
            sign_key=sign_key,
            verify_key=verify_key,
        ))
    if not routes:
        raise ValueError("relay config declares no routes")
    return routes, data.get("health_port")


class Relay:
    def __init__(self, routes: list[Route]) -> None:
        self._routes = routes
        self._receivers: list = []
        self.started_at = time.time()

    @property
    def routes(self) -> list[Route]:
        return self._routes

    async def start(self) -> None:
        for route in self._routes:
            source_host, source_port = transport.parse_address(route.source)
            sends = []
            for target in route.targets:
                target_host, target_port = transport.parse_address(target)
                sock = transport.open_send_socket(target_host, ttl=route.ttl)
                sends.append((sock, (target_host, target_port)))
            route._sends = sends

            def forward(data: bytes, addr, route=route) -> None:
                if route.sign_key is not None:
                    try:
                        # verify_key set => authenticate the ORIGIN before re-signing
                        # (else boundary signing authenticates only the relay).
                        message = decode_network_message(
                            data, verify_key=route.verify_key,
                            require_signed=route.verify_key is not None)
                        # datavalue_fields=None preserves each message's original
                        # field encoding; keep-alive frames stay keep-alive.
                        data = encode_network_message(
                            message, datavalue_fields=None, sign_key=route.sign_key)
                    except WireError:
                        route.invalid += 1
                        return
                for sock, target in route._sends:
                    try:
                        sock.sendto(data, target)
                    except OSError:
                        pass
                route.datagrams += 1
                route.bytes += len(data)
                route.last_forwarded = time.time()

            receiver = await transport.create_receiver(source_host, source_port, forward)
            self._receivers.append(receiver)

    def stop(self) -> None:
        for receiver in self._receivers:
            receiver.close()
        self._receivers.clear()
        for route in self._routes:
            for sock, _ in route._sends:
                sock.close()
            route._sends = []

    def stats(self) -> dict:
        return {
            "service": "hypernova-relay",
            "version": __version__,
            "uptimeSeconds": round(time.time() - self.started_at, 1),
            "routes": [
                {
                    "name": r.name,
                    "from": r.source,
                    "to": r.targets,
                    "datagrams": r.datagrams,
                    "bytes": r.bytes,
                    "invalidDropped": r.invalid,
                    "signing": r.sign_key is not None,
                    "verifyingOrigin": r.verify_key is not None,
                    "lastForwarded": r.last_forwarded,
                    "idleSeconds": None if r.last_forwarded is None
                                   else round(time.time() - r.last_forwarded, 3),
                }
                for r in self._routes
            ],
        }


def create_health_app(relay: Relay) -> web.Application:
    app = web.Application()

    async def health(request):
        return web.json_response(relay.stats())

    app.router.add_get("/api/health", health)
    return app


async def _serve(config_path: str) -> None:
    routes, health_port = load_config(config_path)
    relay = Relay(routes)
    await relay.start()
    if health_port:
        runner = web.AppRunner(create_health_app(relay))
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", health_port)
        await site.start()
    names = ", ".join(r.name for r in routes)
    print(f"hypernova-relay: forwarding {len(routes)} route(s): {names}")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        relay.stop()


def run(config_path: str) -> None:
    asyncio.run(_serve(config_path))
