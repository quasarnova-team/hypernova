# hypernova — a vision: DIP's proven shape on the industry standard

## What DIP got right

For over two decades, CERN's Data Interchange Protocol has done one thing
superbly: let control systems that don't trust each other's internals still
share live values. A publication has a name; a name server knows where it
lives; a subscriber asks by name and gets values with quality and timestamp.
More than 55,000 publications run on that idea today. The shape is right.

## Why a new generation

DIP's substance — a homegrown protocol on a homegrown library, maintained by
one community, opaque to every commercial and open-source tool — is where the
cost lives. Meanwhile the same laboratory standardized its device access on
OPC UA, and the OPC Foundation standardized exactly DIP's communication
pattern as **OPC UA Pub/Sub (Part 14)**: typed values, quality and
timestamps, periodic datasets, publish once / listen many, over UDP the
industry already ships.

hypernova is DIP's shape rebuilt on that standard substance:

- **Publications are OPC UA Pub/Sub datasets.** Typed fields, per-field
  status and source timestamp on the wire — DIP's value+quality+time, in an
  encoding twenty vendors implement.
- **The phonebook stays.** A registry answers "where does this name live?"
  and prevents collisions — DIPNS's job, plus one thing DIPNS never did:
  the registry *listens* to what it registers, so it is also the browser,
  showing live values, rates and staleness for the whole namespace.
- **Data never flows through a middleman inside a network.** Publishers
  multicast on their own segment; any number of local listeners pay nothing.
  Network boundaries are crossed the way DIP crosses them today — explicit,
  auditable, per-flow pinholes — by a small relay, not by a broker owning
  the data path.
- **C++ servers publish for free.** Every quasar/supernova OPC UA server is
  already a native hypernova publisher: five lines of config.xml, no code,
  no gateway process. The estate that feeds DIP through gateways today can
  feed hypernova directly.

## Principles

1. **The registry is advisory, never load-bearing.** Lookups and browsing
   need it; flowing data does not. A subscriber that knows its stream keeps
   working with the registry down.
2. **Fan-out is free where the network allows it** (multicast within a
   segment) **and explicit where it doesn't** (relay pinholes across
   boundaries). No hidden data paths.
3. **Interchange, not control.** Commands, setpoints and FSM transitions
   stay on classic, sessioned, secured OPC UA client/server. hypernova moves
   telemetry.
4. **Self-describing over configured.** Field names and types live in the
   registry and (progressively) on the wire, so a browser or a new consumer
   never needs someone else's config file to understand a stream.
5. **Standard first.** Anything hypernova adds beyond Part 14 (names,
   registration) is a thin, documented layer; the datagrams themselves are
   plain UADP that any Part 14 implementation can read — as proven against
   open62541 and against supernova's C++ engine.

## Where this goes

- **v0.x — the fabric**: registry + live browser, publish/subscribe by name
  in Python, supernova servers as native publishers, boundary relay,
  DIP-parity demonstrated end to end.
- **v1 — hardening**: Part 14 message signing before any frame crosses a
  boundary; structured field types; registry redundancy.
- **Later**: an MQTT flavor of the relay for consumers outside multicast
  reach entirely (cloud, offices); commercial SCADA tools as native consumers the day their vendors
  ships Part 14; DIP-to-hypernova bridging for staged migration.
