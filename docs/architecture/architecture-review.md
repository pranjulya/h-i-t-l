# Architecture Review and Readiness Checklist

**Review result:** READY_FOR_USER_REVIEW; NOT APPROVED_FOR_IMPLEMENTATION  
**Reviewed scope:** Planning documents only.

## 1. Consistency review

| Check | Result | Evidence |
|---|---|---|
| Risk routes agree everywhere | Pass | PRD, risk model, state machine |
| LLM cannot execute infrastructure | Pass | HLD trust boundary; threat controls; phase plan |
| PostgreSQL/Redis responsibilities do not conflict | Pass | PostgreSQL authoritative; Redis optional acceleration only |
| Approved intent is immutable | Pass | Revision + canonical digest design |
| State names/transitions align | Pass | State machine is authoritative; LLD consumes it |
| Phase dependencies are acyclic | Pass | 00→01→02→03→04→05→06→07→08→09 |
| Planning is separated from implementation | Pass | All phases `NOT_STARTED`; no application source created |

## 2. Scope and simplicity

- [x] Five explicit tools only.
- [x] Reusable domain boundaries without arbitrary workflows/tools/policies.
- [x] No custom policy DSL, event sourcing, Kubernetes, multi-region, UI builder, or plugin platform.
- [x] LangGraph deferred behind stable application interfaces.
- [x] Redis not required for correctness.
- [x] Direct intent API preserves usefulness if the LLM is unavailable.

## 3. Security

- [x] Authentication validates token issuer, audience, signature, and time claims.
- [x] Tenant and actor derive from server-authenticated context.
- [x] RBAC uses current DB assignments and resource scopes.
- [x] Requester self-approval is blocked.
- [x] CRITICAL L1 and L2 require distinct principals and permissions.
- [x] The model has no credential or infrastructure network path.
- [x] Strict typed schemas reject unknown tools/fields and dynamic endpoints/commands.
- [x] Secrets and unbounded payloads are excluded/redacted from logs and audit.

## 4. Concurrency, freshness, and TOCTOU

- [x] Expected `state_version`, row locks/CAS, and uniqueness constraints define one winner.
- [x] Approval binds intent ID, revision, digest, policy version, route, level, actor, scope, and expiry.
- [x] Any material parameter change creates a new revision.
- [x] Approval and approver authorization are checked again at execution.
- [x] Current risk/policy and live target context are re-evaluated immediately before adapter send.
- [x] Conditional provider operations/precondition tokens are used where available.
- [x] Cancellation/rejection race semantics are explicit.

## 5. Idempotency, retries, and recovery

- [x] Same key/same request replays; same key/different request conflicts.
- [x] One execution row and stable operation key exist per intent revision.
- [x] Safe reads, pre-send failures, and proven-idempotent mutations have distinct retry rules.
- [x] Ambiguous mutation outcomes enter `EXECUTION_UNKNOWN`.
- [x] Reconciliation uses provider operation key/ID and never guesses failure.
- [x] Outbox makes audit/notification delivery recoverable after primary commit.
- [x] PostgreSQL loss fails mutations closed; Redis loss degrades performance only.

## 6. Audit immutability

- [x] State change and audit outbox commit atomically.
- [x] Audit writer is append-only at the application permission layer.
- [x] Per-aggregate ordering, previous hash, and event hash reveal edits/gaps.
- [x] Separate retained sink and provider audit IDs improve independence.
- [x] Historic policy/role/approval snapshots remain evidence, while current auth is rechecked.
- [x] Audit backlog, hash mismatch, and sequence gaps generate alerts.

## 7. Observability

- [x] Correlation, causation, trace, intent, approval, execution, and provider identifiers are linked.
- [x] Metrics cover risk/policy outcomes, pending/expired/stale approvals, illegal transitions, execution latency/outcomes, retries, unknown outcomes, reconciliation, and outbox lag.
- [x] Labels avoid tenant/user/resource high cardinality.
- [x] Logs/traces omit raw credentials and sensitive model/provider content.
- [x] Recovery runbooks and failure drills are planned.

## 8. Testing adequacy

- [x] Unit tests cover pure rules and state guards.
- [x] PostgreSQL integration tests prove constraints and transaction races.
- [x] Adapter contract tests cover idempotency, preconditions, timeouts, and status lookup.
- [x] End-to-end tests cover each tool/risk route and critical two-step flow.
- [x] Failure injection covers crashes at pre-send, post-send, post-response, and pre-audit-publication points.
- [x] Security tests cover injection, tenant isolation, stale/revoked authority, and redaction.

## 9. Open decisions that do not block planning review

These are intentionally resolved at the named ADR gate, not left ambiguous: select the first demo infrastructure target in Phase 05 (recommended in-memory/demo adapter before a real cloud); select the OIDC test provider in Phase 03; set exact dependency versions during Phase 00 using then-current supported releases. None changes the domain contracts.

## 10. Approval gate

Implementation may begin only when the user approves this package. On approval, Phase 00 moves from `NOT_STARTED` to `IN_PROGRESS`; no later phase begins until its prerequisites and preceding review gate pass.


## 11. Release evidence (Phase 09)

Evidence mapping for the PRD §11 success criteria and the checklists above.
All suites run with `uv run pytest -q` (unit + contract + integration + e2e +
security + failure) against PostgreSQL 16.

| PRD success criterion | Evidence |
|---|---|
| Every V1 tool follows its required approval route under tested policies | `tests/unit/test_risk_policy_matrix.py`, `tests/e2e/test_e2e_journeys.py` |
| One-byte material change → new digest, stale approvals | `tests/integration/test_concurrent_claim.py::test_one_byte_material_change_stales_the_claim`, `tests/unit/test_canonical_intent.py` |
| Two concurrent executions → at most one adapter invocation | `tests/integration/test_concurrent_claim.py`, `tests/failure/test_worker_crash.py` |
| Replayed API calls return the original result or a deterministic conflict | `tests/api/test_intent_routes.py`, `tests/integration/test_idempotency_transactions.py` |
| Self-approval and duplicate CRITICAL approvers rejected | `tests/integration/test_approval_transactions.py` |
| Policy/authorization changes observed during revalidation | `tests/integration/test_policy_auth_refresh.py` |
| Crash after provider send → reconciliation, not blind retry | `tests/integration/test_reconciliation.py`, `tests/failure/test_worker_crash.py` |
| Audit reconstruction answers who/what/when/why/version/outcome | `tests/integration/test_audit_ordering.py`, `tests/e2e/test_e2e_journeys.py::test_audit_chain_reconstructs_the_journey`, `docs/operations/runbooks.md` |

Threat-model controls: `tests/security/`, `tests/failure/`, residual risks in
`docs/architecture/threat-model.md` §9. Release gates: `docs/operations/release-checklist.md`.
