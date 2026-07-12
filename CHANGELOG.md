# Changelog

## 1.1.1 (2026-07-13)

Connection-manager hardening from the FX adversarial review:

- **`fx connect` now rolls both sides back on any post-publisher failure.**
  Previously, missing or malformed publisher coordinates raised without
  closing the already-established publisher, and a registry error after both
  sides were up left both live. Now every failure after the publisher
  establishes — bad coordinates, subscriber refusal, registry error — closes
  whatever is live, so a failed connect never orphans a half-connection (or
  leaks a server endpoint slot).
- Publisher coordinates are validated (type-checked) before use.
- 3 new regression tests (missing-coordinates rollback, malformed-coordinates
  rollback, registration-failure rolls back both) — 89 tests total, green.

Pairs with [supernova 1.2.1](https://github.com/quasarnova-team/supernova/releases/tag/v1.2.1).
Purely additive/defensive; no API change.

## 1.1.0 (2026-07-13)

The FX connection manager — hypernova learns OPC UA FX (Parts 80/81):

- **`hypernova fx connect`** wires one FX server's output dataset to another's
  input dataset at runtime: publisher side first, its returned wire
  coordinates handed to the subscriber side as the peer; on subscriber refusal
  the publisher side is closed again (no half-open links).
- **`hypernova fx status`** browses a server's live `ConnectionEndpoints`;
  **`hypernova fx close`** tears a connection down by id.
- **`--register / --register-as / --network`** turn the established stream into
  a first-class hypernova publication — field names *and types* read from the
  publisher's FX view — so an engineered link shows live in the registry
  browser alongside ad-hoc consumers on the same wire.
- Targets any server exposing the FX pattern; today that is
  [supernova >= 1.2.0](https://github.com/quasarnova-team/supernova) with an
  `<Fx>` configuration section. Requires the `[bridge]` extra (asyncua).
- 5 new unit tests (publisher-first ordering, coordinate handoff, rollback on
  subscriber refusal, projection type map) — 87 tests total, green. Verified
  end to end against real supernova servers in all four backend combinations;
  registry synergy checked live (typed registration + the registry's message
  counter climbing on the FX stream).

The core (registry, clients, relay, bridges, signing) is unchanged — 1.1.0 is
purely additive. Docs: [doc/fx.md](doc/fx.md).

## 1.0.0 (2026-07-12)

The consolidation release — every v0.1 gap closed:

- **Message signing** (hypernova signing profile v1): HMAC-SHA256 in the
  Part 14 SecurityHeader frame structure — sign at the publisher or at the
  boundary relay, verify in Python and Java; `require_signed` demands
  cryptographic verification (a self-asserted signed bit proves nothing).
  Every-bit-flip tamper test; honest limits (no replay window, no
  encryption, not yet cross-stack SecurityPolicy) in doc/security.md.
- **DIP-scale registry**: O(1) stream matching (55k publications: 0.33 s to
  register, <1 µs lookups), write locking, Prometheus `/metrics`,
  `--mirror-of` follower convergence, per-endpoint bind isolation.
- **Arrays end to end**, including through supernova C++ servers
  (supernova 1.1.0) — a DOUBLE[] round-trips
  python→C++ reader→address space→C++ writer→python bit-exact.
- **Java client** (dependency-free, JDK 11+): subscribe/publish by name,
  arrays, quality, signature verification — byte-parity with the C++ and
  Python codecs on shared golden vectors, plus a live cross-language CI loop.
- **`bridge-opcua`**: publications served as a classic OPC UA server —
  any OPC UA client, including commercial SCADA tools, can consume streams (verified with an OPC UA
  client end to end).
- **`bridge-dip`**: the migration bridge — republish existing DIP
  publications as hypernova streams (CI-tested against a stubbed DIP API;
  on-site validation against a live DIP installation still pending).
- **Soaked and reviewed**: 40-minute multi-wrap soak with the registry
  subprocess measured (flat RSS/fds, zero loss); second internal
  adversarial review — 18 findings, all fixed and regression-locked.
- **Ops**: container build + release workflow (public ghcr publication pending), systemd units, deep-linkable browser,
  registry failover via comma-separated URLs.


## 0.1.0 (2026-07-12)

First release of the fabric:

- UADP wire codec (Part 14), byte-identical to supernova's C++ engine on
  C++-generated golden vectors; scalars + one-dimensional arrays; DataValue
  field encoding carrying per-field status + source timestamp.
- Registry: names → streams with collision refusal and leases, per-network
  endpoints, JSON persistence — and it listens to what it registers: live
  web browser with values, quality, rates, staleness, loss counters and
  copy-paste subscriber snippets.
- Python clients: `Publisher`/`Subscriber` by name, coordinate caching
  (registry-down resilient), explicit-coordinates mode.
- Boundary relay: raw-forwarding pinhole process with per-route counters and
  a health endpoint.
- CLI: `registry`, `relay`, `browse`, `sub`, `pub`, `register`.
- Interop suite vs supernova C++ servers (both quasar backends, both
  directions) and a one-command two-network demo.
