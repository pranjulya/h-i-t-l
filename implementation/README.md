# Implementation phases

All phases are specifications only and are `NOT_STARTED`. The user review gate precedes Phase 00.

Every phase contains status, goal, rationale, prerequisites, concepts, exact planned files, architecture impact, data flow, edge/failure cases, tests, acceptance criteria, learning outcomes, interview questions, and Definition of Done. Follow numeric order; do not combine phases merely to save review time.

| File | Focus |
|---|---|
| `phase-00-foundation.md` | Tooling, FastAPI skeleton, config, health, Docker dev baseline |
| `phase-01-intent-persistence.md` | Strict tool models, canonical digest, PostgreSQL, migrations |
| `phase-02-risk-policy.md` | Deterministic risk and versioned policy |
| `phase-03-approvals-rbac.md` | State transitions, human decisions, OIDC/RBAC, expiry, role/policy administration |
| `phase-04-revalidation-idempotency.md` | Freshness, TOCTOU, races, replay safety |
| `phase-05-execution-adapters.md` | Worker, typed adapter, outcomes, reconciliation |
| `phase-06-api-agent-orchestration.md` | Stable APIs and bounded untrusted model proposals |
| `phase-07-audit-observability.md` | Outbox, hash-linked audit, notification delivery, logs, metrics, traces |
| `phase-08-security-recovery.md` | Threat controls, failure injection, runbooks |
| `phase-09-release-learning.md` | E2E proof, CI/container, docs and learning completion |

