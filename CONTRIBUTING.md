# Contributing to hypernova

Questions and ideas are welcome in
[Discussions](https://github.com/quasarnova-team/hypernova/discussions); bugs and
concrete proposals in [Issues](https://github.com/quasarnova-team/hypernova/issues).

## Development setup

```bash
git clone https://github.com/quasarnova-team/hypernova
cd hypernova
pip install -e ".[bridge,dev]"
pytest                       # unit + integration tests
```

The full DIP-replacement demo topology (real C++ field server, registry, relay,
consumer) runs with one command — see [demo/](demo/); it needs Docker and a built
supernova tree.

## Pull requests

- One topic per PR, with tests for any behaviour change. Wire-format changes must
  keep the three-language golden vectors green ([interop/](interop/)).
- The signing profile has documented limits (doc/security.md) — changes to it need
  a matching doc update.
- CI must be green: unit suite plus the cross-language byte-parity loop.

## Reporting security issues

Please do not open public issues for suspected vulnerabilities — see
[SECURITY.md](SECURITY.md). (doc/security.md documents the *protocol* signing
profile; SECURITY.md is how to report problems with it.)
