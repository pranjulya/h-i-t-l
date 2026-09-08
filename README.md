# Project 05 — Human-in-the-Loop AI Ops Workflow

**Status:** PLANNING_COMPLETE — awaiting user review; application implementation has not started.

This repository plans a production-minded AI Ops workflow in which an LLM may propose actions but cannot execute infrastructure operations. Deterministic domain services classify risk, apply policy, collect human approval, revalidate the exact immutable intent, execute through allow-listed adapters, and append an audit trail.

Start with [Implementation.md](Implementation.md), then follow the review order below. No `src/`, migrations, containers, workflow files, or application tests exist yet by design.

## Locked V1 scope

- Tools: `inspect_service`, `restart_service`, `scale_service`, `provision_resource`, `delete_resource`.
- Risk: LOW auto-allow; MEDIUM policy-driven; HIGH one authorized approver; CRITICAL two distinct approval levels.
- PostgreSQL is the durable source of truth. Redis is optional for short-lived distributed locks and idempotency acceleration only.
- Plain Python owns workflow rules. LangGraph may later coordinate calls but may never own policy or state-transition truth.
- The LLM never receives infrastructure credentials and never invokes an infrastructure SDK.

## Run and test

- `uv sync` — create the virtual environment from the lock file (Python 3.12 managed by uv).
- `uv run uvicorn hitl_ops.api.app:create_app --factory --reload` — start the API locally.
- `uv run pytest -q` — unit and integration tests; integration tests use `TEST_DATABASE_URL` (default `postgresql+asyncpg://postgres@localhost:54329/hitl_ops`) and skip when PostgreSQL is unreachable.
- `uv run ruff format . && uv run ruff check . && uv run mypy` — format, lint, and strict typing.
- `uv run alembic upgrade head` — apply migrations.
- `docker compose up --build` — full development stack.

## Review order

1. `docs/product/PRD.md`
2. `docs/architecture/approval-state-machine.md`
3. `docs/architecture/risk-policy-model.md`
4. `docs/architecture/HLD.md`
5. `docs/architecture/threat-model.md`
6. `docs/architecture/LLD.md`
7. `Implementation.md`
8. `implementation/` (starting with `phase-00-foundation.md`)
9. `Learning/`
10. `docs/architecture/architecture-review.md`
