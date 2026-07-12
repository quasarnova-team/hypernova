"""DIP bridge pipeline against a stub of the CERN DIP Python API surface:
everything except the real DIP wire is exercised — config, subscription
creation, quality mapping, forwarding into real hypernova datagrams received
by a real Subscriber. On-site validation vs a real DIPNS: pending (needs the
CERN network), stated in DIP-PARITY.md."""

import asyncio
import json

import pytest

from hypernova.bridge_dip import DipBridge, load_bridge_config
from hypernova.client import Subscriber


class StubMessage:
    def __init__(self, values, quality="good"):
        self._values = values
        self._quality = quality

    def extract(self, name):
        return self._values[name]

    def extractDataQuality(self):  # noqa: N802 — DIP API surface
        return self._quality


class StubSubscription:
    def __init__(self, name, handler):
        self.name = name
        self.handler = handler
        self.destroyed = False

    def push(self, values, quality="good"):
        self.handler.handleMessage(self, StubMessage(values, quality))

    def destroy(self):
        self.destroyed = True


class StubFactory:
    def __init__(self):
        self.subscriptions = []

    def createDipSubscription(self, name, handler):  # noqa: N802 — DIP API surface
        subscription = StubSubscription(name, handler)
        self.subscriptions.append(subscription)
        return subscription


class StubDipModule:
    def __init__(self):
        self.factory = StubFactory()

    def create(self, name):
        return self.factory


@pytest.fixture
def bridge_config(tmp_path):
    path = tmp_path / "bridge.json"
    path.write_text(json.dumps({
        "publisher": {"address": "opc.udp://127.0.0.1:24891",
                      "publisher_id": 900, "writer_group_id": 1},
        "registry": "http://127.0.0.1:1",
        "subscriptions": [
            {"dip": "dip/CERN/LHC/Beam/Energy", "hypernova": "lhc/beam/energy",
             "fields": {"value": "DOUBLE"}},
            {"dip": "dip/CERN/LHC/Beam/Mode", "hypernova": "lhc/beam/mode",
             "fields": {"mode": "STRING"}},
        ],
    }))
    return path


def test_config_validation(bridge_config, tmp_path):
    config = load_bridge_config(bridge_config)
    assert len(config.subscriptions) == 2
    assert config.subscriptions[0].dataset_writer_id == 1
    assert config.subscriptions[1].dataset_writer_id == 2
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"publisher": {"address": "opc.udp://1.2.3.4:1",
                                               "publisher_id": 1, "writer_group_id": 1}}))
    with pytest.raises(ValueError, match="no subscriptions"):
        load_bridge_config(empty)


async def test_dip_values_flow_into_hypernova(bridge_config):
    stub = StubDipModule()
    bridge = DipBridge(load_bridge_config(bridge_config), dip_module=stub)
    await asyncio.to_thread(bridge.start)
    assert len(stub.factory.subscriptions) == 2

    subscriber = Subscriber(
        "lhc/beam/energy", registry="http://127.0.0.1:1",
        address="opc.udp://127.0.0.1:24891", publisher_id=900,
        writer_group_id=1, dataset_writer_id=1, field_names=["value"])
    with subscriber:
        await asyncio.sleep(0.2)
        energy = stub.factory.subscriptions[0]
        energy.push({"value": 6799.5})
        update = await asyncio.to_thread(subscriber.get, 5.0)
        assert update.values["value"].value == 6799.5
        assert update.values["value"].is_good
        assert update.values["value"].source_timestamp is not None

        energy.push({"value": -1.0}, quality="bad")
        update = await asyncio.to_thread(subscriber.get, 5.0)
        assert not update.values["value"].is_good

    assert bridge.forwarded == 2
    assert bridge.errors == 0

    energy.push({"wrong_field": 1})
    assert bridge.errors == 1

    bridge.stop()
    assert all(subscription.destroyed for subscription in stub.factory.subscriptions)


def test_missing_dip_module_message(bridge_config, monkeypatch):
    import importlib
    real_import = importlib.import_module

    def refuse(name, *args, **kwargs):
        if name == "dip":
            raise ImportError("no dip here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", refuse)
    with pytest.raises(SystemExit, match="DIP Python bindings"):
        DipBridge(load_bridge_config(bridge_config))
