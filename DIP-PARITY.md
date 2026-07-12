# DIP parity — the zero-gap matrix

What CERN's DIP offers today, and where it lives in hypernova. "Better" means
the capability exists with strictly more than DIP provides.

| DIP capability | hypernova | Status |
|---|---|---|
| Named publications, hierarchical namespace | Registry names (`site/area1/...`), collision refusal | ✅ |
| Primitive value types | Boolean, (S)Byte, (U)Int16/32/64, Float, Double, String, DateTime | ✅ |
| Arrays of primitives | Part 14 Variant arrays (`"DOUBLE[]"` field declarations) | ✅ |
| Structured publications | A publication *is* a named, typed multi-field dataset — DIP's struct, standardized | ✅ |
| Quality flag | Per-**field** OPC UA StatusCode (good/uncertain/bad + reason codes) | ✅ better |
| Timestamp | Per-field source timestamp on the wire (DataValue encoding) | ✅ better |
| Name server (DIPNS) | Registry: lookup, collision prevention, leases, per-network endpoints | ✅ |
| DIP Browser | Web UI **with live values**, rates, loss counters, staleness, copy-paste subscriber snippets | ✅ better |
| Publisher API (C++) | supernova/quasar servers publish via config.xml — zero code; custom C++ via any Part 14 stack (open62541 verified) | ✅ |
| Subscriber API (C++) | supernova `DataSetReader` (config.xml, values land in the address space); open62541 for standalone apps | ✅ |
| Publisher/Subscriber API (Python) | `hypernova.Publisher` / `hypernova.Subscriber` — five lines, DIP-flat | ✅ new |
| Java API | Native, dependency-free client ([clients/java](clients/java)): subscribe/publish by name, arrays, quality, signature verify — byte-parity with the C++/Python codecs proven on shared golden vectors | ✅ |
| Cross-domain reachability (TN↔GPN) | Relay pinholes (explicit, auditable, per-stream) or plain unicast UADP through a firewall rule — DIP's own model | ✅ |
| Publication liveness | Leases + continuous listening: the browser shows stale/lost/rate, which DIPNS never knew | ✅ better |
| Name-server redundancy | DIP-style primary/secondary: `HYPERNOVA_REGISTRY` takes comma-separated registries — lookups fail over in order, publishers register with every one; plus data flows with no registry at all and subscribers cache coordinates | ✅ |
| Migration from DIP | `hypernova bridge-dip`: republishes existing DIP publications as hypernova streams (pipeline CI-tested against a stubbed DIP API; on-site validation against a live DIP installation still pending) | ✅ (site validation pending) |
| SCADA consumption | `hypernova bridge-opcua`: publications served as a classic OPC UA server — any OPC UA client, including commercial SCADA tools, can consume streams; verified with an OPC UA client end-to-end | ✅ |
| Smoothing | Not offered — DIP doesn't offer it either | ➖ parity |
| Message authentication | HMAC-SHA256 signatures in the Part 14 SecurityHeader — publisher-side, boundary-relay-side, verified in Python and Java. **Beyond DIP** (which has none) | ✅ better |
| Access control / archiving | Not offered — DIP doesn't offer them either | ➖ parity |

**Beyond DIP** (no DIP equivalent): standard wire format readable by any OPC UA
Part 14 implementation (proven against open62541 and supernova's C++ engine,
byte-identical encoders); native zero-code publishing from every quasar-family
OPC UA server; per-field quality; live namespace observability; message-loss
detection via sequence numbers.
