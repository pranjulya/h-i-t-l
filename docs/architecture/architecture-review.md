# Architecture Review and Readiness Checklist

**Review result:** COMPLETE — all phases implemented, tested, reviewed, and verified on `main`; release evidence in §11, known limitations in §12, verification and accepted limitations in §13.  
**Reviewed scope:** Planning documents (§1–§10), release evidence (§11), known limitations (§12), and release verification (§14).

## 1. Consistency review

| Check | Result | Evidence |
|---|---|---|
| Risk routes agree everywhere | Pass | PRD, risk model, state machine |
| LLM cannot execute infrastructure | Pass | HLD trust boundary; threat controls; phase plan |
| PostgreSQL/Redis responsibilities do not conflict | Pass | PostgreSQL authoritative; Redis optional acceleration only |
| Approved intent is immutable | Pass | Revision + canonical digest design |
| State names/transitions align | Pass | State machine is authoritative; LLD consumes it |
| Phase dependencies are acyclic | Pass | 00→01→02→03→04→05→06→07→08→09 |
| Planning is separated from implementation | Pass | Phases 00–09 implemented in sequence; release evidence in §11 |

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
- [ ] Any material parameter change creates a new revision — **no path writes `revision > 1`**; a material change today creates an unrelated intent instead of staling the revision. (§14-1)
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
- [~] Audit writer is append-only at the application permission layer — the writer only appends, but no separate DB roles exist and `audit_events` shares the primary database. (§14-6)
- [x] Per-aggregate ordering, previous hash, and event hash reveal edits/gaps.
- [ ] Separate retained sink and provider audit IDs improve independence — single database; no `CREATE ROLE`/`GRANT` exists for writer/migration/audit/auditor roles. (§14-6)
- [x] Historic policy/role/approval snapshots remain evidence, while current auth is rechecked.
- [ ] Audit backlog, hash mismatch, and sequence gaps generate alerts — no such metric or alert exists in the codebase. (§14-7)

## 7. Observability

- [~] Correlation, causation, trace, intent, approval, execution, and provider identifiers are linked — approval and execution outbox payloads omit `correlation_id`, and the LLD §11 log fields are absent. (§14-7)
- [ ] Metrics cover risk/policy outcomes, pending/expired/stale approvals, illegal transitions, execution latency/outcomes, retries, unknown outcomes, reconciliation, and outbox lag — the only counter in the codebase is `notifications_sent`. (§14-7)
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

Implementation was approved by the user; phases 00–09 were implemented and
tested in sequence, with each phase's review gate satisfied before the next
began. Final acceptance follows the user's end-to-end review.


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

## 12. Known limitations (learning/demo scope)

The following are explicitly not production claims until tests genuinely
establish them:

- Production OIDC/JWKS identity: the service validates HS256 test-issuer
  tokens; asymmetric trust, issuer discovery, and key rotation are not
  implemented.
- Full observability and alerts: in-process counters and JSON logs exist, but
  no exporter, endpoint, dashboards, or alert rules ship with this repo.
- Executing-lease recovery bounds: recovery honors an expired
  EXECUTING lease via reconciliation, but lease durations and scheduler
  cadence are deployment configuration, not proven production values.

Crash recovery after provider send, adapter precondition enforcement, and
the Compose topology are covered by `tests/failure/test_worker_crash.py`,
`tests/contract/test_adapter_contract.py`, and the CI `compose-smoke` job
respectively.

## 13. Release verification and accepted limitations

Verified on merged `main` (`b38b0aa`), PostgreSQL 16.15, Python 3.12:

- **Automated suite:** 344 tests pass — unit, contract, integration, e2e, security, failure. Ruff format/lint and strict mypy exit 0 (the same command set CI runs).
- **Migrations:** clean-database rehearsal `upgrade head → downgrade base → upgrade head`; 11 domain tables, downgrade leaves only `alembic_version`, head is `0008`. Readiness fails closed on a revision/head mismatch.
- **Live end-to-end:** API + supervised worker + demo adapter; `tools/e2e_check.py` reports 41/41 — all five tools, all four risk routes, two-step CRITICAL with distinct approvers, rejection, cancellation, idempotent replay, mismatch conflict, unknown → reconciliation, audit-chain reconstruction, restart durability, and the security spot checks.
- **Targeted proofs of the final review findings:** (a) an expired approval returns 409 *and* the intent durably becomes `EXPIRED`, with a fresh-key retry observing the conflict; (b) every live-target lookup failure fails closed — timeout, transport error, malformed response — and no lookup failure leaves a claim stranded in `REVALIDATING` (a process crash between the claim commit and the gates is a separate, still-open window — §14-2); (c) removing an approver's scopes after approval blocks execution with `approver_scopes_revoked` while the role remains granted; (d) `notification_delivered` appears in the hash-linked audit trail; (e) production configuration rejects a short or demo identity secret.
- **Operations:** `docs/operations/runbooks.md` (six runbooks), `docs/operations/release-checklist.md`, `docs/operations/demo-script.md`; CI runs quality, tests, migrations, docker build, and a compose smoke test on every PR.

### Accepted limitations (explicit, carried as V1 residual risk)

| Limitation | Why accepted | What production requires |
|---|---|---|
| Identity is shared-secret HS256 | Learning scope; validation semantics (issuer, audience, signature, time, required claims) match OIDC | OIDC discovery/JWKS, key rotation, minimum-secret enforcement (now enforced in config) — ADR-008 |
| Demo infrastructure adapter, provider evidence in worker memory | No real cloud mutation is needed to prove the control plane; a worker restart therefore loses status-lookup evidence and an ambiguous outcome stays escalated | A real typed adapter with durable provider status lookup — ADR-007/ADR-010 |
| LLM provider is disabled by default | The direct intent API remains fully usable; the model can never hold authority | A configured provider behind the existing orchestrator boundary |
| In-process rate limiting, single replica | Abuse mitigation, never correctness — PostgreSQL constraints enforce invariants | Shared/shared-nothing limiter when horizontally scaled |
| No Redis | Correctness never depended on it; tests prove its absence changes nothing | Add only on measured contention, as an accelerator — ADR-006 |
| LangGraph deferred | No measured orchestration value yet; graph checkpoints must never be authoritative state | Post-V1 evaluation — ADR-012 |

Reopening any of these requires the PRD/architecture path in `Implementation.md` (authoritative precedence) rather than a code-only change.

> Status note: the release verification above stands for what it tested, but the sections marked `[~]`/`[ ]` in §§4–7 and the gaps in §14 mean phases 01, 02, 04, 05, 06, 07, and 09 are `TESTED`, not `COMPLETE`.

## 14. Known gaps (open)

Findings from a two-axis code review of the full implementation (`fe9b212...HEAD`), each independently re-verified against the code before listing. Notation above: `[x]` verified, `[~]` partial, `[ ]` not implemented.

**Correctness / spec**

1. **Intent revisions cannot be created.** No code path writes `revision > 1`; `POST /intents` hardcodes `revision=1` and mints a new intent ID, so a material change does not stale the prior approval (`api/intents.py`, verified by grep — `approval-state-machine.md` §6 requires otherwise).
2. **Crash between claim commit and gates strands `REVALIDATING`.** `claim_and_revalidate` commits the claim, then re-verifies in a later transaction; worker selection covers only `AUTO_APPROVED`/`APPROVED` (`worker.py:44`) and recovery only `status == "EXECUTING"` (`worker.py:229`), so a process death in that window leaves a revision nothing can move or cancel.
3. **LLD §9 step 6 is absent at execution.** Obligations are acknowledged at decision time only; `_revalidate_gates` never checks them, and preconditions compare only `service`/`resource_id`/`name` (replicas, size, cost_class, deletion_mode, region are not compared with live state) — `revalidation.py`.
4. **Revalidation order differs from LLD §9.** The live target is fetched before the digest/TTL/authorization/policy gates, so a stale or expired revision still triggers a privileged live read — deliberate (lock-free read), but the LLD was not updated.
5. **Provider calls run while holding the intent row lock.** `execution.py` and `reconciliation.py` await the adapter inside the `with_for_update()` transaction; a slow provider blocks approve/cancel on that row.
6. **Risk engine omits mandated factors.** Only environment, cost_class, degraded dependencies, and policy flags are evaluated (`risk.py`); magnitude, reversibility, sensitivity, and service-condition inputs are absent, `RiskContext()` is always empty in both call sites, and `replicas` is accepted as `0..1000` (`agent/schemas.py:87`) — `risk-policy-model.md` §2 requires at least HIGH outside bounds.
7. **Notification content is not the agreed summary.** The notification carries state and a link only — no digest, tool, parameters, or target (`infrastructure/notification.py`), against `threat-model.md` notification-deception control.
8. **Error taxonomy diverges from the PRD.** `UNAUTHORIZED`, `PRECONDITION_FAILED`, and `APPROVAL_REQUIRED` are never emitted; a real policy BLOCK returns 201 with state `BLOCKED` rather than `POLICY_BLOCKED` (`api/intents.py`, `api/errors.py`).
9. **`GET /intents/{id}/events` exposes the audit chain to any same-tenant actor**, not only `audit:read` holders (`risk-policy-model.md` §8).

**Layering / standards**

10. **Risk/policy orchestration lives in the router** (`api/intents.py:86-87`) with no application-layer create-intent service; `revalidation.py:671` duplicates the same calls — AGENTS.md §3 places business rules in domain/application services.
11. **The repository bypasses the transition guard**, writing `intent.state` and a literal `intent.state_version = 4` (`infrastructure/repositories.py`).
12. **Invented state-machine edges:** `EXECUTING → {STALE, EXPIRED}` exist in code but not in `approval-state-machine.md` §2, and the transition bookkeeping is re-implemented across five modules (shotgun surgery).

**Evidence**

13. This checklist previously asserted items 1, 6, and 7 as verified; they are corrected above. Verified-green claims that still hold: the durable-expiry fix, lookup-failure fail-closed behaviour, scope revocation at execution, audited notification delivery, and production secret enforcement (§12).
