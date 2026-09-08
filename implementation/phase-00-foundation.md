# Phase 00 — Repository and Application Foundation

**Status:** NOT_STARTED — specification only; awaiting explicit user approval. No Phase 00 application files have been created.

## Goal

Create the smallest runnable, typed, testable FastAPI repository baseline with configuration, health/readiness, PostgreSQL connectivity, development containers, and CI quality gates. It must contain no workflow business behavior.

## Why

Later phases need one repeatable runtime and test contract. Establishing it once prevents each domain feature from inventing packaging, configuration, logging, or database setup.

## Prerequisites

- User approves the complete planning package.
- ADR-001, ADR-002, and ADR-011 are accepted.
- Supported dependency versions are selected and recorded in the lock file at implementation time.

## Concepts

Dependency management, application factory/lifespan, twelve-factor configuration, liveness versus readiness, async SQLAlchemy session lifecycle, migration ownership, structured logs, static analysis, test pyramid, reproducible containers.

## Files to create or modify

- Create `pyproject.toml`, dependency lock file, `.gitignore`, `.dockerignore`, `.env.example`.
- Create `src/hitl_ops/__init__.py`, `config.py`, `api/app.py`, `api/errors.py`, `infrastructure/database.py`, `observability/logging.py`.
- Create `tests/unit/test_config.py`, `tests/unit/test_health.py`, `tests/integration/test_readiness.py`, `tests/conftest.py`.
- Create `alembic.ini`, `alembic/env.py`, empty migration script template; no domain table migration.
- Create `Dockerfile`, `compose.yaml`, `.github/workflows/ci.yml`.
- Modify `README.md` with approved local run/test commands and `Implementation.md` status only when work begins/completes.

## Architecture impact

Introduces framework edges and database plumbing while preserving an empty domain core. FastAPI depends inward on configuration/services. No provider SDK, LangGraph, Redis, or infrastructure credential is introduced.

## Data flow

`GET /health/live` returns process liveness without dependencies. `GET /health/ready` checks bounded PostgreSQL connectivity and reports unavailable without leaking connection details. Startup loads validated environment configuration and initializes telemetry/log context.

## Edge and failure scenarios

- Missing/invalid required environment values stop startup with sanitized errors.
- Database unreachable makes readiness fail but process liveness remain healthy.
- Migration/schema mismatch makes readiness fail closed.
- Shutdown drains resources within a bounded timeout.
- Production mode rejects debug configuration and default secrets.

## Tests

- Unit: defaults, required values, invalid URLs/timeouts, production safety, error envelope, liveness.
- Integration: clean PostgreSQL connection, unavailable database, migration head check.
- CI: lock-file install, Ruff format/lint, mypy strict project package, unit/integration tests, migration import check, container build.

## Acceptance criteria

- A clean checkout starts through documented commands and exposes only health endpoints.
- Liveness does not depend on PostgreSQL; readiness does.
- Tests prove sanitized configuration failures and database outage behavior.
- Container runs as non-root with a health check and no embedded secret.
- CI gives deterministic pass/fail for formatting, typing, tests, migrations, and image build.

## Learning outcomes

Explain why foundation work contains no speculative domain abstraction; distinguish liveness/readiness; trace a request through FastAPI lifespan and a bounded DB check; explain configuration and migration ownership.

## Interview questions

1. Why should liveness not fail when PostgreSQL is temporarily down?
2. Why validate configuration at startup rather than first request?
3. What does Alembic own that SQLAlchemy metadata creation should not own in production?
4. Why avoid adding Redis and LangGraph in foundation?

## Definition of Done

All named files exist, smallest tests pass locally and in CI, clean Docker startup/readiness is demonstrated, dependency/security scan has no unresolved critical finding, docs match commands, diff review finds no workflow code, and phase status reaches `COMPLETE` through the defined sequence.

