"""Request body validation unit tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hitl_ops.api.intents import AgentIntentBody


def test_agent_request_rejects_whitespace_only() -> None:
    with pytest.raises(ValidationError):
        AgentIntentBody(request="   ")


def test_agent_request_accepts_non_blank_text() -> None:
    body = AgentIntentBody(request="restart the billing API in staging")
    assert body.request == "restart the billing API in staging"
