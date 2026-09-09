# Fixed Boundaries — Consolidated PR Review

Review snapshot: 2026-09-09

Repository: `pranjulya/h-i-t-l`

Scope: PR #1 through PR #10 (Phase 00 through Phase 09)

This document is the actionable boundary for the next fixing pass. Test totals are not treated as evidence that concurrency, authorization, approval integrity, crash recovery, or audit semantics are correct.

## New pushes detected

No pushes landed between the earlier review context (2026-09-09 11:15 UTC) and this review. The earlier conversation did not preserve a head-SHA baseline or a completed findings ledger, so commit timestamps and current PR history are the only defensible comparison. Every PR still contained exactly one commit, and every commit predates that context.

| PR | Phase | Head SHA before this report commit | Changed files | Current PR state |
| --- | --- | --- | ---: | --- |
| #1 | 00 | `9ca5595c48490cf15f0f60212e96d6842b7d9899` | 32 | Open, mergeable |
| #2 | 01 | `f5adcd0c8527d655cb3cec259e1d89cbcc932128` | 19 | Open, mergeable |
| #3 | 02 | `d207b3cfcf9808175395fa161d2edd6ebd5cd030` | 11 | Open, mergeable |
| #4 | 03 | `0a3d46828bcca72cee30ab6f57e60006f1bb0066` | 21 | Open, mergeable |
| #5 | 04 | `174f81584188b2c9186fe9397d899b768bbcc6ab` | 16 | Open, mergeable |
| #6 | 05 | `918e4ebf1d89a404feb1d8ea5ed1e7df9be5be6b` | 15 | Open, mergeable |
| #7 | 06 | `2e9a35aa4a9e651dc46aab15e7f2d7e3aa598d22` | 20 | Open, mergeable |
| #8 | 07 | `5baf1dd9f1abf826d908590a675bd4662002b796` | 20 | Open, mergeable |
| #9 | 08 | `3e8562e16e72125601d3165214e8f74a71b5d3e1` | 22 | Open, mergeable |
| #10 | 09 | `aabbbbcd660a9faedc0d06b5e56e420d2df53fc0` | 11 | Open, mergeable |

No PR had review comments, submitted reviews, or unresolved review threads at the snapshot time.

All ten GitHub Actions runs were failing. PRs #1–#4 passed quality, tests, and migrations but failed the Docker job. PRs #5–#10 also failed lint, with the observed lint-error counts increasing across the stack: 1, 2, 3, 5, 14, and 14 respectively. Passing test jobs do not make these PRs green.

## New findings

### Blocker

1. **[PR #1–#10] CI never reaches a fully green state.** In `Dockerfile`, the dependency-build layer runs `uv sync --frozen --no-dev` after copying `pyproject.toml`, `uv.lock`, `src/`, and `alembic/`, but not `README.md`. Hatchling reads project metadata during the build and aborts because `README.md` is missing. PRs #5–#10 additionally contain unresolved lint errors. A mergeable GitHub status is not equivalent to a passing release gate.

2. **[PR #6, `src/hitl_service/worker.py`, claim/execution transaction around lines 72–91] A provider side effect can be sent more than once after a worker crash.** Claiming the work, setting the intent to `EXECUTING`, invoking `adapter.execute(...)`, and saving the result all occur inside one database transaction. If the provider accepts the side effect and the worker dies before commit, the database rolls back the claim and state transition. A later tick can send the same operation again. A stable idempotency key reduces the risk only when every real provider honors it correctly; it is not an internal exactly-once guarantee.

3. **[PR #9, `tests/test_worker_crash.py`, lines 44–71; still present in PR #10] The advertised crash-boundary test is false-green.** The test acquires a claim and rolls the transaction back, but never invokes `adapter.execute`. It therefore cannot simulate the dangerous boundary—provider success followed by process death before durable result persistence—and does not support the architecture review's duplicate-execution claim.

4. **[PR #3–#5, `src/hitl_service/services/policy.py`, `src/hitl_service/services/commands.py`] Policy scopes and obligations are stored but not enforced.** Policy results can include constraints such as `require_change_ticket`, yet command authorization checks only roles. An approval can therefore satisfy the state machine while mandatory contextual controls remain unmet.

5. **[PR #5, `src/hitl_service/services/revalidation.py`] Revalidation misses materially stricter policy changes when disposition and route stay the same.** The comparison does not reliably invalidate approval when required roles, scopes, obligations, or TTL become stricter under the same route. It also reasons from the original policy row in places where current-policy semantics are required. This permits stale approval reuse after a security-significant policy update.

6. **[PR #5–#6, target revalidation in `src/hitl_service/services/revalidation.py` and demo adapter identity in `src/hitl_service/adapters/demo.py`] Exact-target validation is not fail-closed.** Missing target identity keys can be accepted. The checker expects fields such as `service`, `resource_id`, or `name`, while the demo adapter returns an `identity` object, so the shipped implementation does not prove that the approved target is the target that will actually execute.

### High

7. **[PR #4 and PR #7, expiry path in `src/hitl_service/services/commands.py` and API session handling] Expiry can be rolled back while the API reports it.** The command records an `EXPIRED` transition and then raises. The request-scoped transaction rolls back on the exception, so the caller can receive an expiry response while durable state remains unexpired. The existing test catches the exception inside the session and commits manually, which does not represent the production API path.

8. **[PR #4, approval handling in `src/hitl_service/services/commands.py`] Critical approval roles are not bound to approval levels.** The implementation checks that approvers are distinct and possess a required role, but does not bind a specific role to L1 versus L2. Two differently named required roles can approve in the wrong level order while the intent still advances.

9. **[PR #3–#4, `PolicyBundleORM`, policy administration routes, and policy audit/outbox payloads] Tenant administrators can affect global policy state.** Policy bundles have no tenant ownership field, while tenant-authenticated administrators can manage them. Policy audit payloads also omit tenant identity, and outbox handling defaults attribution to `tenant-1`. This breaks tenant isolation and audit attribution.

10. **[PR #5 and PR #7, idempotency persistence and mutation routes] Concurrent requests can escape the idempotency contract as raw integrity failures.** The flow selects and then inserts without converting a uniqueness race into a replay or a documented conflict. Two requests with the same key can both observe absence; one receives an `IntegrityError` or generic 500. A test describes this race as a conflict instead of validating stable contract behavior.

11. **[PR #9, request-size middleware] The payload cap is bypassable.** Enforcement trusts a present, numeric `Content-Length`. Chunked bodies and requests without that header can exceed the configured limit.

12. **[PR #6–#7, proposal, target-fetch, and reconciliation paths] External calls lack bounded timeouts.** LLM proposal generation, execution-time target reads, and provider reconciliation can wait indefinitely, holding workers, leases, or request capacity and obscuring crash recovery.

13. **[PR #6 and PR #10, `compose.yaml`, `src/hitl_service/main.py`, and release E2E path] The documented stack is not runnable as the claimed system.** Compose starts the database and API but does not run migrations, configure production identity, start a worker, supply a real adapter, or bootstrap roles. `create_app` always installs `DisabledLLMProvider`. The E2E test manually invokes worker behavior rather than proving the deployed composition executes end to end.

14. **[PR #8, audit append logic and schema] Audit-chain sequencing is unsafe with multiple publishers.** Appends lack a unique causation constraint and do not lock the aggregate while choosing the next sequence/hash. Concurrent publishers can allocate conflicting sequence positions or duplicate logically identical events.

### Medium

15. **[PR #2–#10, mutation API surface] Idempotency protection is incomplete.** Intent creation is protected, but approval, cancellation, and administrative mutation routes do not consistently require and persist idempotency keys.

16. **[PR #2–#10, `POST /v1/intents` and correlation handling] Caller-controlled provenance remains possible.** Direct intent creation allows a client to claim `source=AGENT`, and correlation is derived from a token claim rather than the server's request ID. Audit provenance can therefore differ from the actual ingress path.

17. **[PR #2–#10, API request models] Unknown outer fields are silently ignored.** Pydantic boundary models do not consistently reject extras even though the documentation claims strict rejection. Misspelled or obsolete security-relevant fields can be accepted without effect.

18. **[PR #2–#10, authentication configuration] Production authentication uses a static HS256 shared secret.** There is no JWKS/OIDC validation, issuer-key rotation, or asymmetric trust boundary. This is acceptable only as an explicit learning-mode limitation, not as a production-ready identity design.

19. **[PR #8–#10, audit verification and emission] Audit completeness is overstated.** An empty audit stream reports `chain_valid=true`; tail deletion is not detectable without an external anchor; notification delivery is not itself audited; and creation actor attribution can be a command identifier rather than the authenticated principal.

20. **[PR #7–#10, outbox retry selection] Scheduled backoff is written but not honored.** Publishers persist `next_attempt_at`, but selection does not filter on it, allowing immediate retry loops rather than bounded backoff.

21. **[PR #8–#10, observability] Telemetry does not cover the safety claims.** The implementation exposes only limited notification counters; it lacks a configured exporter/endpoint plus metrics and alerts for execution attempts, revalidation failures, stale claims, duplicate suppression, reconciliation, audit-chain failures, and outbox lag. Service logs alone do not close these gaps.

22. **[PR #2–#10, database schema] State-machine integrity relies too heavily on application code.** Transition, evaluation, approval, and execution records lack complete foreign keys/check constraints back to intents. Advertised immutable intent and approval history is not enforced through database permissions or append-only controls.

### Low

23. **[PR #10, `architecture-review.md`] The architecture status is internally inconsistent.** Planning-only and not-approved language remains while release evidence is appended, and several claims lack source pointers or tests at the actual semantic boundary.

## Previously known findings now resolved

None can be demonstrated as resolved. The earlier conversation did not preserve concrete findings with code locations, and no subsequent commit landed before this review snapshot.

## Previously known findings still open

- **Blocker — PR #9/#10:** Test-count confidence remains unsupported because the crash test never crosses the provider side-effect boundary.
- **Blocker — PR #3–#5:** Scopes, obligations, and same-route policy tightening remain untested and unenforced.
- **High — PR #4:** Inverse L1/L2 role assignment is not covered.
- **High — PR #9:** Chunked and missing-`Content-Length` bodies are not covered by payload-limit tests.
- **High — PR #8:** Multi-publisher audit append races are not exercised.
- **High — PR #6:** Provider-success/process-death recovery and reconciliation are not proven against a real adapter.

## Best-practice improvements

- **Best Practice — PR #2–#8:** Consolidate duplicate transition and outbox writers so every state change has one enforcement point for actor, tenant, causation, and audit metadata.
- **Best Practice — PR #2–#8:** Add database foreign keys, state/level checks, unique causation identifiers, and a one-active-policy constraint where the domain requires them.
- **Best Practice — PR #8:** Externally anchor signed audit heads and use separate application, audit-writer, and migration database roles. An in-database hash chain cannot defend against a database owner rewriting the chain.
- **Best Practice — PR #7–#9:** Rate-limit on validated principal and tenant identity through a shared store when multiple replicas are supported.
- **Best Practice — PR #1/#10:** Pin the `uv` tool and runtime base image by version or digest, add dependency/container scanning, and define explicit supported-version update policy.
- **Best Practice — PR #6–#10:** Remove or wire unused abstractions such as `ControlledError`, `TickObservation`, and `state_query`; dead safety-shaped code makes the implemented boundary harder to identify.
- **Best Practice — PR #1–#10:** Make required CI jobs branch-protection gates and prevent a merge while Docker, lint, migration, or semantic safety tests are red.

## Cross-stack residual risks

- Provider idempotency and conditional-write support remain mandatory because the application cannot guarantee exactly-once external side effects by database transaction alone.
- A database owner can truncate or rebuild the audit chain unless chain heads are independently anchored and monitored.
- The in-memory rate limiter, demo adapter, in-memory notification sink, process-local metrics, and disabled LLM provider are learning-mode components, not production controls.
- No real adapter or IAM integration proves that the execution principal is bounded to the approved action, target, and parameters.
- Stacked PR test success can hide later regressions in earlier phases unless every head is tested against its actual base and the final stack is tested as deployed.
- Mergeability is currently misleading because required CI enforcement is absent or insufficient while every run is red.

## Recommended fix order

1. Repair the Docker build, clear all lint errors, and require every CI job to pass before merge.
2. Split durable claim/`EXECUTING` persistence from the external provider call, then recover committed stale claims through reconciliation rather than resend-by-default.
3. Add a real crash-after-provider-success test that kills or interrupts execution before result persistence and proves no duplicate side effect.
4. Bind approval levels to roles; enforce scopes and obligations; compute and validate an approval decision digest; invalidate approval for every material current-policy change, including same-route tightening.
5. Make exact target identity validation fail closed, and add bounded timeouts plus conditional/precondition checks at execution.
6. Tenant-scope policy administration, policy selection, outbox records, and audit attribution.
7. Persist expiry without rolling it back; normalize idempotency uniqueness races; enforce streaming body limits; extend idempotency to every mutation.
8. Make Compose run migrations, identity setup, role bootstrap, worker, adapter, and observability components; make E2E test the deployed topology.
9. Serialize audit appends, enforce unique causation, externally anchor chain heads, add operational telemetry, and reconcile documentation with verified behavior.

## Consolidated changes since last review

- No code pushes were detected before this report was added.
- No previously reported defect can be proven fixed.
- The current stack adds a passing-test narrative but still has red CI and unproven safety at the most important execution boundary.
- Do not merge the stack until at least the first five items in the recommended fix order are implemented and adversarially retested.
