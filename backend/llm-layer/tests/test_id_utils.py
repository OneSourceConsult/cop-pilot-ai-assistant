from __future__ import annotations

from uuid import UUID

from app.id_utils import uuid7


def test_uuid7_returns_version_7_uuid() -> None:
    value = uuid7()
    parsed = UUID(value)

    assert parsed.version == 7
    assert parsed.variant == "specified in RFC 4122"


def test_uuid7_is_lexicographically_sortable_over_time() -> None:
    first = uuid7()
    second = uuid7()

    assert first < second
