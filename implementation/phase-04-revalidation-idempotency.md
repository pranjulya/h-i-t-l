# Phase 04 — Revalidation, Concurrency, and Idempotency

**Status:** TESTED — implemented with race/crash/idempotency tests (one permit per revision, lease retake, digest staleness, policy/authorization refresh), Ruff, and strict mypy; `REVIEWED`/`COMPLETE` follow the user end-to-end review gate.

## Goal

Implement exclusive execution claims, request/command idempotency, current approval/authorization/policy checks, digest recomputation, and live target precondition validation.

## Why

Approval describes a past decision. Execution is safe only if the exact intent and conditions remain valid and retries/races cannot create another operation.

## Prerequisites

Phase 03 `COMPLETE`; ADR-006 accepted; live-context interface and material preconditions defined per tool.

## Concepts

TOCTOU, compare-and-swap, row locks, uniqueness constraints, idempotency key/request hash, leases, precondition tokens, stale decisions, transaction retry.

## Files to create or modify

- Create `application/revalidation.py`, idempotency service within `application/commands.py`, live target query contract in `adapters/base.py`.
- Extend repositories/ORM and migration for execution claims/idempotency indexes.
- Create unit `test_revalidation.py`, `test_idempotency.py`; integration `test_concurrent_claim.py`, `test_idempotency_transactions.py`, `test_policy_auth_refresh.py`.
- Modify state guards for `REVALIDATING`, `STALE`, and `EXPIRED`.

## Architecture impact

Creates the final deterministic gate before privileged execution. PostgreSQL is the correctness mechanism; no Redis dependency is required.

## Data flow

Eligible revision → atomic execution claim → digest/TTL/approval/current-role checks → current risk/policy → live typed target snapshot → obligation/precondition comparison → short-lived process-local execution permit or terminal stale/expired result.

## Edge and failure scenarios

Changed parameter/digest, expired approval during claim, revoked approver, stricter policy, target replaced/version changed, service condition degraded, duplicate key with changed body, two workers, worker crash during claim, deadlock/serialization retry, slow live lookup exceeding claim deadline.

## Tests

One-byte material changes stale approval; current role/policy changes are observed; two concurrent claims yield one permit; same idempotency key/body replays and changed body conflicts; crash/expired claim recovery cannot yield two permits; PostgreSQL transaction retries are bounded.

## Acceptance criteria

Every permit references the exact digest and operation key; no client can submit a permit; a failed gate moves to the specified state with reason; all races have deterministic winners; Redis absence does not change correctness; target queries are read-only and bounded.

## Learning outcomes

Explain TOCTOU, semantic idempotency, optimistic versus pessimistic concurrency, why a distributed lock is insufficient, and how precondition tokens narrow the approval-to-action gap.

## Interview questions

1. Why is approval expiry checked after the worker claims the row?
2. Why must the database constraint remain if Redis locks are added?
3. What makes two requests semantically identical for idempotency?

## Definition of Done

Unit/race/crash/idempotency tests pass repeatedly; database indexes/constraints are reviewed; revalidation ordering matches LLD; no provider mutation exists; metrics hooks are identified; learning material is complete; phase is `COMPLETE`.

