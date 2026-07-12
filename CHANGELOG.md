# Changelog

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
