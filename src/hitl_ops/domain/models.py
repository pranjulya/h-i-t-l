"""Domain models for intent creation and snapshots."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hitl_ops.domain.enums import IntentSource, IntentState, ToolName


@dataclass(frozen=True, slots=True)
class CreateIntentCommand:
    tenant_id: str
    intent_id: uuid.UUID
    revision: int
    tool: ToolName
    canonical_parameters: dict[str, Any]
    intent_digest: str
    requester_id: str
    requester_rationale: str
    source: IntentSource
    raw_proposal: dict[str, Any] | None = None
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    actor_id: str = ""


@dataclass(frozen=True, slots=True)
class IntentSnapshot:
    tenant_id: str
    intent_id: uuid.UUID
    revision: int
    tool: ToolName
    intent_digest: str
    state: IntentState
    state_version: int
    created_at: datetime
