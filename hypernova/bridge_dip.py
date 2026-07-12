"""DIP → hypernova migration bridge: republish existing DIP publications as
hypernova streams, so consumers migrate one at a time while publishers stay
untouched — no flag day.

    hypernova bridge-dip bridge_config.json

Config:

    {
      "publisher": {"address": "opc.udp://239.10.0.30:14840",
                    "publisher_id": 900, "writer_group_id": 1},
      "registry": "http://registry:4850",
      "subscriptions": [
        {"dip": "dip/CERN/LHC/Beam/Energy",
         "hypernova": "lhc/beam/energy",
         "fields": {"value": "DOUBLE"}}
      ]
    }

The DIP side is loaded dynamically: the official CERN DIP Python bindings
(``import dip``) where installed; anywhere else — including this repository's
CI — a stub with the same surface drives the full pipeline, so everything
*around* DIP is tested. Validation against a real DIP name server requires
the CERN network and is tracked as pending in DIP-PARITY.md.
"""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from hypernova.client import Publisher
from hypernova.wire import STATUS_BAD, STATUS_GOOD, STATUS_UNCERTAIN

__all__ = ["BridgeConfig", "Subscription", "DipBridge", "load_bridge_config", "run"]

_DIP_QUALITY = {"good": STATUS_GOOD, "uncertain": STATUS_UNCERTAIN, "bad": STATUS_BAD}


@dataclass
class Subscription:
    dip_name: str
    hypernova_name: str
    fields: dict  # field name -> hypernova type name
    dataset_writer_id: int = 0


@dataclass
class BridgeConfig:
    address: str
    publisher_id: int
    writer_group_id: int
    registry: str | None
    subscriptions: list = field(default_factory=list)


def load_bridge_config(path: str | Path) -> BridgeConfig:
    data = json.loads(Path(path).read_text())
    publisher = data["publisher"]
    config = BridgeConfig(
        address=publisher["address"],
        publisher_id=int(publisher["publisher_id"]),
        writer_group_id=int(publisher["writer_group_id"]),
        registry=data.get("registry"),
    )
    for index, entry in enumerate(data.get("subscriptions", [])):
        config.subscriptions.append(Subscription(
            dip_name=entry["dip"],
            hypernova_name=entry["hypernova"],
            fields=dict(entry["fields"]),
            dataset_writer_id=int(entry.get("dataset_writer_id", index + 1)),
        ))
    if not config.subscriptions:
        raise ValueError("bridge config declares no subscriptions")
    return config


def _load_dip_module(explicit=None):
    if explicit is not None:
        return explicit
    try:
        return importlib.import_module("dip")
    except ImportError:
        raise SystemExit(
            "the CERN DIP Python bindings ('import dip') are not installed. "
            "On a CERN machine install them from the DIP support site; this "
            "bridge cannot run without them (its pipeline is CI-tested "
            "against a stub — see tests/test_bridge_dip.py).") from None


class DipBridge:
    """One DIP subscriber per configured publication, one hypernova Publisher
    per publication, values forwarded with quality mapped to StatusCodes."""

    def __init__(self, config: BridgeConfig, *, dip_module=None) -> None:
        self._config = config
        self._dip = _load_dip_module(dip_module)
        self._publishers: dict[str, Publisher] = {}
        self._subscriptions = []
        self.forwarded = 0
        self.errors = 0

    def start(self) -> None:
        try:
            self._start()
        except Exception:
            self.stop()
            raise

    def _start(self) -> None:
        factory = self._dip.create("hypernova-dip-bridge")
        for subscription in self._config.subscriptions:
            publisher = Publisher(
                subscription.hypernova_name,
                fields=subscription.fields,
                address=self._config.address,
                publisher_id=self._config.publisher_id,
                writer_group_id=self._config.writer_group_id,
                dataset_writer_id=subscription.dataset_writer_id,
                registry=self._config.registry,
                description=f"DIP bridge: {subscription.dip_name}",
            )
            self._publishers[subscription.dip_name] = publisher
            handler = _Handler(self, subscription, publisher)
            self._subscriptions.append(
                factory.createDipSubscription(subscription.dip_name, handler))

    def stop(self) -> None:
        for subscription in self._subscriptions:
            try:
                subscription.destroy()
            except Exception:  # noqa: BLE001 — DIP teardown must not block ours
                pass
        for publisher in self._publishers.values():
            publisher.close()


class _Handler:
    """DIP handler interface: handleMessage(subscription, message),
    connected(...), disconnected(...)."""

    def __init__(self, bridge: DipBridge, subscription: Subscription,
                 publisher: Publisher) -> None:
        self._bridge = bridge
        self._subscription = subscription
        self._publisher = publisher

    def handleMessage(self, dip_subscription, message) -> None:  # noqa: N802 — DIP API
        try:
            values = {}
            statuses = {}
            quality = _DIP_QUALITY.get(
                str(getattr(message, "extractDataQuality", lambda: "good")()).lower(),
                STATUS_GOOD)
            for field_name in self._subscription.fields:
                values[field_name] = message.extract(field_name)
                statuses[field_name] = quality
            self._publisher.send(_status=statuses, **values)
            self._bridge.forwarded += 1
        except Exception:  # noqa: BLE001 — one bad sample must not kill the handler
            self._bridge.errors += 1

    def connected(self, dip_subscription) -> None:  # noqa: N802 — DIP API
        pass

    def disconnected(self, dip_subscription, reason) -> None:  # noqa: N802 — DIP API
        pass


def run(config_path: str, *, dip_module=None) -> None:
    config = load_bridge_config(config_path)
    bridge = DipBridge(config, dip_module=dip_module)
    try:
        bridge.start()
    except Exception:
        bridge.stop()
        raise
    names = ", ".join(s.dip_name for s in config.subscriptions)
    print(f"hypernova-dip-bridge: forwarding {len(config.subscriptions)} "
          f"DIP publication(s): {names}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        bridge.stop()
