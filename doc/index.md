# hypernova

Publish/subscribe data interchange for control systems — DIP's proven shape
(named publications, a registry, subscribe by name) rebuilt on standard
[OPC UA Pub/Sub (Part 14)](https://reference.opcfoundation.org/Core/Part14/v105/docs/).
The data-interchange fabric of the [quasarnova](https://quasarnova-team.github.io/) family.

Publications are Part 14 datasets over UDP, readable by any Part 14
implementation — and every [supernova](https://github.com/quasarnova-team/supernova)
OPC UA server is already a native publisher with a `<PubSub>` element in its config.

## Install

```bash
pip install "hypernova[bridge] @ git+https://github.com/quasarnova-team/hypernova"
# (PyPI's "hypernova" is an unrelated package — install from git)
```

## Five lines, either direction

```python
from hypernova import Subscriber

with Subscriber("site/area1/pump7/env") as sub:
    for update in sub.updates():
        t = update.values["temperature"]
        print(t.value, "good" if t.is_good else hex(t.status))
```

A dependency-free Java client ships too —
[clients/java](https://github.com/quasarnova-team/hypernova/tree/main/clients/java).

## Where to next

- **[Quickstart](quickstart.md)** — the ten-minute tour: registry, publish, subscribe, browse.
- **[API](api.md)** — Python client, CLI, and the registry's REST surface.
- **[Security](security.md)** — the signing profile and its honest limits.
- **[Deployment](deployment.md)** — real network boundaries, relays, systemd units.
- On GitHub: [Architecture](https://github.com/quasarnova-team/hypernova/blob/master/ARCHITECTURE.md) ·
  [Vision](https://github.com/quasarnova-team/hypernova/blob/master/VISION.md) ·
  [DIP parity matrix](https://github.com/quasarnova-team/hypernova/blob/master/DIP-PARITY.md) ·
  [Quality record](https://github.com/quasarnova-team/hypernova/blob/master/QUALITY.md) ·
  [Changelog](https://github.com/quasarnova-team/hypernova/blob/master/CHANGELOG.md)

## Heritage

hypernova's publish/subscribe shape is inspired by DIP, the Data Interchange
Protocol developed at CERN, where it has interconnected control systems for two
decades. quasarnova is an independent project and is not affiliated with or endorsed
by CERN; hypernova shares no code with DIP — the wire format is standard OPC UA
Pub/Sub.
