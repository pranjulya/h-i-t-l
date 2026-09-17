"""Agent orchestrator unit tests: bounded calls, strict validation, fail-closed."""

from __future__ import annotations

import pytest

from hitl_ops.agent.orchestrator import (
    AgentOrchestrator,
    DisabledLLMProvider,
    ScriptedProvider,
)
from hitl_ops.domain.errors import DomainError


async def test_disabled_provider_fails_closed() -> None:
    orchestrator = AgentOrchestrator(DisabledLLMProvider())
    with pytest.raises(DomainError) as excinfo:
        await orchestrator.propose("scale up")
    assert excinfo.value.code == "LLM_UNAVAILABLE"
    assert excinfo.value.retryable is True


async def test_valid_proposal_is_bounded_and_validated() -> None:
    provider = ScriptedProvider(
        {
            "tool": "scale_service",
            "parameters": {"environment": "staging", "service": "api", "replicas": 4},
            "rationale": "x" * 5000,
        }
    )
    proposal = await AgentOrchestrator(provider).propose("scale up")
    assert proposal.tool == "scale_service"
    assert len(proposal.rationale) <= 2000


async def test_oversized_request_is_rejected_before_provider_call() -> None:
    provider = ScriptedProvider({"tool": "scale_service", "parameters": {}})
    with pytest.raises(DomainError) as excinfo:
        await AgentOrchestrator(provider).propose("x" * 5000)
    assert excinfo.value.code == "VALIDATION_FAILED"


async def test_unknown_tool_is_rejected() -> None:
    provider = ScriptedProvider({"tool": "rm_rf", "parameters": {}})
    with pytest.raises(DomainError) as excinfo:
        await AgentOrchestrator(provider).propose("clean up")
    assert excinfo.value.code in ("VALIDATION_FAILED", "UNKNOWN_TOOL")


async def test_extra_fields_in_proposal_are_rejected() -> None:
    provider = ScriptedProvider(
        {
            "tool": "scale_service",
            "parameters": {
                "environment": "staging",
                "service": "api",
                "replicas": 4,
                "force": True,
            },
        }
    )
    with pytest.raises(DomainError):
        await AgentOrchestrator(provider).propose("scale up")


async def test_unstructured_output_is_rejected() -> None:
    provider = ScriptedProvider("just do the thing")
    with pytest.raises(DomainError):
        await AgentOrchestrator(provider).propose("do it")


async def test_provider_crash_maps_to_llm_unavailable() -> None:
    class ExplodingProvider:
        async def propose(self, request_text: str, context: dict) -> dict:
            raise RuntimeError("socket blown")

    with pytest.raises(DomainError) as excinfo:
        await AgentOrchestrator(ExplodingProvider()).propose("go")
    assert excinfo.value.code == "LLM_UNAVAILABLE"
