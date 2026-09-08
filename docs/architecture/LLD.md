# Low-Level Design

**Status:** PROPOSED_FOR_REVIEW  
**Implementation rule:** Exact names may change only through an accepted ADR and synchronized updates to this document and `Implementation.md`.

## 1. Planned source layout

```text
src/hitl_ops/
  api/{app.py,dependencies.py,errors.py,intents.py,approvals.py,admin.py}
  agent/{orchestrator.py,schemas.py}
  domain/{enums.py,models.py,intent.py,risk.py,policy.py,state_machine.py}
  application/{commands.py,revalidation.py,execution.py,reconciliation.py}
  infrastructure/{database.py,orm.py,repositories.py,outbox.py,identity.py}
  adapters/{base.py,demo.py}
  observability/{logging.py,telemetry.py}
tests/{unit,integration,contract,e2e}/
alembic/versions/
```

These are implementation targets, not files created by this planning package.

## 2. Domain enums

`ToolName`: the five V1 tools.  
`RiskBand`: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.  
`PolicyDisposition`: `ALLOW`, `REQUIRE_APPROVAL`, `BLOCK`.  
`ApprovalRoute`: `NONE`, `SINGLE`, `CRITICAL_TWO_STEP`.  
`ApprovalDecision`: `APPROVE`, `REJECT`.  
`IntentState`: exactly the state-machine states.  
`ExecutionOutcome`: `SUCCEEDED`, `FAILED`, `UNKNOWN`.

## 3. Data models

All identifiers are UUIDv7 or server-generated UUIDs; timestamps are timezone-aware UTC; JSON documents use JSONB; tables include `created_at`. Tenant-scoped unique indexes always include `tenant_id`.

### `action_intents`

`intent_id`, `tenant_id`, `revision`, `tool`, `canonical_parameters`, `intent_digest`, `requester_id`, `requester_rationale`, `raw_proposal` (redacted, size-bounded JSONB of the original model proposal, truncated with an explicit overflow marker when it exceeds the configured limit; stored as untrusted evidence per FR-03 and never executed directly), `source` (`AGENT` or `DIRECT`), `state`, `state_version`, `approval_expires_at`, `created_at`, `updated_at`.

Constraints: composite primary key `(tenant_id,intent_id,revision)`; non-unique digest lookup index; `revision >= 1`; approved revisions are not updated except lifecycle/state fields. New material parameters create a new row with the same `intent_id` and incremented revision. Idempotency records—not digest uniqueness—deduplicate client retries.

### `risk_evaluations`

`id`, `tenant_id`, `intent_id`, `intent_revision`, `band`, `factors`, `rule_version`, `evaluated_context`, `evaluated_at`; unique per intent revision and evaluation generation. Historic records remain immutable.

### `policy_evaluations`

`id`, intent reference, `disposition`, `route`, `required_roles`, `required_scopes`, `obligations`, `reason_codes`, `policy_version`, `approval_ttl_seconds`, `evaluated_at`.

### `approval_decisions`

`id`, intent reference, `intent_digest`, `level`, `decision`, `actor_id`, `actor_roles_snapshot`, `scope_snapshot`, `reason`, `policy_version`, `decided_at`, `expires_at`. Unique approving actor per intent revision; unique approved level where single-decision semantics require it. Decisions are append-only.

### `state_transitions`

`id`, intent reference, `from_state`, `to_state`, `actor_type`, `actor_id`, `command_id`, `reason_code`, `metadata`, `occurred_at`; unique `command_id` and monotonic sequence per revision.

### `executions`

`id`, intent reference, `operation_key`, `attempt`, `status`, `provider_operation_id`, `request_fingerprint`, `precondition_snapshot`, `started_at`, `finished_at`, `error_code`, `result_summary`; unique intent revision and unique operation key. An attempt records transport interaction; retry rules depend on outcome certainty.

### `idempotency_records`

`tenant_id`, `actor_id`, `scope`, `key_hash`, `request_hash`, `status`, `resource_type`, `resource_id`, `response_status`, `response_body`, `expires_at`; unique `(tenant_id,actor_id,scope,key_hash)`. Same key/different request hash returns conflict.

### `audit_events` and `outbox_messages`

Audit: `event_id`, tenant, aggregate ID/revision, sequence, event type, actor, timestamp, correlation/causation IDs, policy/risk versions, sanitized attributes, `previous_hash`, `event_hash`. Unique aggregate sequence and event ID. Outbox: `id`, topic, payload, attempts, next-attempt, published-at, created-at. State and outbox insert commit together.

### `role_assignments`

`id`, tenant, principal, role, environment/resource scope, valid-from, valid-until, revoked-at, granted-by. Current authorization is evaluated from these assignments plus validated identity claims.

## 4. Canonical intent

Each tool has a strict Pydantic model with `extra="forbid"`, bounded strings/enums/integers, and normalized identifiers. The canonicalizer serializes UTF-8 JSON with sorted keys and compact separators after model validation. Digest input is a versioned envelope:

```json
{"digest_version":"1","tenant_id":"…","requester_id":"…","tool":"scale_service","revision":1,"parameters":{"environment":"staging","replicas":4,"service":"api"}}
```

SHA-256 is an integrity binding, not a signature. Approval authenticity comes from authenticated server-side decision recording.

## 5. Service interfaces

```text
canonicalize(proposal, actor) -> CanonicalIntent
evaluate_risk(intent, context, rules) -> RiskEvaluation
evaluate_policy(intent, risk, actor, context, bundle) -> PolicyDecision
transition(intent_id, command, actor, expected_version) -> IntentSnapshot
revalidate(intent_id, execution_actor) -> ExecutionPermit | RevalidationFailure
execute(permit) -> ExecutionRecord
reconcile(execution_id) -> ExecutionRecord
append_outbox(event, transaction) -> None
```

`ExecutionPermit` is short-lived, process-local data containing the exact intent reference, digest, validated typed parameters, precondition token, operation key, and issued timestamp. It is not a bearer credential and is never accepted from an API client.

## 6. API contracts

Base path: `/v1`. All mutation requests require `Idempotency-Key`; clients may send `If-Match`/`expected_state_version` for decisions. Authentication supplies actor and tenant.

### `POST /intents`

Input: `{tool, parameters, rationale, source}`. Output `201`: `{intent_id, revision, digest, risk, policy, state, state_version, approval_requirements, expires_at, links}`. Unknown/extra fields fail with `422 VALIDATION_FAILED`. Same idempotency key and request returns original response; different body returns `409 IDEMPOTENCY_CONFLICT`.

### `POST /agent/intents`

Input: `{request, context_refs}`. The server calls the model once within configured bounds, validates its proposal, then returns the same intent response. Model refusal/invalid output returns a controlled error and creates no executable intent.

### `GET /intents/{intent_id}`

Returns tenant-scoped current revision, risk/policy summaries, approvals, execution status, and permitted actions. Sensitive adapter output is not included.

### `POST /intents/{intent_id}/approvals`

Input: `{revision, intent_digest, level, decision, reason, expected_state_version}`. Output `200` snapshot. Server ignores any client-supplied actor/role. Stale digest/version returns `409`.

### `POST /intents/{intent_id}/cancel`

Input: `{revision, reason, expected_state_version}`. Output `200` snapshot or `409` once execution is claimed.

### Administrator contracts

`POST /admin/role-assignments`, `POST /admin/role-assignments/{id}/revoke`, and `POST /admin/policy-bundles` are reserved for the Administrator role and are served by the Phase 03 administration commands. Policy bundles are authored in source control; the admin contract registers/activates a reviewed version. Every change appends an administrative audit event (§10); historic approval evidence is never altered.

### `GET /intents/{intent_id}/events`

Auditor/authorized operator view of sanitized ordered events with cursor pagination.

There is no public execute endpoint. A worker selects eligible approved revisions; an authenticated internal command may exist only behind service identity and network controls.

## 7. Transaction and concurrency design

Creation transaction writes intent, risk, policy, initial transitions, idempotency response, and outbox. Approval transaction locks intent revision, checks state/version/digest/TTL/current authorization/distinctness, appends decision, transitions, and writes outbox. Execution claim atomically changes `AUTO_APPROVED|APPROVED` to `REVALIDATING` if version matches and creates the unique execution row. Revalidation finishes under a bounded claim/lease; the final transition to `EXECUTING` persists before adapter send.

PostgreSQL uniqueness and row-level locking are sufficient for V1. A Redis lock may reduce contention but cannot permit an operation the database would reject.

## 8. Idempotency and retries

- API idempotency scope is tenant + actor + route + key, retained at least 24 hours.
- Operation key is stable for an intent revision and supplied to providers that support idempotency.
- Safe reads may retry bounded transient failures with exponential backoff and jitter.
- Mutations retry only before confirmed send, or when provider idempotency/status lookup proves safety.
- Database serialization/deadlock errors retry the whole transaction a small bounded number of times.
- `EXECUTION_UNKNOWN` triggers reconciliation; it is never converted to `FAILED` merely because time elapsed.

## 9. Revalidation order

1. Load/lock claimed intent and current state/version.
2. Recompute canonical digest.
3. Check approval TTL, route, levels, principals, and current role/scope.
4. Re-evaluate current risk and policy bundle.
5. Fetch live target identity/version/health/metadata.
6. Verify obligations and compare approved/live material context.
7. Persist precondition snapshot and `EXECUTING`; issue permit.

Any failed gate records a stable reason and moves to `STALE`, `EXPIRED`, or `FAILED` only as defined by the state machine.

## 10. Audit event contract

Required event types: intent created/revised, risk evaluated, policy evaluated, approval requested/decided, approval expired/staled, cancellation, execution claimed/started/succeeded/failed/unknown/reconciled, notification delivery, and administrative role/policy changes. `event_hash = SHA-256(canonical_event_without_hash + previous_hash)`. Payloads contain references and sanitized summaries, not credentials or unrestricted model/provider payloads.

## 11. Observability

Structured fields include timestamp, severity, service, environment, correlation ID, trace/span IDs, tenant hash, actor hash, intent ID/revision, state, transition, tool, risk, policy version, execution ID, outcome, and error code. Metrics avoid unbounded labels. Traces link API → model → transaction → approval → worker → adapter without recording secrets.

## 12. Test architecture

- Unit: canonicalization/digest, risk tables/properties, policy, transitions/guards, authorization, retry classification, audit hashing.
- Integration: PostgreSQL constraints/transactions/migrations/outbox, concurrent decisions/claims, API idempotency.
- Contract: every typed adapter using shared fixtures for idempotency, preconditions, confirmed and ambiguous outcomes.
- E2E: all five tools and four risk routes using the demo adapter, including expiration/staleness/reconciliation.
- Security/failure: injection, tenant isolation, revoked roles, secret redaction, database/Redis/LLM/provider/audit-sink outages.
