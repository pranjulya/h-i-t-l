# Human-in-the-Loop AI Ops

An approval and execution service for AI-proposed infrastructure actions. An LLM may *propose* an
action; it can never execute one. Deterministic code classifies the risk, applies a versioned policy,
collects the right human approvals, re-checks everything just before execution, runs the action
through an allow-listed adapter, and records a hash-linked audit trail.

[![CI](https://github.com/pranjulya/h-i-t-l/actions/workflows/ci.yml/badge.svg)](https://github.com/pranjulya/h-i-t-l/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

> The model never holds infrastructure credentials. Approval is bound to an immutable digest of the
> exact action, so "approve restart" can never become "approve delete".

**Status: TESTED.** All ten phases are implemented and tested. Seven phases stay `TESTED` rather than
`COMPLETE` because of the verified gaps listed in
[architecture-review §14](docs/architecture/architecture-review.md#14-known-gaps-open). See
[Status and known gaps](#status-and-known-gaps).

## The problem

Agentic ops tools often let a model call infrastructure APIs directly, maybe with a "confirm?" prompt
in between. That design fails in predictable ways:

- **Approval drift.** A human approves one action and a different one runs, because the parameters
  changed after approval.
- **Stale authority.** An approval stays valid after the approver's role is revoked or the policy
  changes.
- **Blind retries.** A timeout after the provider acted leads to a second destructive call.
- **No forensics.** Nobody can reconstruct who approved what, under which policy version.

## How it works

```mermaid
flowchart LR
    A[Agent proposal] --> C[Canonical intent<br/>typed tool schema<br/>SHA-256 digest]
    C --> R[Risk engine<br/>LOW · MEDIUM · HIGH · CRITICAL]
    R --> P[Versioned policy]
    P -->|LOW / allowed| AA[AUTO_APPROVED]
    P -->|HIGH| H1[1 authorized approver]
    P -->|CRITICAL| H2[2 distinct approvers<br/>L1 then L2]
    P -->|deny| BL[BLOCKED]
    H1 --> AP[APPROVED]
    H2 --> AP
    AA --> W[Worker: revalidate digest, TTL,<br/>current authorization, policy, live target]
    AP --> W
    W --> X[Allow-listed adapter<br/>+ idempotency key]
    X --> S[SUCCEEDED / FAILED]
    X -->|response lost| U[EXECUTION_UNKNOWN<br/>reconcile, never retry]
```

All state changes go through one state machine
([`approval-state-machine.md`](docs/architecture/approval-state-machine.md)). Each transition is
committed together with an outbox event and appended to a hash-linked audit chain.

| V1 scope | |
|---|---|
| Tools | `inspect_service`, `restart_service`, `scale_service`, `provision_resource`, `delete_resource` |
| Risk routing | LOW auto-allow · MEDIUM policy-driven · HIGH one approver · CRITICAL two distinct approval levels |
| Source of truth | PostgreSQL (no Redis in V1, see ADR-006) |
| Orchestration | Plain Python owns the rules. LangGraph may coordinate later, but never owns policy or state |

## See it work

The live checker drives the whole workflow over HTTP, the way a client would:

```text
JOURNEY 4 — CRITICAL deletion, two distinct approvers
PASS  delete band CRITICAL
PASS  L1 approval accepted  [PENDING_APPROVAL_2]
PASS  same actor cannot satisfy L2  [HTTP 403]
PASS  distinct L2 approver completes approval  [APPROVED]

JOURNEY 6 — ambiguity, reconciliation, audit trail
PASS  lost provider response enters EXECUTION_UNKNOWN  [EXECUTION_UNKNOWN]
PASS  reconciliation resolves it from provider evidence  [SUCCEEDED]
PASS  audit chain verifies

SECURITY SPOT CHECKS
PASS  cross-tenant request returns 404  [HTTP 404]
PASS  mutation without an idempotency key is rejected
PASS  no public execute endpoint exists  [HTTP 404]

SUMMARY
41/41 checks passed
```

## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/) and PostgreSQL 16 (local, or
`docker run -d -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16`).

```bash
cp .env.example .env
# Set IDENTITY_SHARED_SECRET to a random string of at least 32 characters.
# Point DATABASE_URL at your Postgres (default: postgresql+asyncpg://hitl:hitl@localhost:5432/hitl_ops).
set -a; . ./.env; set +a

uv sync
uv run alembic upgrade head
uv run python -m hitl_ops.bootstrap                                    # demo roles and policy, once
uv run uvicorn hitl_ops.api.app:create_app --factory --port 8000       # terminal 1
uv run python -m hitl_ops.worker_main                                  # terminal 2 (same env)
uv run python tools/e2e_check.py                                       # terminal 3 (same env)
```

Without `IDENTITY_SHARED_SECRET`, every protected route returns `403`. Mint a learning-mode token
with `uv run python -m hitl_ops.dev_tokens --actor approver-1` (this command is refused when
`APP_ENV=production`). Swagger UI is at `http://127.0.0.1:8000/docs`. A narrated walkthrough is in
[`docs/operations/demo-script.md`](docs/operations/demo-script.md).

`docker compose up --build` starts the full stack. It publishes ports `8000` and `5432`, so stop any
local Postgres first.

## Key engineering decisions

| Decision | Why |
|---|---|
| Canonical JSON plus a versioned SHA-256 digest identifies each intent revision | Approval binds to exact bytes, so any material change needs a new approval |
| PostgreSQL compare-and-set, row locks and constraints for concurrency | Conflicting approvals lose with `STATE_CONFLICT`, with no distributed lock to fail |
| A separate worker revalidates before execution | Authority is checked at execution time, not only at approval time. Revoking a role blocks an already-approved intent |
| `EXECUTION_UNKNOWN` plus reconciliation by provider operation key | An ambiguous outcome is resolved from evidence and never retried blindly |
| Same-transaction outbox and an append-only, hash-linked audit | Each event is written atomically with its state change, and tampering is detectable |
| Identity comes from a verified token; roles and scopes come from the database | Token claims alone can't grant authority the database has revoked |

All decision records: [`docs/architecture/decisions/`](docs/architecture/decisions/README.md).

## Quality

```bash
uv run pytest -q          # 344 passed (integration, security and failure suites need TEST_DATABASE_URL)
uv run ruff format --check . && uv run ruff check . && uv run mypy
```

Integration tests use their own database (`TEST_DATABASE_URL`), so running them never clears demo data.

## Project layout

```text
src/hitl_ops/
  agent/            proposal schemas and orchestrator (the only surface the LLM touches)
  domain/           intent, risk, policy, state machine
  application/      commands, revalidation, execution, reconciliation, administration
  api/              FastAPI routes, auth, rate limits, error envelope
  adapters/         allow-listed infrastructure adapters (demo adapter in V1)
  infrastructure/   repositories, outbox, audit chain, identity, notifications
  worker.py         execution worker
alembic/            migrations
tools/e2e_check.py  live end-to-end checker
docs/               PRD, state machine, risk/policy model, HLD/LLD, threat model, ADRs
Learning/           guided study material and interview scenarios
```

## Status and known gaps

The project tracks its own gaps in the repository rather than hiding them. The highest-impact open
items from [§14](docs/architecture/architecture-review.md#14-known-gaps-open):

- Intent revisions above 1 are never created, so a material change does not stale an earlier approval.
- A crash between the execution claim and revalidation can leave an intent stuck in `REVALIDATING`.
- Provider calls run while the intent row lock is held.
- The risk engine evaluates only some of the factors the risk model requires.

Supported V1 limitations are in
[§13](docs/architecture/architecture-review.md#13-release-verification-and-accepted-limitations).

## Documentation

Suggested reading order:

1. [PRD](docs/product/PRD.md)
2. [Approval state machine](docs/architecture/approval-state-machine.md)
3. [Risk and policy model](docs/architecture/risk-policy-model.md)
4. [HLD](docs/architecture/HLD.md) · [Threat model](docs/architecture/threat-model.md) · [LLD](docs/architecture/LLD.md)
5. [Implementation plan](Implementation.md) and [phase specs](implementation/)
6. [Learning path](Learning/learning-path.md)
7. [Architecture review](docs/architecture/architecture-review.md)

## License

[MIT](LICENSE)
