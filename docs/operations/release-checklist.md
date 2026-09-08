# Release Checklist — V1

Every item must be checked (with evidence links where noted) before V1 is
declared releasable. This checklist is executed during the user's end-to-end
review.

## Build and packaging

- [ ] `uv sync --frozen` succeeds from a clean checkout (lock file pinned).
- [ ] `docker build .` succeeds; image runs as non-root with a health check and no embedded secret.
- [ ] `docker compose up --build` starts db + app; `/health/ready` returns 200.
- [ ] Dependency and image scans show no unresolved critical/high findings (or findings are explicitly accepted by the user).

## Migrations

- [ ] `uv run alembic upgrade head` on an empty database creates the full schema.
- [ ] `downgrade base` → `upgrade head` rehearsal passes (also covered by `tests/integration/test_intent_migration.py`).
- [ ] Readiness fails closed when the database revision differs from the migration head.

## End-to-end proof

- [ ] `tests/e2e/test_e2e_journeys.py` passes: LOW auto-allow, MEDIUM policy branches, HIGH single approval, CRITICAL two-step with distinct approvers, rejection, cancellation, duplicate-request replay, unknown reconciliation, audit-chain verification, restart durability.
- [ ] Concurrent-claim suite proves at-most-one execution per revision (`test_concurrent_claim.py`).
- [ ] Security suite passes (`tests/security/`); failure suite passes (`tests/failure/`).

## Operations readiness

- [ ] Runbooks exist for: execution unknown, stale approvals, outbox/audit backlog, credential rotation, database loss, notification outage (`docs/operations/runbooks.md`).
- [ ] Demo script executed once end-to-end (`docs/operations/demo-script.md`).
- [ ] Alerts defined for: execution unknown count, outbox lag, audit backlog, notification backlog, illegal transitions.

## Documentation and review

- [ ] PRD success criteria map to evidence (see architecture review §11).
- [ ] Architecture review re-run with evidence links; no unresolved blocking contradiction.
- [ ] Phase statuses agree across `Implementation.md`, `implementation/README.md`, and phase files.
- [ ] Learning package complete; user accepts the demonstration and known limitations.
