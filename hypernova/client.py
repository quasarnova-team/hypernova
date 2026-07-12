"""Publish and subscribe by name — the DIP-flat API.

    from hypernova import Subscriber

    with Subscriber("atlas/dcs/atca/crate1/env") as sub:
        for update in sub.updates():
            print(update.values["temperature"].value)

    from hypernova import Publisher

    with Publisher("atlas/dcs/demo/env",
                   fields={"temperature": "DOUBLE", "label": "STRING"},
                   address="opc.udp://239.10.0.1:14840",
                   publisher_id=42, writer_group_id=100, dataset_writer_id=1) as pub:
        pub.send(temperature=21.5, label="warm")

Both talk to the registry (register / lookup) but keep working without it:
the Subscriber caches coordinates on disk after every successful lookup, the
Publisher only warns when registration is unreachable — data flows anyway.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hypernova import transport
from hypernova.wire import (
    STATUS_GOOD,
    BuiltinType,
    DataSetMessage,
    FieldValue,
    NetworkMessage,
    PublisherIdType,
    WireError,
    datetime_to_opc,
    decode_network_message,
    encode_network_message,
)

__all__ = ["Publisher", "Subscriber", "Update", "RegistryError", "default_registry_url"]

_CACHE_DIR = Path(os.environ.get("HYPERNOVA_CACHE",
                                 str(Path.home() / ".cache" / "hypernova")))


def default_registry_url() -> str:
    """One URL, or several comma-separated (DIP-style primary/secondary):
    lookups fail over in order; publishers register with every one."""
    return os.environ.get("HYPERNOVA_REGISTRY", "http://localhost:4850")


def _registry_urls(registry: str | None) -> list[str]:
    return [u.strip().rstrip("/") for u in (registry or default_registry_url()).split(",")
            if u.strip()]


class RegistryError(RuntimeError):
    """The registry refused or could not serve a request; message says why."""


def _registry_call(method: str, url: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(url, method=method)
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data=body, timeout=5) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except (ValueError, AttributeError):
            pass
        raise RegistryError(f"registry {method} {url} -> {error.code}: {detail}") from None
    except urllib.error.URLError as error:
        raise RegistryError(f"registry unreachable at {url}: {error.reason}") from None


def _cache_path(name: str) -> Path:
    import re
    safe = re.sub(r"[^A-Za-z0-9_.-]", "__", name)
    return _CACHE_DIR / (safe + ".json")


@dataclass
class Update:
    """One received DataSetMessage, fields resolved to names."""

    name: str
    values: dict[str, FieldValue]
    sequence_number: int | None
    received_at: float


class Subscriber:
    """Subscribe to a publication by name (registry lookup, cached) or by
    explicit coordinates (registry-free)."""

    def __init__(
        self,
        name: str,
        *,
        registry: str | None = None,
        network: str | None = None,
        address: str | None = None,
        publisher_id: int | None = None,
        writer_group_id: int | None = None,
        dataset_writer_id: int | None = None,
        field_names: list[str] | None = None,
        interface: str | None = None,
        verify_key: bytes | None = None,
        require_signed: bool = False,
    ) -> None:
        self.name = name
        self._verify_key = verify_key
        # require_signed enforces cryptographic verification, so it needs a key;
        # a key implies enforcement. require_signed=True without a key is a usage error.
        if require_signed and verify_key is None:
            raise ValueError("require_signed needs verify_key — the signed bit alone is unauthenticated")
        self._require_signed = require_signed or verify_key is not None
        self._registries = _registry_urls(registry)
        self._network = network
        self._interface = interface or os.environ.get("HYPERNOVA_INTERFACE") or "0.0.0.0"
        if address is not None:
            self._coords = {
                "address": address, "publisherId": publisher_id,
                "writerGroupId": writer_group_id, "dataSetWriterId": dataset_writer_id,
                "fields": [{"name": n} for n in field_names or []],
            }
        else:
            self._coords = self._lookup()
        self._queue: queue.Queue[Update] = queue.Queue(maxsize=1000)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.dropped_updates = 0
        self.undecodable_datagrams = 0

    def fields(self) -> list:
        """Field descriptors (name, type) from the resolved coordinates."""
        return list(self._coords.get("fields", []))

    def _lookup(self) -> dict:
        last_error: RegistryError | None = None
        for registry in self._registries:
            url = f"{registry}/api/lookup/{self.name}"
            if self._network:
                url += f"?network={self._network}"
            try:
                coords = _registry_call("GET", url)
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _cache_path(self.name).write_text(json.dumps(coords))
                return coords
            except RegistryError as error:
                last_error = error
        cached = _cache_path(self.name)
        if cached.exists():
            return json.loads(cached.read_text())
        raise RegistryError(
            f"{last_error} — and no cached coordinates for {self.name!r}; "
            "pass explicit address/ids to subscribe registry-free") from None

    def start(self) -> "Subscriber":
        host, port = transport.parse_address(self._coords["address"])
        try:
            self._socket = transport.open_receive_socket(host, port, interface=self._interface)
        except OSError as error:
            raise OSError(
                f"cannot listen on {host}:{port} for {self.name!r}: {error}. "
                "Unicast ports are exclusive per host — if another process "
                "(e.g. a registry with listening enabled) already binds this "
                "port, only one of them can receive the stream.") from None
        self._socket.settimeout(0.25)
        self._thread = threading.Thread(target=self._run, name=f"hypernova-sub-{self.name}",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._socket:
            self._socket.close()

    def __enter__(self) -> "Subscriber":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def updates(self, timeout: float | None = None):
        """Yield Update objects; stops iterating on stop() or after `timeout`
        seconds without data (None = forever)."""
        while not self._stop.is_set():
            try:
                yield self._queue.get(timeout=timeout if timeout is not None else 0.5)
            except queue.Empty:
                if timeout is not None:
                    return

    def get(self, timeout: float = 5.0) -> Update:
        """Block for the next update; raises TimeoutError."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"no update for {self.name!r} within {timeout}s") from None

    def _run(self) -> None:
        pid = self._coords.get("publisherId")
        wg = self._coords.get("writerGroupId")
        dsw = self._coords.get("dataSetWriterId")
        field_names = [f["name"] for f in self._coords.get("fields", [])]
        while not self._stop.is_set():
            try:
                data = self._socket.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                message = decode_network_message(data, verify_key=self._verify_key,
                                                 require_signed=self._require_signed)
            except WireError:
                self.undecodable_datagrams += 1
                continue
            for dsm in message.messages:
                if dsm.keep_alive:
                    continue
                if pid is not None and message.publisher_id != pid:
                    continue
                if wg is not None and (message.writer_group_id or 0) != wg:
                    continue
                if dsw is not None and dsm.dataset_writer_id != dsw:
                    continue
                values = {}
                for index, value in enumerate(dsm.fields):
                    key = field_names[index] if index < len(field_names) else f"field{index}"
                    values[key] = value
                update = Update(self.name, values, dsm.sequence_number, time.time())
                try:
                    self._queue.put_nowait(update)
                except queue.Full:
                    self.dropped_updates += 1


class Publisher:
    """Publish a named dataset: registers with the registry (best effort),
    then sends one DataSetMessage per send()/tick."""

    def __init__(
        self,
        name: str,
        *,
        fields: dict[str, str],
        address: str,
        publisher_id: int,
        writer_group_id: int,
        dataset_writer_id: int,
        publisher_id_type: str = "UINT16",
        registry: str | None = None,
        description: str = "",
        endpoints: dict[str, str] | None = None,
        ttl: int = 1,
        register: bool = True,
        interface: str | None = None,
        sign_key: bytes | None = None,
    ) -> None:
        self.name = name
        self._field_types = {n: BuiltinType[t.removesuffix("[]")] for n, t in fields.items()}
        self._field_is_array = {n: t.endswith("[]") for n, t in fields.items()}
        self._address = address
        self._publisher_id = publisher_id
        self._publisher_id_type = PublisherIdType[publisher_id_type]
        self._writer_group_id = writer_group_id
        self._dataset_writer_id = dataset_writer_id
        self._registries = _registry_urls(registry)
        self._sign_key = sign_key
        self._sequence = 0
        self.registered = False
        host, port = transport.parse_address(address)
        self._target = (host, port)
        self._socket = transport.open_send_socket(
            host, ttl=ttl, interface=interface or os.environ.get("HYPERNOVA_INTERFACE"))
        if register:
            payload = {
                "address": address,
                "publisherId": publisher_id,
                "publisherIdType": publisher_id_type,
                "writerGroupId": writer_group_id,
                "dataSetWriterId": dataset_writer_id,
                "description": description,
                "endpoints": endpoints or {},
                "fields": [{"name": n, "type": t} for n, t in fields.items()],
                "replace": True,
            }
            for registry in self._registries:
                try:
                    _registry_call("PUT", f"{registry}/api/publications/{name}", payload)
                    self.registered = True
                except RegistryError:
                    pass

    def renew(self) -> None:
        for registry in self._registries:
            try:
                _registry_call("POST", f"{registry}/api/publications/{self.name}/renew")
            except RegistryError:
                pass

    def send(self, *, _status: dict[str, int] | None = None,
             _timestamp: datetime | None = None, **values) -> None:
        """Publish one sample: send(temperature=21.5, label="warm").
        Every field declared at construction must be present."""
        missing = set(self._field_types) - set(values)
        unknown = set(values) - set(self._field_types)
        if missing or unknown:
            raise ValueError(
                f"publication {self.name!r} fields mismatch: "
                + (f"missing {sorted(missing)} " if missing else "")
                + (f"unknown {sorted(unknown)}" if unknown else ""))
        for field_name, value in values.items():
            if self._field_is_array[field_name] != isinstance(value, (list, tuple)):
                expected = "a list" if self._field_is_array[field_name] else "a scalar"
                raise ValueError(f"field {field_name!r} expects {expected}, got {value!r}")
        stamp = datetime_to_opc(_timestamp or datetime.now(timezone.utc))
        statuses = _status or {}
        self._sequence = (self._sequence + 1) & 0xFFFF
        dsm = DataSetMessage(
            dataset_writer_id=self._dataset_writer_id,
            sequence_number=self._sequence,
            fields=[
                FieldValue(self._field_types[n], values[n],
                           statuses.get(n, STATUS_GOOD), stamp)
                for n in self._field_types
            ])
        message = NetworkMessage(
            publisher_id=self._publisher_id,
            publisher_id_type=self._publisher_id_type,
            writer_group_id=self._writer_group_id,
            group_sequence_number=self._sequence,
            messages=[dsm])
        self._socket.sendto(
            encode_network_message(message, sign_key=self._sign_key), self._target)

    def close(self) -> None:
        self._socket.close()

    def __enter__(self) -> "Publisher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
