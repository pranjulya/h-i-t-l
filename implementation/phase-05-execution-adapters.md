# Phase 05 — Execution Service, Adapter, and Reconciliation

**Status:** TESTED — implemented with shared adapter contract tests (all five tools), crash/ambiguity/reconciliation integration tests, deterministic demo adapter, Ruff, and strict mypy; `REVIEWED`/`COMPLETE` follow the user end-to-end review gate.

## Goal

Execute valid permits through one typed demo infrastructure adapter, classify outcomes, prevent unsafe retries, and reconcile ambiguous provider results.

## Why

The privileged boundary needs a small explicit contract that cannot be driven by arbitrary model output and can recover when the network hides whether a mutation occurred.

## Prerequisites

Phase 04 `COMPLETE`; ADR-007 and ADR-010 accepted; demo adapter chosen; provider operation-key and status-lookup behavior documented.

## Concepts

Ports/adapters, least privilege, operation identity, exactly-once illusion versus at-most-once effects, timeout ambiguity, reconciliation, conditional mutation, compensating action limits.

## Files to create or modify

- Complete `adapters/base.py`; create `adapters/demo.py`.
- Create `application/execution.py`, `application/reconciliation.py`, worker entry point.
- Extend execution ORM/repository and migration fields/indexes.
- Create unit `test_execution_classification.py`; contract `test_adapter_contract.py`; integration `test_execution_worker.py`, `test_reconciliation.py`.

## Architecture impact

Introduces the only privileged infrastructure path. Worker identity and network access are separate from API/agent. The adapter accepts typed commands, never tool names plus arbitrary dictionaries.

## Data flow

Execution permit → persist `EXECUTING` and attempt → typed adapter call with operation/precondition key → confirmed success/failure or ambiguous result → persist outcome/provider ID → unknown outcome scheduled for status reconciliation.

## Edge and failure scenarios

Failure before send, timeout after send, provider 5xx, duplicate delivery, unsupported idempotency, stale precondition, process crash after provider success before commit, result too large/sensitive, provider status unavailable, permanent unknown requiring operator escalation.

## Tests

Shared adapter contract for all five tools; no dynamic URL/shell path; retries only pre-send or proven safe; crash points before/after send/response/commit; duplicate worker delivery; reconciliation maps provider evidence to success/failure and preserves unknown when evidence is insufficient.

## Acceptance criteria

At most one logical provider operation per intent revision; every adapter call has a stable operation key and bounded timeout; unknown is never guessed or blindly retried; execution results are sanitized; credentials are isolated; direct imports/calls from agent/API are structurally prohibited.

## Learning outcomes

Explain why “exactly once” requires provider cooperation, when retry is unsafe, how reconciliation differs from retry, and why typed adapters are a security boundary.

## Interview questions

1. What should happen when delete succeeded but its response was lost?
2. Why persist `EXECUTING` before sending the provider request?
3. When is compensation safer or less safe than reconciliation?

## Definition of Done

Adapter contract, execution, crash, and reconciliation tests pass; demo behavior is deterministic; credentials/redaction review passes; failure semantics match state machine; no real cloud mutation is needed for V1 proof; learning scenario is complete; phase is `COMPLETE`.

