# Deployment across real network boundaries

The reference topology (mirroring how DIP is actually deployed at CERN —
one well-known central service, per-flow firewall exceptions):

```
technical network (e.g. ATCN)              other networks (GPN, ...)
──────────────────────────────             ─────────────────────────
field servers ──multicast──► local            consumers, by name
     │        (fan-out costs nothing)               ▲
     │                                              │ unicast
     └────────► relay host ────pinhole──────────────┘
                    ▲
   registry: one host, reachable from both sides (HTTP)
```

## Rules of thumb

1. **Multicast never crosses a boundary.** Inside one network it is the
   cheapest possible fan-out; between networks it is neither routable in
   practice nor auditable. Don't fight that — relay.
2. **One relay route per stream, one config file per boundary.** The relay's
   JSON *is* the pinhole inventory: reviewable, versionable, exactly like
   DIP's firewall port ranges but per-stream and self-documenting
   (`GET :4860/api/health` shows every route with live counters).
3. **The registry is HTTP on one port** — the only thing that needs to be
   reachable from everywhere, like the DIP name server today. It is advisory:
   its outage stops browsing and *first-ever* lookups, never data. For
   DIP-style redundancy run two and set
   `HYPERNOVA_REGISTRY=http://reg1:4850,http://reg2:4850` — publishers
   register with both, lookups fail over in order.
4. **Register per-network endpoints** so lookup answers depend on where the
   subscriber sits:

   ```bash
   hypernova register site/area1/pump7/env \
       --address opc.udp://239.10.0.1:14840 \
       --endpoint officenet=opc.udp://10.147.0.5:24840 \
       --publisher-id 42 --writer-group-id 100 --dataset-writer-id 1 \
       --field temperature=DOUBLE --field fanSpeed=INT32
   ```

   Consumers on the technical network subscribe to the multicast group;
   consumers elsewhere run `hypernova sub ... --network officenet` and get the
   relayed unicast endpoint.

## Relay configuration

```json
{
  "routes": [
    {
      "name": "site/area1/pump7/env",
      "from": "opc.udp://239.10.0.1:14840",
      "to": ["opc.udp://10.147.0.5:24840", "opc.udp://10.147.0.6:24840"],
      "ttl": 1
    }
  ],
  "health_port": 4860
}
```

The relay never decodes what it forwards (it cannot corrupt a stream) and is
stateless: restart is recovery. Run one instance per boundary host under
systemd/supervisor; watch `idleSeconds` per route for silence.

## Address plan

Pick one multicast range per network (e.g. `239.192.x.y` — organization-local
scope), one group+port per publishing server, distinct
`publisherId` per server, and writer-group/dataset-writer ids per stream —
the same discipline as supernova's config.xml. The registry's collision
refusal enforces uniqueness of both names and stream tuples.

## Security posture

Frames are not yet signed (Part 14 SecurityHeader is the v1 hardening item).
Keep multicast inside trusted networks; every inter-network flow is an
explicit relay route on a controlled host. This is the same exposure DIP has
today (no access control), minus long-lived sessions, plus an audit trail.

## Failure drill

| Symptom | Where to look |
|---|---|
| name unknown at subscribe | `hypernova browse` — is it registered? `sub` waits within `--timeout` for late registration |
| browser row stale on the technical network | publisher down, or wrong ids in the registration (the registry listens on the group — if the publisher is up, compare ids) |
| stale only beyond the boundary | relay route: check `:4860/api/health` counters and `idleSeconds` |
| values but wrong field names | field list in the registration is out of order vs the publisher's config |
| registry down | data unaffected; browsing and first lookups gone — restart it; state is `registry.json` |

`--interface` (pub/sub CLI, `interface=` in the API, `HYPERNOVA_INTERFACE` env):
pins multicast egress/membership to one local address — required on
dual-homed hosts (a machine with feet in two networks) and for
loopback-only testing (`--interface 127.0.0.1`).
