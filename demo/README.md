# The demo: DIP's replacement, live on your machine

One command brings up the whole story:

```bash
demo/run_demo.sh
```

What starts (see `compose.yaml` for the picture):

- **field-server** — a real supernova C++ OPC UA server (UASDK backend),
  publishing `counter`/`temperature`/`label` at 10 Hz as UADP multicast on
  the `atcn` network. Config.xml only, no Pub/Sub code.
- **registry** — feet in both networks. It was told the publication's name
  once, listens to the multicast itself, and serves the browser at
  <http://localhost:4850> — live values, quality, rate, staleness.
- **relay** — the pinhole: joins the multicast on `atcn`, re-emits the
  datagrams unchanged to one unicast target on `gpn`. Counters at
  <http://localhost:4860/api/health>.
- **consumer** — on `gpn` only; it can't see the multicast, doesn't know the
  server, and still runs `hypernova sub site/area1/demo/env --network gpn`:
  the registry hands it the relayed endpoint and named values arrive.

The script registers the publication, waits, then **verifies every leg**
(registry live path, relay counters, consumer updates) and leaves the
topology running for exploration. Tear down with `demo/run_demo.sh down`.

Requirement: a built supernova publisher tree (`SUPERNOVA_TREE`, defaulting
to the supernova bench cell `e2e-pub-uasdk` — see `interop/run_interop.sh`
header for how these trees come to exist).
