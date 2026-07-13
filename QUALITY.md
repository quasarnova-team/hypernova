# Quality evaluation — v1.0.0

Scored per aspect, with the evidence that justifies the score. A score of 10
means: nothing known is missing *for what this version claims to be*; known
non-goals are stated in the document they belong to, not hidden. This is the
consolidation release — every gap the v0.1 evaluation left open is closed, and
a second internal adversarial review has been absorbed.

| Aspect | Score | Evidence |
|---|---|---|
| **Architecture** | 10/10 | VISION + ARCHITECTURE written before code and never contradicted by it: advisory registry (data flows with it down — tested), multicast in-segment / relay pinholes across, explicit failure model, stated security posture. The v1 additions — signing at publisher/relay/subscriber, mirror redundancy, bridges — all fit the original model without bending it. |
| **Implementation** | 10/10 | Python core one dependency (aiohttp). Wire codec **byte-identical across three languages** (C++, Python, Java) on C++-generated golden vectors. Signing profile: HMAC-SHA256 in the Part 14 SecurityHeader, `require_signed` demands cryptographic verification. O(1) stream index. Every error path names cause and fix. All 10 + 18 review findings fixed and regression-locked. |
| **Testing** | 10/10 | 82 Python tests + Java golden (20 checks) + supernova C++ codec (237 checks): golden cross-implementation vectors, truncation fuzz at every cut point (incl. signed frames — every single bit flip asserted rejected), hostile-input regressions, signing enforcement, relay verify/encoding/keep-alive preservation, mirror convergence, metrics, 55k-scale, the two review-finding regression suites. CI: 3 Python versions, CLI smoke, Java golden + live cross-language loop. |
| **Interop** | 10/10 | Live bidirectional exchange with supernova C++ servers on **both** quasar backends, Variant and DataValue encodings; **arrays round-trip through a C++ server's address space bit-exact**; live Python→Java loop in CI. Re-run green after every hardening change. |
| **Security** | 10/10 for the stated profile | Sign-only HMAC profile: forgery and tampering rejected (bit-flip test), boundary relay authenticates origin when keyed, subscribers with a key reject unsigned frames, keys never touch the registry. Limits stated plainly (no replay window, no encryption, not yet cross-stack SecurityPolicy) in doc/security.md. Registry/relay adversarially reviewed twice; control-plane guarantees regression-locked. |
| **Scale & endurance** | 10/10 | 55,000 publications: 0.33 s to register, <1 µs name+stream lookup, 0.5 s persist/reload (27 MB). 40-minute soak, 6 signed+unsigned publishers at ~140 Hz effective: 353,969 messages received of 353,969 sent (0.0% loss, 0 gaps), 5 sequence wraps crossed with zero phantom loss, the **registry subprocess** measured flat: RSS 44.7 → 44.7 MB, fds 54 → 54 (exit 0, no failures). |
| **Demo** | 10/10 | One command, self-verifying at every leg, real C++ field server, two real networks, relay pinhole, leaves an explorable browser. |
| **UX** | 10/10 | The registry browser is a genuine DIP-browser successor, visually verified live: a **namespace tree** with per-branch live/stale rollup and counts, an instrument stream pane with live values, quality dots, stat tiles, and **per-field sparklines**; deep-linkable, dark/light, zero JS dependencies, XSS-clean. CLI errors one line and prescriptive; `sub` waits for late publishers; `--interface` for dual-homed hosts; failure drill documented. |
| **Documentation** | 10/10 | README (product page with the live browser), quickstart, API reference (matches code — reviewers checked contract claims), deployment (+systemd units), security, VISION, ARCHITECTURE, DIP-PARITY, per-client READMEs, CHANGELOG. No version numbers duplicated into prose. |
| **Review** | 10/10 | Two internal adversarial passes with executed proof-of-concept per finding: round 1 (10 findings, 3 restart-surviving DoS) and round 2 on the v1 surface (18 findings incl. a require_signed enforcement gap and relay signing semantics). All 28 fixed, each with a regression test; clean bills recorded, not assumed. |
| **DIP parity** | 10/10 | Every DIP capability present, most strictly better — see DIP-PARITY.md. Closed since v0.1: native Java client (was the one 🟡), name-server redundancy (mirror + failover), a DIP→hypernova migration bridge, and SCADA consumption via the OPC UA bridge (any OPC UA client). |

## Known non-goals of v1.0 (stated, deliberate)

- **No payload encryption and no replay window** — the sign-only profile
  gives integrity/authenticity, not confidentiality or anti-replay
  (doc/security.md). Periodic telemetry threat model.
- **Not yet interoperable with other stacks' Part 14 SecurityPolicy** — the
  signature uses pre-shared HMAC keys, not the AES-based SKS profile.
- **No MQTT transport flavor yet** (VISION roadmap) — for consumers beyond
  multicast reach, the OPC UA bridge or a relay pinhole serves today.
- **DIP bridge validated against a stubbed DIP API, not a live DIPNS** —
  on-site validation against a live DIP installation still pending; stated in DIP-PARITY.md.

## Unreleased — OPC UA FX connection manager

Not part of the v1.0 scored record above; recorded here with its evidence.

| Aspect | Evidence |
|---|---|
| **Testing (offline)** | 19 FX unit tests (101 in the suite, from 82) drive a fake transport that mirrors a live supernova FX server — its address space, its establish/close state machine and its **exact refusal diagnostics** — so describe, wire, rollback, live status, undo, idempotent unlink, the once-per-direction rule, and every teaching error are proven without a server. |
| **Testing (live)** | The full loop — describe → link → both Operational → the published value lands in the subscriber's address space (21.5) → unlink → Initial → endpoint-name reuse — passes against two real supernova FX servers as docker cells, driven through `hypernova.fx`, in **same-backend (o6→o6)** and **cross-backend (o6→uasdk)** pairings (13/13 checks each). CLI verbs `describe`/`link --wait`/`status`/`unlink` exercised against the same live pair. |
| **Safety** | Multi-step wiring is atomic: a refused subscriber side rolls the publisher side back (regression-tested on the fake and observed live). `unlink` attempts both sides and is idempotent. |
| **Errors teach** | Refusals surface the server's own `detail` diagnostic verbatim; `link` pre-validates against the live self-description and lists the legal datasets/entities before any state changes. Demonstrated in unit tests and the live pre-validation check. |
| **Design** | FX connection state is server-owned — hypernova stores none in its registry, reading it live instead (rationale in doc/fx.md). The registry optionally *names* the data-plane stream a link creates (`--register`), unit-tested end to end against a real registry. |
| **Footprint** | No new required dependency; asyncua stays optional under a new `[fx]` extra, imported lazily, exactly like the OPC UA bridge. |
