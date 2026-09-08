"""Agent orchestrator: bounded, untrusted model proposals behind strict schemas.

The provider is a replaceable edge. Model output is validated against the
allow-listed tool schemas; invalid or refused proposals create no intent.
The default provider is disabled until configured (LLM_UNAVAILABLE), and the
model never receives credentials or infrastructure access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from hitl_ops.agent.schemas import (
    ParameterValidationError,
    UnknownToolError,
    _StrictModel,
    parse_tool_parameters,
)
from hitl_ops.domain.errors import DomainError


class ProposalProvider(Protocol):
    """Single bounded call; returns an untrusted candidate tool proposal."""

    async def propose(self, request_text: str, context: dict[str, Any]) -> dict[str, Any]: ...


class DisabledLLMProvider:
    """Fail-closed provider used when no LLM is configured."""

    async def propose(self, request_text: str, context: dict[str, Any]) -> dict[str, Any]:
        raise DomainError(
            "LLM provider is not configured",
            code="LLM_UNAVAILABLE",
            http_status=503,
            retryable=True,
        )


class ScriptedProvider:
    """Deterministic provider for tests and demos."""

    def __init__(self, proposal: dict[str, Any]) -> None:
        self._proposal = proposal

    async def propose(self, request_text: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._proposal


@dataclass(frozen=True, slots=True)
class BoundedProposal:
    tool: str
    parameters: _StrictModel
    rationale: str


MAX_REQUEST_CHARS = 4000
MAX_CONTEXT_ITEMS = 20


class AgentOrchestrator:
    def __init__(self, provider: ProposalProvider) -> None:
        self._provider = provider

    async def propose(
        self, request_text: str, context: dict[str, Any] | None = None
    ) -> BoundedProposal:
        if len(request_text) > MAX_REQUEST_CHARS:
            raise DomainError(
                "request text exceeds the bounded limit", code="VALIDATION_FAILED", http_status=422
            )
        bounded_context = dict(list((context or {}).items())[:MAX_CONTEXT_ITEMS])
        try:
            raw = await self._provider.propose(request_text, bounded_context)
        except DomainError:
            raise
        except Exception as exc:  # provider transport failures
            raise DomainError(
                "LLM provider call failed", code="LLM_UNAVAILABLE", http_status=503, retryable=True
            ) from exc
        if not isinstance(raw, dict):
            raise DomainError(
                "model proposal was not a structured object",
                code="VALIDATION_FAILED",
                http_status=422,
            )
        tool = raw.get("tool")
        parameters = raw.get("parameters")
        if not isinstance(tool, str) or not isinstance(parameters, dict):
            raise DomainError(
                "model proposal must include tool and parameters",
                code="VALIDATION_FAILED",
                http_status=422,
            )
        rationale = str(raw.get("rationale", ""))[:2000]
        # Strict validation against the allow-list; extra/unknown anything fails.
        try:
            validated = parse_tool_parameters(tool, parameters)
        except UnknownToolError as exc:
            raise DomainError(
                "tool is not on the allow-list", code="UNKNOWN_TOOL", http_status=422
            ) from exc
        except ParameterValidationError as exc:
            raise DomainError(
                "proposal parameters failed validation",
                code="VALIDATION_FAILED",
                http_status=422,
            ) from exc
        return BoundedProposal(tool=tool, parameters=validated, rationale=rationale)
