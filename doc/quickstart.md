# Quick start

Ten minutes: a registry, a publisher, a subscriber, and the browser — all on
one machine.

## 1. Install

```bash
pip install hypernova          # Python >= 3.10
```

## 2. Start the phonebook

```bash
hypernova registry
```

The registry now answers on `http://localhost:4850` — REST for programs, the
live browser for you. (`--store registry.json` is the default persistence;
`--store ''` for in-memory.)

## 3. Publish something

```bash
hypernova pub demo/hello \
    --address opc.udp://239.10.0.9:14840 \
    --publisher-id 7 --writer-group-id 1 --dataset-writer-id 1 \
    --field counter=INT32 --field greeting=STRING \
    --value counter=0 --value greeting=hello --ramp --interval 0.5
```

This registers `demo/hello` with the registry and publishes a ramping counter
twice a second as OPC UA Part 14 UADP multicast. Every field carries a
status code and a source timestamp.

## 4. Subscribe — by name, nothing else

```bash
hypernova sub demo/hello
```

```
18:04:11  demo/hello  seq=42  counter=41  greeting='hello'
18:04:12  demo/hello  seq=43  counter=42  greeting='hello'
```

The name was resolved by the registry; the datagrams came straight from the
publisher (multicast — the registry is not in the data path). Stop the
registry and the subscription keeps running; restart the subscriber and it
still works, from its coordinate cache.

## 5. Browse

Open <http://localhost:4850>: the namespace, live values with quality and
source time, message rate, staleness, loss counters — and for every
publication a copy-paste Python subscriber and a supernova `DataSetReader`
XML snippet.

## 6. The same, in Python

```python
from hypernova import Publisher, Subscriber

with Publisher("demo/py", fields={"level": "DOUBLE"},
               address="opc.udp://239.10.0.9:14841",
               publisher_id=8, writer_group_id=1, dataset_writer_id=1) as pub:
    pub.send(level=3.14)

with Subscriber("demo/py") as sub:
    update = sub.get(timeout=5)
    print(update.values["level"].value)
```

## 7. A C++ server as publisher (no code)

Any supernova server publishes natively — config.xml only:

```xml
<PubSub publisherId="42" publisherIdType="UInt16">
  <Connection address="opc.udp://239.10.0.9:14842">
    <WriterGroup id="100" publishingIntervalMs="100">
      <DataSetWriter id="1">
        <Field source="PS1.temperature"/>
      </DataSetWriter>
    </WriterGroup>
  </Connection>
</PubSub>
```

Tell the registry about it (the server doesn't self-register — an operator
or deployment script does, once):

```bash
hypernova register atlas/dcs/my-server/env \
    --address opc.udp://239.10.0.9:14842 \
    --publisher-id 42 --writer-group-id 100 --dataset-writer-id 1 \
    --field temperature=DOUBLE
```

From that moment it's browsable and subscribable by name like everything
else. The full two-network demo (relay pinhole included) is one command:
`demo/run_demo.sh`.
