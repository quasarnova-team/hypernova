"""Publication store: the phonebook. Names map to stream coordinates; the
store refuses collisions (DIPNS's job) and persists to a deliberately boring
JSON file."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from pathlib import Path

from hypernova import transport
from hypernova.wire import BuiltinType, PublisherIdType

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
_ADDRESS_RE = re.compile(r"^opc\.udp://[^\s:/]+:\d{1,5}$")

DEFAULT_LEASE_SECONDS = 600.0


class StoreError(ValueError):
    """Invalid registration or collision; message says exactly what and why."""


def _validate_address(address: str) -> None:
    if not _ADDRESS_RE.match(address):
        raise StoreError(f"invalid address {address!r}: expected opc.udp://host:port")
    try:
        _, port = transport.parse_address(address)
    except ValueError as error:
        raise StoreError(str(error)) from None
    if not 1 <= port <= 65535:
        raise StoreError(f"invalid address {address!r}: port out of range 1-65535")


@dataclass
class FieldSpec:
    name: str
    type: str

    @property
    def is_array(self) -> bool:
        return self.type.endswith("[]")

    def builtin(self) -> BuiltinType:
        return BuiltinType[self.type.removesuffix("[]")]


@dataclass
class Publication:
    name: str
    address: str
    publisher_id: int
    writer_group_id: int
    dataset_writer_id: int
    fields: list[FieldSpec]
    publisher_id_type: str = "UINT16"
    description: str = ""
    endpoints: dict[str, str] = field(default_factory=dict)
    #: provenance when this stream was created by an FX link (see doc/fx.md):
    #: {"connection", "publisher": {server,entity,dataset}, "subscriber": {...}}
    fx: dict | None = None
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    registered_at: float = 0.0
    renewed_at: float = 0.0

    @property
    def key(self) -> tuple:
        return (self.address, self.publisher_id, self.writer_group_id, self.dataset_writer_id)

    @property
    def stream_key(self) -> tuple:
        """Wire-visible identity (a datagram carries no address)."""
        return (self.publisher_id, self.writer_group_id, self.dataset_writer_id)

    @property
    def lease_expired(self) -> bool:
        return time.time() > self.renewed_at + self.lease_seconds

    def pid_type(self) -> PublisherIdType:
        return PublisherIdType[self.publisher_id_type]

    def address_for(self, network: str | None) -> str:
        if network and network in self.endpoints:
            return self.endpoints[network]
        return self.address

    def to_json(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Publication":
        known = {f.name for f in dataclass_fields(cls)} - {"fields"}
        fields = [FieldSpec(name=f["name"], type=f["type"]) for f in data["fields"]]
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(fields=fields, **kwargs)


def _validate(publication: Publication) -> None:
    if not _NAME_RE.match(publication.name):
        raise StoreError(
            f"invalid publication name {publication.name!r}: use slash-separated "
            "segments of letters, digits, '_', '.', '-'")
    _validate_address(publication.address)
    for network, address in publication.endpoints.items():
        try:
            _validate_address(address)
        except StoreError as error:
            raise StoreError(f"endpoint for network {network!r}: {error}") from None
    if not 0 <= publication.publisher_id <= 0xFFFFFFFFFFFFFFFF:
        raise StoreError("publisher_id out of range")
    if not 0 <= publication.writer_group_id <= 0xFFFF:
        raise StoreError("writer_group_id out of range (UInt16)")
    if not 0 <= publication.dataset_writer_id <= 0xFFFF:
        raise StoreError("dataset_writer_id out of range (UInt16)")
    if publication.publisher_id_type not in PublisherIdType.__members__:
        raise StoreError(
            f"unknown publisher_id_type {publication.publisher_id_type!r}: "
            f"expected one of {', '.join(PublisherIdType.__members__)}")
    if not publication.fields:
        raise StoreError("a publication needs at least one field")
    seen = set()
    for spec in publication.fields:
        base = spec.type.removesuffix("[]")
        if base not in BuiltinType.__members__ or base == "NULL":
            raise StoreError(
                f"field {spec.name!r} has unknown type {spec.type!r}: expected one of "
                f"{', '.join(t for t in BuiltinType.__members__ if t != 'NULL')} "
                "(append [] for arrays)")
        if spec.name in seen:
            raise StoreError(f"duplicate field name {spec.name!r}")
        seen.add(spec.name)
    if publication.lease_seconds <= 0:
        raise StoreError("lease_seconds must be positive")
    if publication.fx is not None:
        _validate_fx(publication.fx)


_MAX_FX_FIELD_BYTES = 256


def _fx_string(value, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise StoreError(f"{field_name} must be a non-empty string")
    if len(value.encode("utf-8")) > _MAX_FX_FIELD_BYTES:
        raise StoreError(f"{field_name} is too long (max {_MAX_FX_FIELD_BYTES} bytes)")


def _validate_fx(fx: dict) -> None:
    if not isinstance(fx, dict):
        raise StoreError("fx provenance must be an object")
    _fx_string(fx.get("connection"), "fx.connection")
    for side in ("publisher", "subscriber"):
        endpoint = fx.get(side)
        if not isinstance(endpoint, dict):
            raise StoreError(f"fx.{side} must be an object with server, entity, dataset")
        for key in ("server", "entity", "dataset"):
            _fx_string(endpoint.get(key), f"fx.{side}.{key}")


class Store:
    """In-memory publication catalog with atomic JSON persistence."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else None
        self._by_name: dict[str, Publication] = {}
        self._by_stream: dict[tuple, Publication] = {}
        self.load_error: str | None = None
        if self._path and self._path.exists():
            try:
                for data in json.loads(self._path.read_text()):
                    publication = Publication.from_json(data)
                    _validate(publication)  # never trust a hand-edited store file
                    if publication.stream_key in self._by_stream:
                        continue  # drop duplicate wire triples from a corrupt file
                    self._by_name[publication.name] = publication
                    self._by_stream[publication.stream_key] = publication
            except (ValueError, TypeError, KeyError) as error:
                quarantine = self._path.with_suffix(self._path.suffix + ".corrupt")
                os.replace(self._path, quarantine)
                self._by_name.clear()
                self._by_stream.clear()
                self.load_error = (f"could not load {self._path}: {error} — "
                                   f"file preserved as {quarantine}, starting empty")

    def __len__(self) -> int:
        return len(self._by_name)

    def list(self) -> list[Publication]:
        return sorted(self._by_name.values(), key=lambda p: p.name)

    def get(self, name: str) -> Publication | None:
        return self._by_name.get(name)

    def register(self, publication: Publication, *, replace: bool = False) -> Publication:
        _validate(publication)
        existing = self._by_name.get(publication.name)
        if existing and not replace:
            raise StoreError(f"publication {publication.name!r} is already registered "
                             "(renew it, or register with replace)")
        other = self._by_stream.get(publication.stream_key)
        if other and other.name != publication.name:
            raise StoreError(
                f"stream collision: {other.name!r} already uses publisher "
                f"{publication.publisher_id}, writer group {publication.writer_group_id}, "
                f"dataset writer {publication.dataset_writer_id} — the wire triple is a "
                "publication's identity and must be unique registry-wide")
        now = time.time()
        publication.registered_at = existing.registered_at if existing else now
        publication.renewed_at = now
        if existing:
            self._by_stream.pop(existing.stream_key, None)
        self._by_name[publication.name] = publication
        self._by_stream[publication.stream_key] = publication
        self._persist()
        return publication

    def find_stream(self, publisher_id: int, writer_group_id: int,
                    dataset_writer_id: int) -> Publication | None:
        """O(1) datagram-to-publication match."""
        return self._by_stream.get((publisher_id, writer_group_id, dataset_writer_id))

    def renew(self, name: str) -> Publication:
        publication = self._by_name.get(name)
        if not publication:
            raise StoreError(f"unknown publication {name!r}")
        publication.renewed_at = time.time()
        self._persist()
        return publication

    def remove(self, name: str) -> None:
        publication = self._by_name.get(name)
        if publication is None:
            raise StoreError(f"unknown publication {name!r}")
        del self._by_name[name]
        self._by_stream.pop(publication.stream_key, None)
        self._persist()

    def _persist(self) -> None:
        if not self._path:
            return
        payload = json.dumps([p.to_json() for p in self.list()], indent=2)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(payload)
        os.replace(temp, self._path)
