# Changelog

## 1.0.0 (2026-07-12)

The production release — every v0.1 gap closed:

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
  subprocess measured (flat RSS/fds, zero loss); second independent
  adversarial review — 18 findings, all fixed and regression-locked.
- **Ops**: container image on ghcr, systemd units, deep-linkable browser,
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
