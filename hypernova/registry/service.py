"""The registry's REST + web face. Everything the browser UI shows comes
through the same JSON API subscribers use — one source of truth."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from aiohttp import web

from hypernova import __version__
from hypernova.registry.listener import Listener
from hypernova.registry.store import FieldSpec, Publication, Store, StoreError
from hypernova.registry.ui import INDEX_HTML
from hypernova.wire import BuiltinType

_STALE_AFTER_SECONDS = 10.0


def _field_json(name: str, value) -> dict:
    return {
        "name": name,
        "type": value.type.name,
        "value": value.value,
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
        },
        "leaseExpired": publication.lease_expired,
    }
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
            lease_seconds=float(data.get("leaseSeconds", 600.0)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StoreError(f"malformed registration: {error}") from None


def create_app(store: Store, *, listen: bool = True) -> web.Application:
    """`listen=False` runs a pure phonebook: lookups and registration work,
    the live-browser columns stay empty (deployments behind unicast-only
    reachability, or tests sharing one host with subscribers)."""
    listener = Listener(store)
    app = web.Application()

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
        return web.json_response({
            "service": "hypernova-registry",
            "version": __version__,
            "publications": len(store),
            "undecodableDatagrams": listener.undecodable_datagrams,
        })

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
            data = await request.json()
            publication = _publication_from_json(name, data)
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
            publication = store.renew(request.match_info["name"])
        except StoreError as error:
            return web.json_response({"error": str(error)}, status=404)
        return web.json_response(_publication_json(publication, listener))

    @routes.delete("/api/publications/{name:.+}")
    async def remove(request):
        try:
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


def run(host: str = "0.0.0.0", port: int = 4850, store_path: str | None = "registry.json") -> None:
    store = Store(Path(store_path) if store_path else None)
    web.run_app(create_app(store), host=host, port=port)
