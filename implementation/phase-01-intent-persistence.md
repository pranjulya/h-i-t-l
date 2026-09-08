# Phase 01 — Immutable Intent Domain and Persistence

**Status:** NOT_STARTED

## Goal

Implement strict models for the five tools, canonical intent revisions/digests, core enums, PostgreSQL persistence, migrations, and repositories without risk or approval behavior.

## Why

An approval is meaningful only when bound to an exact durable action. This phase creates that trust anchor before decisions depend on it.

## Prerequisites

Phase 00 `COMPLETE`; ADR-002 and ADR-003 accepted; PRD parameter rules reviewed.

## Concepts

Aggregate identity, immutable revision, canonical serialization, schema validation, digest binding, optimistic version, relational constraints, repository transaction boundaries.

## Files to create or modify

- Create `domain/enums.py`, `domain/models.py`, `domain/intent.py`, `agent/schemas.py`.
- Create `infrastructure/orm.py`, `infrastructure/repositories.py`.
- Create first domain Alembic migration for intents, transitions, idempotency records, and the minimal transactional audit outbox used by every later state change.
- Create `tests/unit/test_tool_schemas.py`, `test_canonical_intent.py`; integration `test_intent_repository.py`, `test_intent_migration.py`.
- Modify database test fixtures and learning concept notes.

## Architecture impact

Creates the durable aggregate shared by all later services. Domain models remain independent of SQLAlchemy and FastAPI; repositories translate between domain and ORM.

## Data flow

Untrusted proposal → exact tool Pydantic model → normalized values → canonical versioned JSON → SHA-256 digest → intent revision → transaction persistence (including the redacted, size-bounded raw proposal stored as untrusted evidence per FR-03) → initial `REQUESTED` transition.

## Edge and failure scenarios

Unknown tool/field, invalid environment, out-of-range replicas, empty/resource identifier confusion, non-canonical key ordering, Unicode normalization differences, oversized raw proposal truncation, duplicate idempotency key, same key/different request, concurrent revision creation, database rollback, and state commit attempted without an outbox row.

## Tests

- Golden digest vectors for every tool and reordered equivalent JSON.
- Property checks: equivalent validated input has same digest; any material field/revision/tenant/requester change changes it.
- Migration constraints reject duplicate revision/state sequence and invalid revision.
- Repository transaction rollback leaves neither intent nor transition.
- Raw proposal evidence persists redacted and size-bounded and is never a valid execution input.

## Acceptance criteria

All five proposals validate strictly; approved-material fields are explicitly enumerated; persisted canonical JSON/digest round-trip exactly; new material changes create revisions; domain imports no web/ORM/provider modules; migration upgrade and downgrade rehearsal passes.

## Learning outcomes

Explain why immutable revisions beat in-place mutation, what a digest proves and does not prove, why canonicalization precedes hashing, and how database constraints complement application validation.

## Interview questions

1. Why include tenant, requester, and revision in the digest envelope?
2. Why is SHA-256 not proof that a human approved an intent?
3. Which invariants belong in PostgreSQL as well as Python?

## Definition of Done

Unit/integration/migration tests pass; golden vectors are documented; schema and repository review confirms no approval/risk leakage; rollback/concurrency behavior is proven; learning content is updated; phase is reviewed and marked `COMPLETE`.
