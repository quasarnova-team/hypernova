# Security

## The model

hypernova's trust model matches the deployment model: **inside a technical
network, frames are trusted like every other protocol on that network**
(DIP's position for two decades); **anything that crosses a boundary is
signed**. Three places can sign or verify:

- a **Publisher** signs at the source (`sign_key=`, `--sign-key-file`),
- the **relay** signs at the boundary (`"sign_key_file"` on a route) — the
  recommended default: publishers inside stay untouched (including C++
  supernova servers), and exactly the frames that leave the network carry a
  signature,
- a **Subscriber** verifies (`verify_key=`, `--verify-key-file`) — and once
  it has a key it *requires* signatures, so an attacker cannot bypass
  verification by simply omitting the SecurityHeader.

```bash
python -c "import secrets; print(secrets.token_hex(32))" > stream.key
# boundary relay route:  "sign_key_file": "/etc/hypernova/stream.key"
hypernova sub atlas/dcs/atca/crate1/env --network gpn --verify-key-file stream.key
```

## The wire format (hypernova signing profile v1)

Signed frames are standard OPC UA Part 14 UADP: the ExtendedFlags1 security
bit, a SecurityHeader (signed flag, `securityTokenId`, 8-byte random nonce),
and a signature appended to the NetworkMessage. The signature is
**HMAC-SHA256 over the entire frame** (all headers and payload), 32 bytes,
with a pre-shared key per stream or per boundary.

Honesty note: OPC UA Part 14 defines its own SecurityPolicies
(AES-CTR-based) keyed through Security Key Services. hypernova v1 carries
its signature in the standard frame structure but uses HMAC-SHA256 with
pre-shared key files — simpler to deploy, verifiable in four lines of any
language, but **not interoperable with other stacks' Part 14 security**
until a full SecurityPolicy lands (roadmap). Unsigned interop is unaffected.

## Properties

| Threat | Coverage |
|---|---|
| Forged values (spoofed publisher ids) | ✅ rejected — every bit of a signed frame is authenticated (a test flips every single bit and asserts rejection) |
| Tampering in flight | ✅ rejected |
| Replay | 🟡 **no cryptographic replay protection**: a captured signed frame replays verbatim and verifies. Sequence numbers let consumers *notice* duplicates/reordering, but nothing rejects a replay. Acceptable for periodic telemetry (the next sample supersedes it); do not use the sign-only profile where a replayed value is itself dangerous |
| Eavesdropping | ❌ frames are not encrypted — telemetry is treated as readable on any network it reaches |
| Key distribution | operational: key files on publisher/relay/subscriber hosts. **Keys never pass through the registry** — it can browse signed streams (shown "signed, unverified") but never holds secrets |

## Registry and relay hardening

The registry parses attacker-reachable input on both faces (HTTP and UDP).
Adversarially reviewed twice; the control plane guarantees are regression-locked:

- malformed or hostile registrations cannot corrupt or brick it (validation
  before persistence, quarantine of corrupt store files, per-endpoint bind
  isolation),
- hostile datagrams cannot crash it or its browser (700k-datagram fuzz,
  clamped timestamps, NaN-safe JSON),
- publication names are escaped everywhere they render.

A signing relay route additionally **validates** every frame it forwards
(it must decode to re-encode) and drops undecodable input, counting drops
in its health stats.
