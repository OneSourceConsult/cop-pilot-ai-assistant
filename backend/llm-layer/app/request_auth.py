"""Request-local credentials; never stored in conversation state or global clients."""

from contextvars import ContextVar


incoming_bearer: ContextVar[str | None] = ContextVar("incoming_bearer", default=None)


def parse_bearer(value: str | None) -> str | None:
    if value is None:
        return None
    parts = value.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ValueError("Expected an Authorization: Bearer header.")
    return "Bearer " + parts[1]
