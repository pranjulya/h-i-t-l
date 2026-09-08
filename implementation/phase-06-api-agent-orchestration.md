# Phase 06 — API and Agent Orchestration

**Status:** NOT_STARTED

## Goal

Expose stable FastAPI contracts for intents, decisions, cancellation, status, and events, plus a bounded LLM-assisted proposal path that delegates to the same application services.

## Why

Users need a usable interface, but transport and model behavior must remain replaceable edges around the already-proven control plane.

## Prerequisites

Phase 05 `COMPLETE`; API error contract approved; one LLM provider adapter/configuration selected; provider calls can be disabled in tests.

## Concepts

Hexagonal boundaries, Pydantic request/response contracts, dependency injection, stable errors, polling/resource status, model tool schemas, prompt injection containment, bounded provider calls.

## Files to create or modify

- Create `api/dependencies.py`, `api/intents.py`, `api/approvals.py`, `api/admin.py`; modify app/router/error modules.
- Create `agent/orchestrator.py` and provider boundary; reuse strict `agent/schemas.py`.
- Create unit `test_agent_orchestrator.py`; API `test_intent_routes.py`, `test_approval_routes.py`, `test_admin_routes.py`, `test_error_contract.py`; integration `test_agent_to_intent.py`.
- Own `GET /intents/{intent_id}/events` serving sanitized persisted events with cursor pagination; Phase 07 upgrades it to the hash-linked audit store view.
- Expose the Administrator role-assignment and policy-bundle contracts against the Phase 03 administration commands.
- Update OpenAPI descriptions and README examples without live secrets.

## Architecture impact

Adds two entry paths—direct structured request and agent-assisted request—that converge before canonicalization. Neither path invokes the execution adapter.

## Data flow

Authenticated request → validation/idempotency → optional bounded LLM proposal → strict proposal validation → existing intent/risk/policy flow → approval/status resource response. Workers, not public callers, initiate eligible execution.

## Edge and failure scenarios

Malformed body, unknown/extra fields, model refusal/timeout/invalid tool, prompt injection in incident data, forged actor/tenant fields, oversized rationale, idempotent replay, stale decision version/digest, cross-tenant lookup, worker status lag.

## Tests

OpenAPI/schema snapshots; stable status/error mappings; authenticated tenant isolation; model cannot create unknown tools/fields; invalid model output creates no executable intent; direct and agent inputs yield identical canonical domain results; no public execute route exists.

## Acceptance criteria

All LLD endpoints and error codes work; actor/tenant come only from auth; mutation routes require idempotency keys; model calls are bounded and observable; API modules contain no risk/policy/state rules; adapter is unreachable from API/agent dependency graph.

## Learning outcomes

Explain why API and LLM are adapters, how strict structured output reduces but does not remove validation, and why status polling is safer than a public execute command.

## Interview questions

1. Why retain a direct structured-intent API?
2. What happens when the model proposes a valid schema but unsafe action?
3. Which errors should be retryable by an API client?

## Definition of Done

Unit/API/integration/security tests pass; OpenAPI matches LLD; model fixtures include hostile input; no infrastructure credential/call path exists; error/redaction review passes; docs and learning content are current; phase is `COMPLETE`.

