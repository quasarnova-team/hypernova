# API reference

## Python: `hypernova.Subscriber`

```python
Subscriber(name, *, registry=None, network=None,
           address=None, publisher_id=None, writer_group_id=None,
           dataset_writer_id=None, field_names=None)
```

Resolve `name` at the registry (`registry` URL, default
`$HYPERNOVA_REGISTRY` or `http://localhost:4850`; several comma-separated
URLs give DIP-style primary/secondary failover — lookups try them in order,
publishers register with every one) and receive its stream.
`network` asks the registry for that network's endpoint (relayed copies).
Passing `address` + ids skips the registry entirely. Successful lookups are
cached under `~/.cache/hypernova/` (override with `$HYPERNOVA_CACHE`), so a
subscriber restart works with the registry down; only a first-ever lookup
needs it. Raises `RegistryError` with a precise message otherwise.

Use as a context manager, or call `start()` / `stop()`:

| Member | Meaning |
|---|---|
| `updates(timeout=None)` | Iterator of `Update`s; with a `timeout`, stops after that many silent seconds |
| `get(timeout=5.0)` | Next `Update` or `TimeoutError` |
| `dropped_updates` | Updates discarded because the consumer was too slow (queue of 1000) |
| `undecodable_datagrams` | Datagrams on the port that were not valid UADP |

`Update`: `name`, `sequence_number`, `received_at`, and `values` — a dict of
field name → `FieldValue`.

`FieldValue`: `type` (`BuiltinType`), `value` (scalar or list for arrays),
`status` (OPC UA StatusCode, `is_good` property), `source_timestamp`
(`source_datetime` property).

## Python: `hypernova.Publisher`

```python
Publisher(name, *, fields, address, publisher_id, writer_group_id,
          dataset_writer_id, publisher_id_type="UINT16", registry=None,
          description="", endpoints=None, ttl=1, register=True)
```

`fields` is an ordered dict of field name → type name (`"DOUBLE"`,
`"INT32[]"`, ... — the order defines the wire order). Construction registers
the publication (`replace=true`) unless `register=False`; an unreachable
registry only clears `publisher.registered` — publishing proceeds.

`send(**values)` publishes one DataSetMessage; every declared field is
required (mismatches raise `ValueError` naming the offender). Per-field
status: `send(x=5, _status={"x": STATUS_BAD})`; timestamp override:
`send(x=5, _timestamp=datetime(...))`. Fields are DataValue-encoded (value +
status + source time). `renew()` refreshes the registry lease.

## Wire codec: `hypernova.wire`

`encode_network_message(message, *, datavalue_fields=True)` /
`decode_network_message(data)` — the UADP profile spoken by supernova and
open62541 (see [ARCHITECTURE.md](../ARCHITECTURE.md)). Raises `WireError`
with a diagnostic on anything unsupported. Scalars and one-dimensional
arrays of: Boolean, SByte, Byte, Int16, UInt16, Int32, UInt32, Int64,
UInt64, Float, Double, String, DateTime.

## Registry REST

| Method + path | Meaning |
|---|---|
| `GET /api/publications` | All publications with live state (rate, staleness, loss) |
| `GET /api/publications/{name}` | One publication + last values (quality, source time) |
| `PUT /api/publications/{name}` | Register; body per below; 409 on name/stream collision, 400 with a precise message on malformed input |
| `POST /api/publications/{name}/renew` | Refresh the lease |
| `DELETE /api/publications/{name}` | Remove |
| `GET /api/lookup/{name}?network=N` | Subscriber-facing coordinates (per-network endpoint if registered) |
| `GET /api/health` | Service, version, counts |
| `GET /api/types` | Accepted field type names |

Registration body:

```json
{
  "address": "opc.udp://239.10.0.1:14840",
  "publisherId": 42, "publisherIdType": "UINT16",
  "writerGroupId": 100, "dataSetWriterId": 1,
  "fields": [{"name": "temperature", "type": "DOUBLE"}],
  "endpoints": {"gpn": "opc.udp://10.147.0.5:24840"},
  "description": "optional", "leaseSeconds": 600, "replace": false
}
```

## CLI

```
hypernova registry [--bind H] [--port 4850] [--store FILE]
hypernova relay CONFIG.json
hypernova browse [--registry URL]
hypernova sub NAME [--network N] [--count N] [--timeout S] [--registry URL]
hypernova pub NAME --address A --publisher-id P --writer-group-id W
              --dataset-writer-id D --field n=TYPE... --value n=V...
              [--interval S] [--count N] [--ramp] [--registry URL]
hypernova register NAME --address A --publisher-id P --writer-group-id W
              --dataset-writer-id D --field n=TYPE...
              [--endpoint NET=ADDR]... [--replace] [--registry URL]
```

`sub` waits (within `--timeout`) for a name that isn't registered yet —
subscribers may start before their publishers. Relay config format is
documented in `hypernova/relay.py` and [deployment.md](deployment.md).
