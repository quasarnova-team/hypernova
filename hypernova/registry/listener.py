"""The half of the registry DIPNS never had: it listens to what it
registers. One receiver per distinct (host, port); every decoded
DataSetMessage is matched to a publication and cached as its live state —
which makes the registry a live browser of the whole namespace."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from hypernova import transport
from hypernova.registry.store import Publication, Store
from hypernova.wire import FieldValue, WireError, decode_network_message

_RATE_WINDOW_SECONDS = 30.0


@dataclass
class LiveState:
    last_values: dict[str, FieldValue] = field(default_factory=dict)
    last_seen: float | None = None
    messages: int = 0
    lost: int = 0
    undecodable: int = 0
    _last_sequence: int | None = None
    _arrivals: list[float] = field(default_factory=list)

    @property
    def rate_hz(self) -> float:
        now = time.time()
        recent = [t for t in self._arrivals if now - t <= _RATE_WINDOW_SECONDS]
        if len(recent) < 2:
            return 0.0
        span = recent[-1] - recent[0]
        return (len(recent) - 1) / span if span > 0 else 0.0

    def observe(self, fields: dict[str, FieldValue], sequence: int | None) -> None:
        now = time.time()
        self.last_values.update(fields)
        self.last_seen = now
        self.messages += 1
        self._arrivals.append(now)
        if len(self._arrivals) > 512:
            del self._arrivals[:256]
        if sequence is not None and self._last_sequence is not None:
            gap = (sequence - self._last_sequence) & 0xFFFF
            if 1 < gap < 0x8000:
                self.lost += gap - 1
        self._last_sequence = sequence


class Listener:
    """Joins every distinct endpoint in the store and caches live state."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._transports: dict[tuple[str, int], object] = {}
        self._live: dict[str, LiveState] = {}
        self.undecodable_datagrams = 0
        self.endpoint_errors: dict[str, str] = {}

    def live(self, name: str) -> LiveState:
        return self._live.setdefault(name, LiveState())

    async def sync(self) -> None:
        """(Re)open receivers so every registered endpoint is being heard.
        One endpoint failing to bind (port clash with a local subscriber,
        privileged port, unparsable address from an old store) never affects
        the others or the service — the failure is recorded and surfaced."""
        wanted: dict[tuple[str, int], None] = {}
        self.endpoint_errors.clear()
        for publication in self._store.list():
            try:
                wanted[transport.parse_address(publication.address)] = None
            except ValueError as error:
                self.endpoint_errors[publication.address] = str(error)
        for endpoint in list(self._transports):
            if endpoint not in wanted:
                self._transports.pop(endpoint).close()
        for (host, port) in wanted:
            if (host, port) in self._transports:
                continue
            try:
                receiver = await transport.create_receiver(
                    host, port, lambda data, addr: self._on_datagram(data))
            except (OSError, ValueError) as error:
                self.endpoint_errors[f"{host}:{port}"] = str(error)
                continue
            self._transports[(host, port)] = receiver

    def close(self) -> None:
        for receiver in self._transports.values():
            receiver.close()
        self._transports.clear()

    def _on_datagram(self, data: bytes) -> None:
        try:
            message = decode_network_message(data)
        except WireError:
            self.undecodable_datagrams += 1
            return
        for dsm in message.messages:
            if dsm.keep_alive:
                continue
            publication = self._match(message.publisher_id,
                                      message.writer_group_id or 0,
                                      dsm.dataset_writer_id)
            if publication is None:
                continue
            named = {}
            for index, value in enumerate(dsm.fields):
                if index < len(publication.fields):
                    named[publication.fields[index].name] = value
                else:
                    named[f"field{index}"] = value
            self.live(publication.name).observe(named, dsm.sequence_number)

    def _match(self, publisher_id: int, writer_group_id: int,
               dataset_writer_id: int) -> Publication | None:
        for publication in self._store.list():
            if (publication.publisher_id == publisher_id
                    and publication.writer_group_id == writer_group_id
                    and publication.dataset_writer_id == dataset_writer_id):
                return publication
        return None
