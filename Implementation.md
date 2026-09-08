# Project 05 — Master Implementation Plan

> **For agentic workers:** Execute exactly one approved phase at a time. Use test-first implementation, review the diff against the phase acceptance criteria, and never advance status on partial evidence.

**Status:** PLANNING_COMPLETE — all implementation phases `NOT_STARTED`; awaiting user approval  
**Goal:** Build a production-minded HITL AI Ops workflow in which model proposals pass deterministic risk, policy, human approval, revalidation, idempotent execution, and durable audit controls.  
**Architecture:** Plain Python domain/application services own every business rule. FastAPI exposes strict contracts; PostgreSQL is authoritative; an isolated execution worker calls typed adapters; the audit trail is delivered through a transactional outbox.  
**Tech stack:** Python 3.12+, FastAPI, Pydantic v2, PostgreSQL 16+, SQLAlchemy 2, Alembic, pytest, Ruff, mypy, HTTPX, OpenTelemetry, Docker, GitHub Actions; optional Redis only when justified.  
**Specs:** `docs/product/PRD.md`, `docs/architecture/*.md`.

## Global constraints

1. No application code begins before explicit user approval.
2. The five V1 tools and locked risk routes are the complete feature scope.
3. LLM output is untrusted proposal data and never reaches an infrastructure SDK directly.
4. Approved parameters are immutable; material change creates a new revision and stale approvals cannot execute.
5. PostgreSQL alone must preserve correctness. Redis may only accelerate idempotency, locks, or short-lived coordination.
6. State changes, idempotency result, and audit outbox records commit atomically where applicable.
7. Destructive mutations are never blindly retried after an ambiguous send.
8. Every phase ships the smallest independently testable vertical capability and updates its paired learning content.

## Authoritative precedence

When documents disagree: PRD product rules → approval state machine legal transitions → risk/policy model → accepted ADRs → LLD interfaces → phase documents. Resolve the contradiction in documentation before code changes.

## Phase status model

`NOT_STARTED` → `IN_PROGRESS` → `IMPLEMENTED` → `TESTED` → `REVIEWED` → `COMPLETE`. Only one phase may be `IN_PROGRESS`. `COMPLETE` requires code, automated tests, review, documentation, learning notes, and the phase Definition of Done.

## Ordered roadmap

| Phase | Deliverable | Depends on | Status |
|---:|---|---|---|
| 00 | Repository and application foundation | User approval | TESTED |
| 01 | Immutable intent domain and PostgreSQL persistence | 00 | TESTED |
| 02 | Risk and policy evaluation | 01 | TESTED |
| 03 | Approval state machine, OIDC/RBAC, expiry | 02 | TESTED |
| 04 | Revalidation, concurrency, idempotency | 03 | TESTED |
| 05 | Execution service, demo adapter, reconciliation | 04 | NOT_STARTED |
| 06 | FastAPI and agent orchestration | 05 | NOT_STARTED |
| 07 | Audit trail, notification delivery, and observability | 06 | NOT_STARTED |
| 08 | Security hardening and failure recovery | 07 | NOT_STARTED |
| 09 | End-to-end validation, Docker/CI, learning release | 08 | NOT_STARTED |

## Cross-phase verification gates

- After 01: a canonical intent and digest round-trip through PostgreSQL without mutation.
- After 02: all five tools map to the locked risk/policy routes with fail-closed unknowns.
- After 03: legal approval flows work; self/duplicate/unauthorized approvals fail.
- After 04: concurrent claims and replays prove at-most-one execution eligibility.
- After 05: confirmed, failed, and ambiguous adapter outcomes recover safely.
- After 06: HTTP and agent entry paths produce identical domain behavior.
- After 07: every transition reconstructs from ordered, hash-linked audit evidence.
- After 08: threat controls and injected outages pass.
- After 09: clean-environment CI, deployment smoke test, operator drill, and architecture review pass.

## Planned repository shape

```text
h-i-t-l/
  AGENTS.md  CLAUDE.md  README.md  Implementation.md
  docs/{product,architecture}/
  implementation/
  Learning/{concepts,scenarios,interview}/
  src/hitl_ops/        # created only during approved phases
  tests/               # created only during approved phases
  alembic/             # created only during approved phases
```

## Review and change control

Each phase begins by accepting the ADR candidates it needs, confirms prerequisite tests, and changes status to `IN_PROGRESS`. Any product-rule change first updates PRD, affected architecture documents, architecture review, downstream phases, and learning material. LangGraph evaluation is post-V1 unless the user explicitly changes scope.

## Final Definition of Done

- All phase DoDs are complete and statuses agree across documents.
- Five tool flows, four risk routes, expiry, staleness, races, idempotency, ambiguous outcomes, and audit reconstruction pass automated tests.
- Migration upgrade/downgrade rehearsal, clean Docker startup, GitHub Actions, and recovery drill pass.
- No secret appears in logs/traces/audit fixtures.
- Architecture review is re-run with evidence links and no unresolved blocking contradiction.
- User accepts the learning demonstration and production limitations.

