# Architecture Decision Record Candidates

Each candidate becomes an accepted ADR immediately before the first phase that depends on it. Rejected alternatives and consequences must be recorded; changing an accepted decision requires a superseding ADR.

| ADR | Decision candidate | Recommended decision | Needed by |
|---|---|---|---|
| ADR-001 | Domain orchestration ownership | Plain Python services/state table own rules; LangGraph optional adapter | Phase 00 |
| ADR-002 | Durable state | PostgreSQL source of truth; SQLAlchemy/Alembic | Phase 00 |
| ADR-003 | Intent identity | Strict typed tool schemas, canonical JSON, versioned SHA-256 digest, immutable revisions | Phase 01 |
| ADR-004 | Risk/policy representation | Deterministic versioned code/data rules; no V1 policy DSL | Phase 02 |
| ADR-005 | Approval route | Locked LOW/MEDIUM/HIGH/CRITICAL model and distinct critical approvers | Phase 03 |
| ADR-006 | Concurrency/idempotency | PostgreSQL CAS/locks/constraints first; Redis optional accelerator | Phase 04 |
| ADR-007 | Execution boundary | Separate worker + typed allow-listed adapter + provider operation key | Phase 05 |
| ADR-008 | Identity/authorization | OIDC/JWT identity plus DB-backed current RBAC scopes | Phase 03 |
| ADR-009 | Audit reliability | Same-transaction outbox, append-only hash-linked events, separate retained sink | Phase 07 |
| ADR-010 | Ambiguous outcomes | `EXECUTION_UNKNOWN` plus reconciliation; no blind mutation retry | Phase 05 |
| ADR-011 | Deployment baseline | Docker/Compose learning deployment and GitHub Actions; no Kubernetes V1 | Phase 00 |
| ADR-012 | LangGraph adoption test | Adopt only if measured orchestration value without state-authority leakage | Post-V1 |

Acceptance requires compatibility with the PRD, threat model, and legal state transitions; explicit operational consequences; and one verification method.
