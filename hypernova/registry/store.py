"""Publication store: the phonebook. Names map to stream coordinates; the
store refuses collisions (DIPNS's job) and persists to a deliberately boring
JSON file."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hypernova.wire import BuiltinType, PublisherIdType

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
_ADDRESS_RE = re.compile(r"^opc\.udp://[^\s:/]+:\d{1,5}$")

DEFAULT_LEASE_SECONDS = 600.0


class StoreError(ValueError):
    """Invalid registration or collision; message says exactly what and why."""


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
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    registered_at: float = 0.0
    renewed_at: float = 0.0

    @property
    def key(self) -> tuple:
        return (self.address, self.publisher_id, self.writer_group_id, self.dataset_writer_id)

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
        fields = [FieldSpec(**f) for f in data.pop("fields")]
        return cls(fields=fields, **data)


def _validate(publication: Publication) -> None:
    if not _NAME_RE.match(publication.name):
        raise StoreError(
            f"invalid publication name {publication.name!r}: use slash-separated "
            "segments of letters, digits, '_', '.', '-'")
    if not _ADDRESS_RE.match(publication.address):
        raise StoreError(
            f"invalid address {publication.address!r}: expected opc.udp://host:port")
    for network, address in publication.endpoints.items():
        if not _ADDRESS_RE.match(address):
            raise StoreError(f"invalid endpoint for network {network!r}: {address!r}")
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


class Store:
    """In-memory publication catalog with atomic JSON persistence."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else None
        self._by_name: dict[str, Publication] = {}
        if self._path and self._path.exists():
            for data in json.loads(self._path.read_text()):
                publication = Publication.from_json(data)
                self._by_name[publication.name] = publication

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
        for other in self._by_name.values():
            if other.name != publication.name and other.key == publication.key:
                raise StoreError(
                    f"stream collision: {other.name!r} already uses publisher "
                    f"{publication.publisher_id}, writer group {publication.writer_group_id}, "
                    f"dataset writer {publication.dataset_writer_id} on {publication.address}")
        now = time.time()
        publication.registered_at = existing.registered_at if existing else now
        publication.renewed_at = now
        self._by_name[publication.name] = publication
        self._persist()
        return publication

    def renew(self, name: str) -> Publication:
        publication = self._by_name.get(name)
        if not publication:
            raise StoreError(f"unknown publication {name!r}")
        publication.renewed_at = time.time()
        self._persist()
        return publication

    def remove(self, name: str) -> None:
        if name not in self._by_name:
            raise StoreError(f"unknown publication {name!r}")
        del self._by_name[name]
        self._persist()

    def _persist(self) -> None:
        if not self._path:
            return
        payload = json.dumps([p.to_json() for p in self.list()], indent=2)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(payload)
        os.replace(temp, self._path)
