# hypernova architecture

## Components

```
                 ┌───────────────────────────┐
                 │   registry + browser      │   advisory plane:
                 │  names → streams, live    │   lookup, browse,
                 │  values, web UI, REST     │   collision control
                 └─────▲──────────▲──────────┘
        register/renew │          │ lookup / browse
                       │          │
   ┌───────────────────┴──┐    ┌──┴──────────────────────┐
   │ publishers           │    │ subscribers             │
   │ · supernova servers  │    │ · hypernova Subscriber  │
   │   (config.xml only)  │    │   (Python, by name)     │
   │ · hypernova          │    │ · any Part 14 stack     │
   │   Publisher (Python) │    │                         │
   └─────────┬────────────┘    └──────────▲──────────────┘
             │      UADP datagrams        │
             └──────────► network ────────┘
                (multicast in-segment;
                 relay pinholes across)
```

Four parts, one wire format:

| Part | Role | DIP analog |
|---|---|---|
| **registry** | names → stream coordinates; collision refusal; lease/renewal; REST + web UI; listens to registered groups and caches last values | DIP Name Server + DIP Browser |
| **client library** (`hypernova` Python package) | `Publisher` / `Subscriber` by name; values with per-field quality + source timestamp | DIP publisher/subscriber API |
| **relay** | joins streams on one interface, re-emits them to unicast targets or another group on another interface; counters + health | the firewall port-exception, made into a process |
| **supernova / any Part 14 publisher** | native C++ publishers via server configuration | DIP gateways (now unnecessary) |

## The wire

Plain OPC UA Part 14 UADP over UDP, version 1 — the exact profile supernova
ships and open62541 interoperates with:

- NetworkMessage: publisher id (UInt16 by default), group header (writer
  group id + sequence number), payload header, one or more DataSetMessages.
- DataSetMessage: data key frame; **DataValue field encoding** for
  hypernova-native publishers, so every field carries `(value, status,
  source timestamp)` — DIP's quality/timestamp with standard semantics.
  Variant field encoding (value only) is accepted on reception for
  compatibility with plain publishers.
- Sequence numbers at both levels allow loss detection; delivery is best
  effort by design — the next sample supersedes a lost one.

## Names

A **publication** is one DataSetMessage stream — one `(connection,
publisherId, writerGroupId, dataSetWriterId)` tuple — with an ordered list
of named, typed **fields**. Publication names are hierarchical,
slash-separated, DIP-style:

```
site/area1/pump7/env        fields: temperature (Double), fanSpeed (Int32)
```

The registry enforces uniqueness of names and of stream tuples per
connection, exactly as DIPNS prevents publication collisions.

## Flows

**Register.** A publisher (or an operator, for supernova servers) `PUT`s the
publication: name, connection address, ids, fields. The registry validates,
refuses collisions, stores, and — if the group is reachable — joins it and
starts caching last values. Registrations carry a lease; publishers renew,
stale entries are flagged (not deleted) in the browser.

**Subscribe by name.** The client library asks the registry for the name,
gets stream coordinates + field schema, joins the multicast group (or binds
the unicast port), decodes UADP, and delivers `{field: (value, quality,
timestamp)}` callbacks. Coordinates are cached on disk: a subscriber
restarts and keeps working with the registry down (Principle 1).

**Browse.** Humans open the web UI: the namespace tree, per-publication live
values, message rate, staleness, and a copy-paste subscriber snippet (and
supernova `DataSetReader` XML) for every publication.

**Cross a boundary.** A relay instance on an allowed host joins
`site/area1/pump7/env` on network A and re-emits it to declared unicast
targets (or a group) on network B. The pinhole is the relay's config — one
auditable file. Subscribers on B use the same client library with the B-side
coordinates (the registry stores per-network reachability, so lookup answers
differ by where you ask from).

## Failure model

| Failure | Behaviour |
|---|---|
| registry down | data keeps flowing; new lookups fail; cached subscribers unaffected; browser gone until restart (state rebuilt from registrations file + re-listening) |
| publisher restarts | sequence numbers reset; subscribers accept resets (documented wraparound/reset rule), staleness clears on first frame |
| subscriber lags/dies | nobody notices — no server-side state anywhere |
| relay dies | B-side streams go stale; browser staleness makes it visible; relay is stateless — restart is recovery |
| datagram loss | gap visible in sequence numbers; per-publication loss counters in subscriber stats |

## Security posture (stated plainly)

Frames can carry an HMAC-SHA256 signature in the Part 14 SecurityHeader —
signed at the publisher, or at the boundary relay the moment a stream leaves
the trusted network; a subscriber with a key rejects unsigned frames
outright. Encryption and a replay window do not exist yet; the honest limits
live in doc/security.md. Deployment guidance stands: multicast stays inside
trusted technical networks; relays — not raw multicast — cross boundaries,
so every inter-network flow is an explicit, per-stream decision on a
controlled host.

## Dependencies

Standard library only for the wire, sockets and relay. The registry's web
layer uses `aiohttp`; tests use `pytest`. No broker, no database — the
registry persists registrations as a JSON file, deliberately boring.
