# Human-in-the-Loop AI Ops Workflow

**Status:** TESTED — all ten phases are implemented with passing suites, but seven are `TESTED` rather than `COMPLETE` pending the verified gaps in [`docs/architecture/architecture-review.md` §14 "Known gaps (open)"](docs/architecture/architecture-review.md#14-known-gaps-open). Supported V1 limitations are listed in [§13 "Release verification and accepted limitations"](docs/architecture/architecture-review.md#13-release-verification-and-accepted-limitations).

This repository implements a production-minded AI Ops workflow in which an LLM may propose actions but cannot execute infrastructure operations. Deterministic domain services classify risk, apply policy, collect human approval, revalidate the exact immutable intent, execute through allow-listed adapters, and append an audit trail.

Start with [Implementation.md](Implementation.md), then follow the review order below. The application lives in `src/hitl_ops/` with migrations in `alembic/`; run and test commands are below.

## Locked V1 scope

- Tools: `inspect_service`, `restart_service`, `scale_service`, `provision_resource`, `delete_resource`.
- Risk: LOW auto-allow; MEDIUM policy-driven; HIGH one authorized approver; CRITICAL two distinct approval levels.
- PostgreSQL is the durable source of truth. V1 uses no Redis; it may be added later as an accelerator only (see ADR-006 and architecture-review §13).
- Plain Python owns workflow rules. LangGraph may later coordinate calls but may never own policy or state-transition truth.
- The LLM never receives infrastructure credentials and never invokes an infrastructure SDK.

## Local quickstart

Authenticated calls need `IDENTITY_SHARED_SECRET` (see `.env.example`). Without it the API rejects every request with 403 ("identity verification is not configured").

```bash
cp .env.example .env
# set IDENTITY_SHARED_SECRET to a long random string (≥32 bytes in production)
uv sync
uv run alembic upgrade head
uv run python -m hitl_ops.bootstrap          # seed demo roles / policy once
uv run uvicorn hitl_ops.api.app:create_app --factory --reload
# second terminal — execution worker
uv run python -m hitl_ops.worker_main
# mint a learning token
uv run python -m hitl_ops.dev_tokens --actor approver-1
```

For a scripted walkthrough see [`docs/operations/demo-script.md`](docs/operations/demo-script.md). The live end-to-end checker is `tools/e2e_check.py`.

## Run and test

- `uv sync` — create the virtual environment from the lock file (Python 3.12 managed by uv).
- `uv run uvicorn hitl_ops.api.app:create_app --factory --reload` — start the API locally (requires `IDENTITY_SHARED_SECRET` and a migrated Postgres).
- `uv run python -m hitl_ops.worker_main` — start the execution worker.
- `uv run pytest -q` — unit and integration tests; integration/security/failure tests require PostgreSQL via `TEST_DATABASE_URL` (default `postgresql+asyncpg://postgres@localhost:54329/hitl_ops_tests`, created on demand) so they never touch the demo database.
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
