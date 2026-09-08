# Phase 03 — Approval State Machine, OIDC, and RBAC

**Status:** NOT_STARTED

## Goal

Implement legal lifecycle transitions, authenticated actor context, current scoped role authorization, append-only approval decisions, critical two-step distinctness, rejection, cancellation, and expiry.

## Why

Human presence is not a control unless identity, authority, exact approved content, timing, separation of duties, and race behavior are enforced server-side.

## Prerequisites

Phase 02 `COMPLETE`; ADR-005 and ADR-008 accepted; identity test issuer and role scope format selected.

## Concepts

Finite-state machines, command guards, OIDC/JWT validation, RBAC/ABAC scope, separation of duties, append-only decisions, database time, optimistic concurrency.

## Files to create or modify

- Create `domain/state_machine.py`, `application/commands.py`, `infrastructure/identity.py`.
- Create `application/administration.py`: Administrator-guarded commands that grant/revoke role assignments and register/activate source-control-reviewed policy bundle versions; every change is audited (LLD §10).
- Extend ORM/repositories; migration for approvals and role assignments.
- Create `tests/unit/test_state_machine.py`, `test_authorization.py`; integration `test_approval_transactions.py`, `test_approval_expiry.py`, `test_role_administration.py`.
- Add internal service contracts; public routes wait until Phase 06.

## Architecture impact

Adds human authority and identity boundaries while keeping transitions independent from HTTP. Database guards and service logic jointly protect distinct approvers and one-winner races.

## Data flow

Validated identity → tenant-scoped intent lock → expected state/version/digest → current role/scope and distinctness checks → append decision → transition → same-transaction audit outbox row for every committed transition (a real outbox record from this phase onward; delivery metadata and publication complete in Phase 07).

## Edge and failure scenarios

Expired/forged token, wrong audience, revoked/expired role, cross-tenant intent, requester self-approval, same actor at L1/L2, L2 before L1 commit, concurrent reject/approve, cancellation after claim, duplicate command, approval TTL boundary, non-administrator administration attempt.

## Tests

Every legal/illegal transition; HIGH and MEDIUM single approval; CRITICAL ordered two-step; self/duplicate/wrong-scope denial; server actor overrides body claims; database-clock expiry; concurrency proves one committed winner; unauthorized response does not disclose foreign intent; Administrator-only role/policy administration is enforced and audited.

## Acceptance criteria

Clients cannot set state or actor; all transitions use expected version and audit command ID; decisions are immutable; current authorization and approval snapshots both persist; expiry and cancellation follow the authoritative state table; role/policy administration commands require the Administrator role, append audited change events, and register policy versions that later evaluations read.

## Learning outcomes

Explain authentication versus authorization, RBAC plus resource scope, separation of duties, append-only decisions, and transactional race resolution.

## Interview questions

1. Why store an approver role snapshot if current authorization is checked later?
2. How do row locks and state versions solve different problems?
3. Why must L2 wait until L1 commits?

## Definition of Done

State/identity/authorization/concurrency tests pass; migration enforces material uniqueness; identity errors are sanitized; review traces every transition to the state document; learning scenarios demonstrate abuse cases; phase is `COMPLETE`.

