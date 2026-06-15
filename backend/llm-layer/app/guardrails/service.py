from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.guardrails.client import GuardrailClient
from app.guardrails.mock_client import MockGuardrailClient


@lru_cache(maxsize=1)
def get_guardrail_client() -> GuardrailClient:
    return MockGuardrailClient(get_settings())
