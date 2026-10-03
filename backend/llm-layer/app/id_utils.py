from __future__ import annotations

import os
import threading
import time
import uuid

_LOCK = threading.Lock()
_LAST_TIMESTAMP_MS = -1
_SEQUENCE = 0


def uuid7() -> str:
    """Return a UUIDv7 string without depending on Python 3.14+."""

    global _LAST_TIMESTAMP_MS, _SEQUENCE

    timestamp_ms = int(time.time_ns() // 1_000_000)
    with _LOCK:
        if timestamp_ms < _LAST_TIMESTAMP_MS:
            timestamp_ms = _LAST_TIMESTAMP_MS

        if timestamp_ms == _LAST_TIMESTAMP_MS:
            _SEQUENCE = (_SEQUENCE + 1) & 0x0FFF
            if _SEQUENCE == 0:
                while timestamp_ms <= _LAST_TIMESTAMP_MS:
                    timestamp_ms = int(time.time_ns() // 1_000_000)
        else:
            _SEQUENCE = int.from_bytes(os.urandom(2), "big") & 0x0FFF

        _LAST_TIMESTAMP_MS = timestamp_ms
        sequence = _SEQUENCE

    if timestamp_ms >= 1 << 48:
        raise ValueError("timestamp exceeds UUIDv7 range")

    random_tail = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (sequence << 64)
        | (0b10 << 62)
        | random_tail
    )
    return str(uuid.UUID(int=value))
