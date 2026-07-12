# The FX connection manager

OPC UA FX (Field eXchange, OPC UA Parts 80–84) is the industry's connection
layer on top of Pub/Sub: servers expose *functional entities* whose input and
output datasets are preconfigured, and a **connection manager** activates a
link between two servers at runtime by calling their `EstablishConnections`
methods. The data plane is ordinary Part 14 UADP — the same datagrams
hypernova speaks everywhere else.

`hypernova fx` is that connection manager, in the usual five-line spirit.
It targets any server exposing the FX pattern; today that is
[supernova](https://github.com/quasarnova-team/supernova) with an `<Fx>`
section in its configuration (see its
[FX documentation](https://github.com/quasarnova-team/supernova/blob/master/Documentation/source/Fx.rst)
and [FX-PARITY](https://github.com/quasarnova-team/supernova/blob/master/FX-PARITY.md)
for exactly which subset of the specification is implemented).

Requires the `[bridge]` extra (asyncua).

## Wire two servers together

```console
$ hypernova fx connect \
    --publisher  opc.tcp://cell-a:4841 --pub-entity control --pub-dataset env \
    --subscriber opc.tcp://cell-b:4841 --sub-entity control --sub-dataset setpoints \
    --address opc.udp://239.192.0.20:4841
```

What happens, in order:

1. `EstablishConnections` on the **publisher** server: it validates the
   request against its preconfigured `env` dataset, starts publishing to the
   given address, and replies with its wire coordinates
   (publisherId / writerGroupId / dataSetWriterId).
2. `EstablishConnections` on the **subscriber** server, with those
   coordinates as the `peer`: it starts listening and the values land in its
   own address space.
3. If the subscriber side refuses, the publisher side is closed again —
   no half-open links left behind.

Both servers grow a browsable `ConnectionEndpoint` object whose `Status`
reads `Operational`. The AutomationComponent is discovered automatically
(the object under `Objects` carrying an `EstablishConnections` method);
pass `--pub-component` / `--sub-component` to skip discovery.

## Observe and tear down

```console
$ hypernova fx status opc.tcp://cell-a:4841
CellA/control/hello: Operational  address=opc.udp://239.192.0.20:4841  dataset=publisher:control.env

$ hypernova fx close opc.tcp://cell-b:4841 hello
$ hypernova fx close opc.tcp://cell-a:4841 hello
```

Closed endpoints return to `Initial` and stay browsable; re-establishing
under the same name reuses them.

## Make the link a publication

An FX publisher stream is a perfectly normal Part 14 stream — so it can be a
first-class hypernova publication too:

```console
$ hypernova fx connect ... \
    --register http://registry:4850 --register-as site/area1/cell-a/env --network tn
```

The connection manager reads the dataset's field names and types from the
publisher's FX view and registers the stream: it shows up in the registry
browser with live values, and any `hypernova sub site/area1/cell-a/env` can
listen — the engineered link and the ad-hoc consumers share one wire.

## Semantics worth knowing

- **The connection manager holds no state.** Everything lives in the two
  servers (their endpoints) and, optionally, the registry. Losing the manager
  loses nothing.
- **Establish is validated server-side** against the preconfigured datasets —
  a connection manager cannot invent new wiring, only activate what each
  server's configuration declared.
- **Refusals are protocol-level**: a bad request returns `BadInvalidArgument`
  and the server logs the precise reason.
- **The argument encoding is a JSON projection** of the specification's
  connection-configuration structures (documented in supernova's FX-PARITY) —
  third-party FX connection managers will interoperate once the binary
  structures land there.
