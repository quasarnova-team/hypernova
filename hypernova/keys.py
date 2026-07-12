"""Key files for the hypernova signing profile: one shared key per stream (or
per boundary), distributed operationally — never through the registry.

A key file holds 32+ bytes of hex (whitespace tolerated). Create one with:

    python -c "import secrets; print(secrets.token_hex(32))" > stream.key
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["load_key"]


def load_key(path: str | Path) -> bytes:
    """Read a hex key file; raises ValueError with a fix-it message."""
    try:
        text = Path(path).read_text().strip()
    except OSError as error:
        raise ValueError(f"cannot read key file {path}: {error}") from None
    try:
        key = bytes.fromhex(text)
    except ValueError:
        raise ValueError(
            f"key file {path} is not hex; generate one with "
            "python -c \"import secrets; print(secrets.token_hex(32))\"") from None
    if len(key) < 16:
        raise ValueError(f"key file {path} holds only {len(key)} bytes; use >= 16 (32 recommended)")
    return key
