# Phase 02 — Deterministic Risk and Policy

**Status:** TESTED — implemented with matrix/property/persistence tests (fail-closed unknowns, monotonic escalation, seed bundle), Ruff, and strict mypy; `REVIEWED`/`COMPLETE` follow the user end-to-end review gate.

## Goal

Implement pure deterministic risk classification and versioned policy evaluation, persist their complete evidence, and route intents to auto-allow, approval, or block.

## Why

Probabilistic model reasoning must not decide authority. Separating harm classification from organizational permission keeps each rule explainable and independently testable.

## Prerequisites

Phase 01 `COMPLETE`; ADR-004 accepted; risk-policy model and minimum bands approved.

## Concepts

Pure functions, monotonic risk escalation, rule versions, policy obligations, decision evidence, fail-closed defaults, risk versus authorization.

## Files to create or modify

- Create `domain/risk.py`, `domain/policy.py`; extend domain result models.
- Extend ORM/repositories; create migration for risk/policy evaluations and seed the initial versioned policy bundle (bundle contents are source-control reviewed; activating new versions goes through the Phase 03 Administrator command).
- Create `tests/unit/test_risk.py`, `test_policy.py`, `test_risk_policy_matrix.py`; integration persistence test.
- Update state transition persistence only to record `RISK_EVALUATED`, `POLICY_EVALUATED`, and resulting route states via a minimal transition subset; Phase 03's `domain/state_machine.py` becomes the single legal-transition authority and subsumes these writes.

## Architecture impact

Adds two side-effect-free engines plus persistence orchestration. No API, human decisions, or adapter calls are introduced.

## Data flow

Canonical intent + bounded evaluated context → risk band/factors/rule version → policy disposition/route/obligations/TTL/version → legal state transitions and outbox-ready evidence.

## Edge and failure scenarios

Unknown tool/context, missing production metadata, overlapping rules, policy version absent, invalid TTL, policy attempts to lower tool minimum, storage failure mid-evaluation, repeated evaluation command.

## Tests

Complete tool/environment/magnitude matrix; property test that added escalation factors never reduce risk; `delete_resource` always CRITICAL; production mutations meet required minimum; unknown data blocks; persisted evidence reproduces decisions; repeated command is idempotent.

## Acceptance criteria

Locked routes are produced for every fixture; risk and policy versions/evidence persist; no rule depends on model rationale; unknown/invalid context fails closed; engines run without database/framework imports; resulting states match the state machine.

## Learning outcomes

Explain risk-policy separation, rule precedence, obligations, versioned decisions, monotonic escalation, and fail-closed design.

## Interview questions

1. Why can policy block a LOW-risk action?
2. Why should a tool minimum risk be impossible to lower?
3. How can historic decisions remain explainable after policy changes?

## Definition of Done

Matrix/property/integration tests pass; rules and versions are reviewable; persistence and transitions are atomic; no custom policy language exists; architecture review has no contradiction; paired learning material is complete; phase is `COMPLETE`.

