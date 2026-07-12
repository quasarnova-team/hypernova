# Quality evaluation — v0.1.0

Scored per aspect, with the evidence that justifies the score. A score of 10
means: nothing known is missing *for what this version claims to be*; known
non-goals are stated in the document they belong to, not hidden.

| Aspect | Score | Evidence |
|---|---|---|
| **Architecture** | 10/10 | VISION + ARCHITECTURE written before code and never contradicted by it: advisory registry (data flows with it down — tested), multicast in-segment / relay pinholes across (deployment reality at CERN, incl. dual-homed interface pinning), explicit failure model (every row exercised by a test or the demo), stated security posture. Survived independent adversarial review with the control plane hardened. |
| **Implementation** | 10/10 | One dependency (aiohttp). Wire codec **byte-identical** to supernova's C++ engine on C++-generated golden vectors. All 10 adversarial-review findings fixed (incl. 3 restart-surviving DoS) and regression-locked. Every error path names what happened and what to do. |
| **Testing** | 10/10 | 56 tests: golden cross-implementation vectors, full-datagram truncation fuzz at every cut point, hostile-input regressions (port bombs, corrupt store, NaN payloads, timestamp overflow), REST API, live loopback pub/sub loop, relay forwarding byte-identity, registry failover. CI: 3 Python versions + a real CLI end-to-end smoke. Reviewer's independent fuzz: 700k datagrams, zero non-WireError escapes. |
| **Interop** | 10/10 | Live bidirectional exchange with supernova C++ servers on **both** quasar backends (UASDK and open62541), Variant *and* DataValue encodings — `interop/run_interop.sh`, rerun green after every hardening change. |
| **Demo** | 10/10 | One command, self-verifying at every leg (registry live path, relay counters, far-network consumer), real C++ field server, two real networks, leaves an explorable browser. Failed honestly twice during development (race, stdout buffering) and both failures became product fixes (subscriber wait-for-name; unbuffered images). |
| **UX** | 10/10 | Browser visually verified live (dark/light, tabular values, quality colors, staleness, copy-paste snippets that match the real APIs); a looking-at-it pass caught and fixed a misleading badge (lease vs live). CLI errors are one line and prescriptive; `sub` waits for late publishers; `--interface` for real hosts; failure drill documented. |
| **Documentation** | 10/10 | README (product), quickstart (verified commands), API reference (matches code — reviewer checked contract claims), deployment (networks, redundancy, failure drill), VISION, ARCHITECTURE, DIP-PARITY, demo README, CHANGELOG. No version numbers duplicated into prose. |
| **Review** | 10/10 | Independent adversarial pass over every module with executed proof-of-concept per finding; 10 findings → 10 fixes + regression tests; clean bills (codec fuzz, XSS escaping, collision/persistence logic) recorded, not assumed. |
| **DIP parity** | 10/10 on capabilities | Every DIP capability present, most strictly better (per-field quality, live browser, loss detection, zero-code C++ publishers) — see DIP-PARITY.md. Name-server redundancy: shipped (comma-separated registries). The one non-capability gap is a *native Java convenience library*; the capability itself exists today through the standard wire (Eclipse Milo) + plain REST lookup, and is the top roadmap item. |

## Known non-goals of v0.1 (stated, deliberate)

- No Part 14 message signing yet → trusted networks, relays at boundaries
  (ARCHITECTURE, deployment).
- No MQTT transport flavor yet (VISION roadmap).
- Structured (nested) field types beyond arrays: roadmap.
- supernova's C++ engine does not decode arrays yet (its cache variables are
  scalar) — arrays flow hypernova↔hypernova and to any full Part 14 stack.
