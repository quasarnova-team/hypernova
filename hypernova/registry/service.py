"""The registry's REST + web face. Everything the browser UI shows comes
through the same JSON API subscribers use — one source of truth."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from urllib.parse import urljoin

from aiohttp import web

from hypernova import __version__
from hypernova.registry.listener import Listener
from hypernova.registry.store import FieldSpec, Publication, Store, StoreError
from hypernova.registry.ui import INDEX_HTML
from hypernova.wire import BuiltinType

_STALE_AFTER_SECONDS = 10.0


def _json_safe(value):
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return repr(value)
    if isinstance(value, list):
        return [_json_safe(element) for element in value]
    return value


def _field_json(name: str, value) -> dict:
    return {
        "name": name,
        "type": value.type.name,
        "value": _json_safe(value.value),
        "status": f"0x{value.status:08X}",
        "good": value.is_good,
        "sourceTime": value.source_datetime.isoformat() if value.source_datetime else None,
    }


def _publication_json(publication: Publication, listener: Listener, *, detail: bool = False) -> dict:
    live = listener.live(publication.name)
    now = time.time()
    stale = live.last_seen is None or now - live.last_seen > _STALE_AFTER_SECONDS
    data = {
        "name": publication.name,
        "description": publication.description,
        "address": publication.address,
        "endpoints": publication.endpoints,
        "publisherId": publication.publisher_id,
        "publisherIdType": publication.publisher_id_type,
        "writerGroupId": publication.writer_group_id,
        "dataSetWriterId": publication.dataset_writer_id,
        "fields": [{"name": f.name, "type": f.type} for f in publication.fields],
        "live": {
            "stale": stale,
            "lastSeen": live.last_seen,
            "ageSeconds": None if live.last_seen is None else round(now - live.last_seen, 3),
            "rateHz": round(live.rate_hz, 2),
            "messages": live.messages,
            "lost": live.lost,
            "signed": live.signed,
        },
        "leaseExpired": publication.lease_expired,
        "registeredAt": publication.registered_at,
    }
    if publication.fx is not None:
        data["fx"] = publication.fx  # provenance, in list + detail (browser marks it)
    if detail:
        data["values"] = [
            _field_json(f.name, live.last_values[f.name])
            for f in publication.fields if f.name in live.last_values
        ]
    return data


def _publication_from_json(name: str, data: dict) -> Publication:
    try:
        fields = [FieldSpec(name=f["name"], type=f["type"]) for f in data["fields"]]
        return Publication(
            name=name,
            address=data["address"],
            publisher_id=int(data["publisherId"]),
            publisher_id_type=str(data.get("publisherIdType", "UINT16")),
            writer_group_id=int(data["writerGroupId"]),
            dataset_writer_id=int(data["dataSetWriterId"]),
            fields=fields,
            description=str(data.get("description", "")),
            endpoints=dict(data.get("endpoints", {})),
            fx=data.get("fx"),  # validated in the store; None for non-FX publications
            lease_seconds=float(data.get("leaseSeconds", 600.0)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StoreError(f"malformed registration: {error}") from None


def create_app(store: Store, *, listen: bool = True,
               mirror_of: str | None = None) -> web.Application:
    """`listen=False` runs a pure phonebook: lookups and registration work,
    the live-browser columns stay empty (deployments behind unicast-only
    reachability, or tests sharing one host with subscribers).

    `mirror_of` makes this instance a follower: every 10 s it pulls the
    primary's publication list and merges anything it does not have (or has
    older) — so a secondary that was down during registrations converges.
    Followers still accept direct registrations (clients register with every
    registry in their comma-separated list), so mirroring is convergence
    insurance, not the primary mechanism."""
    listener = Listener(store)
    app = web.Application()
    mirror_state = {"lastSync": None, "error": None}

    async def _mirror_loop():
        import logging
        import aiohttp
        logger = logging.getLogger("hypernova.registry.mirror")
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(urljoin(mirror_of, "/api/publications"),
                                           timeout=aiohttp.ClientTimeout(total=5)) as response:
                        listed = await response.json()
                for entry in listed:
                    mine = store.get(entry["name"])
                    # converge: adopt if absent, or if the primary's copy is newer
                    if mine is not None and entry.get("registeredAt", 0) <= mine.registered_at:
                        continue
                    try:
                        async with write_lock:
                            store.register(_publication_from_json(entry["name"], entry),
                                           replace=True)
                    except StoreError as error:
                        logger.info("mirror could not adopt %s: %s", entry["name"], error)
                mirror_state["lastSync"] = time.time()
                mirror_state["error"] = None
                if listen:
                    await listener.sync()
            except Exception as error:  # noqa: BLE001 — follower must survive anything
                mirror_state["error"] = str(error)
            await asyncio.sleep(10)

    async def start_mirror(app_):
        if mirror_of:
            app_["mirror_task"] = asyncio.create_task(_mirror_loop())

    async def stop_mirror(app_):
        task = app_.get("mirror_task")
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    app.on_startup.append(start_mirror)
    app.on_cleanup.append(stop_mirror)
    write_lock = asyncio.Lock()

    async def start_listener(app_):
        if listen:
            await listener.sync()

    async def stop_listener(app_):
        listener.close()

    app.on_startup.append(start_listener)
    app.on_cleanup.append(stop_listener)

    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(request):
        return web.Response(text=INDEX_HTML, content_type="text/html")

    @routes.get("/api/health")
    async def health(request):
        payload = {
            "service": "hypernova-registry",
            "version": __version__,
            "publications": len(store),
            "undecodableDatagrams": listener.undecodable_datagrams,
            "endpointErrors": listener.endpoint_errors,
        }
        if store.load_error:
            payload["storeLoadError"] = store.load_error
        if mirror_of:
            payload["mirrorOf"] = mirror_of
            payload["mirrorLastSync"] = mirror_state["lastSync"]
            payload["mirrorError"] = mirror_state["error"]
        return web.json_response(payload)

    @routes.get("/metrics")
    async def metrics(request):
        now = time.time()
        lines = [
            "# TYPE hypernova_publications gauge",
            f"hypernova_publications {len(store)}",
            "# TYPE hypernova_undecodable_datagrams_total counter",
            f"hypernova_undecodable_datagrams_total {listener.undecodable_datagrams}",
            "# TYPE hypernova_endpoint_errors gauge",
            f"hypernova_endpoint_errors {len(listener.endpoint_errors)}",
        ]
        lines.append("# TYPE hypernova_publication_messages_total counter")
        lines.append("# TYPE hypernova_publication_lost_total counter")
        lines.append("# TYPE hypernova_publication_stale gauge")
        for publication in store.list():
            live = listener.live(publication.name)
            label = (publication.name.replace("\\", "\\\\")
                     .replace('"', '\\"').replace("\n", "").replace("\r", ""))
            stale = 1 if (live.last_seen is None
                          or now - live.last_seen > _STALE_AFTER_SECONDS) else 0
            lines.append(f'hypernova_publication_messages_total{{name="{label}"}} {live.messages}')
            lines.append(f'hypernova_publication_lost_total{{name="{label}"}} {live.lost}')
            lines.append(f'hypernova_publication_stale{{name="{label}"}} {stale}')
        return web.Response(text="\n".join(lines) + "\n",
                            content_type="text/plain", charset="utf-8")

    @routes.get("/api/types")
    async def types(request):
        return web.json_response(sorted(t.name for t in BuiltinType if t.name != "NULL"))

    @routes.get("/api/publications")
    async def list_publications(request):
        return web.json_response([
            _publication_json(p, listener) for p in store.list()
        ])

    @routes.get("/api/publications/{name:.+}")
    async def get_publication(request):
        publication = store.get(request.match_info["name"])
        if not publication:
            raise web.HTTPNotFound(text=f"unknown publication {request.match_info['name']!r}")
        return web.json_response(_publication_json(publication, listener, detail=True))

    @routes.put("/api/publications/{name:.+}")
    async def register(request):
        name = request.match_info["name"]
        try:
            try:
                data = await request.json()
            except ValueError:
                return web.json_response({"error": "request body is not valid JSON"},
                                         status=400)
            if not isinstance(data, dict):
                return web.json_response({"error": "request body must be a JSON object"},
                                         status=400)
            publication = _publication_from_json(name, data)
            async with write_lock:
                store.register(publication, replace=bool(data.get("replace", False)))
        except StoreError as error:
            status = 409 if "already" in str(error) or "collision" in str(error) else 400
            return web.json_response({"error": str(error)}, status=status)
        if listen:
            await listener.sync()
        return web.json_response(_publication_json(publication, listener), status=201)

    @routes.post("/api/publications/{name:.+}/renew")
    async def renew(request):
        try:
            async with write_lock:
                publication = store.renew(request.match_info["name"])
        except StoreError as error:
            return web.json_response({"error": str(error)}, status=404)
        return web.json_response(_publication_json(publication, listener))

    @routes.delete("/api/publications/{name:.+}")
    async def remove(request):
        try:
            async with write_lock:
                store.remove(request.match_info["name"])
        except StoreError as error:
            return web.json_response({"error": str(error)}, status=404)
        if listen:
            await listener.sync()
        return web.json_response({"removed": request.match_info["name"]})

    @routes.get("/api/lookup/{name:.+}")
    async def lookup(request):
        publication = store.get(request.match_info["name"])
        if not publication:
            raise web.HTTPNotFound(text=f"unknown publication {request.match_info['name']!r}")
        network = request.query.get("network")
        return web.json_response({
            "name": publication.name,
            "address": publication.address_for(network),
            "publisherId": publication.publisher_id,
            "publisherIdType": publication.publisher_id_type,
            "writerGroupId": publication.writer_group_id,
            "dataSetWriterId": publication.dataset_writer_id,
            "fields": [{"name": f.name, "type": f.type} for f in publication.fields],
        })

    app.add_routes(routes)
    return app


def run(host: str = "0.0.0.0", port: int = 4850, store_path: str | None = "registry.json",
        mirror_of: str | None = None) -> None:
    store = Store(Path(store_path) if store_path else None)
    web.run_app(create_app(store, mirror_of=mirror_of), host=host, port=port)
