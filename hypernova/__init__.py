"""hypernova — the next era of DIP: publish/subscribe data interchange for
control systems on OPC UA Pub/Sub (Part 14)."""

from hypernova.wire import (
    STATUS_BAD,
    STATUS_GOOD,
    STATUS_UNCERTAIN,
    BuiltinType,
    FieldValue,
    PublisherIdType,
    WireError,
)

__version__ = "1.1.0"


def __getattr__(name):
    if name in ("Publisher", "Subscriber", "Update", "RegistryError"):
        from hypernova import client
        return getattr(client, name)
    raise AttributeError(f"module 'hypernova' has no attribute {name!r}")


__all__ = [
    "BuiltinType",
    "FieldValue",
    "Publisher",
    "PublisherIdType",
    "RegistryError",
    "Subscriber",
    "Update",
    "WireError",
    "STATUS_GOOD",
    "STATUS_BAD",
    "STATUS_UNCERTAIN",
    "__version__",
]
