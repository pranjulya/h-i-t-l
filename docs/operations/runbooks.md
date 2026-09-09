# Operations Runbooks

Each runbook names trigger, diagnosis, containment, recovery, and evidence
preservation. All drills are executable in the demo environment (demo adapter,
single PostgreSQL, no Redis).

## RB-1 — Execution unknown (ambiguous provider outcome)

- **Trigger:** intent state `EXECUTION_UNKNOWN`, alert on unknown-outcome count, or auditor query.
- **Diagnosis:** open `GET /v1/intents/{id}` for the execution status and provider operation id; check the provider's status endpoint for that operation key; review the audit trail via `GET /v1/intents/{id}/events`.
- **Containment:** do **not** retry the operation manually. The claim row and operation key guarantee at-most-one logical provider action; manual retries create a second one.
- **Recovery:** let the worker reconciliation pass run (it runs each tick), or trigger reconciliation manually. Provider evidence maps the intent to `SUCCEEDED`/`FAILED`; missing evidence keeps `EXECUTION_UNKNOWN`.
- **Evidence preservation:** preserve the audit chain for the aggregate, the execution row (`precondition_snapshot`, `provider_operation_id`, attempts), and provider-side status output. Never edit rows (the audit table is append-only at the database level).

## RB-2 — Stale approvals (policy/authorization drift)

- **Trigger:** `STALE` reasons (`approver_no_longer_authorized`, `policy_now_blocks`, `digest_mismatch`, `target_replaced`) in transitions or alerts.
- **Diagnosis:** read the failing gate reason from the transition `reason_code` and audit event; compare the current policy bundle version and role assignments with the approval snapshot.
- **Containment:** none needed — revalidation already refused execution. Verify no new approval is possible against the stale revision.
- **Recovery:** create a fresh intent revision (a material change already creates one) and re-run risk/policy/approval under current policy.
- **Evidence preservation:** the original approval decision rows and snapshots stay append-only; include them in the incident record.

## RB-3 — Outbox / audit backlog

- **Trigger:** alert on unpublished outbox age/count or audit chain gap alarm.
- **Diagnosis:** `SELECT topic, attempts, next_attempt_at, created_at FROM outbox_messages WHERE published_at IS NULL ORDER BY created_at;` — large `attempts` points at a failing sink; zero rows but a gap alarm points at the publisher being down.
- **Containment:** stop non-essential publishers; keep the API read-only if the audit sink cannot be restored quickly (evidence continuity is part of the guarantee).
- **Recovery:** restore the sink (or notification transport); the publisher retries with backoff automatically. Backlog drains oldest-first.
- **Evidence preservation:** outbox rows are never deleted before audit publication; verify `verify_aggregate_chain` passes after the backlog drains.

## RB-4 — Credential exposure / rotation

- **Trigger:** suspicion or confirmed disclosure of identity signing material or adapter credentials.
- **Diagnosis:** audit trail for unexpected approvals/executions; provider audit IDs for operations not backed by intents.
- **Containment:** rotate the identity verification material (invalidates all tokens — fail closed); revoke affected role assignments (revocation is observed at revalidation and at decision time).
- **Recovery:** re-grant roles from the administration commands (each change audited); re-issue identities from the OIDC provider.
- **Evidence preservation:** export the audit chain before rotation; role assignment rows are append-only with `revoked_at` timestamps and grant/revoke audit events.

## RB-5 — Database loss / fail-closed degradation

- **Trigger:** readiness endpoint fails (`503 unavailable`), mutation API returns retryable failures.
- **Diagnosis:** `GET /health/ready` distinguishes connectivity loss from migration-head mismatch (both fail closed, without details on the wire); check PostgreSQL and the migration head.
- **Containment:** PostgreSQL loss already rejects mutations; reads that require the DB also fail. Do not restore from a source other than the authoritative database.
- **Recovery:** restore PostgreSQL (point-in-time recovery), run `alembic upgrade head` if the schema is behind, verify readiness, then let the worker resume. Outbox rows recoverable after commit guarantee no state change lost its audit trail.
- **Evidence preservation:** the audit table is append-only; compare `event_hash` chains after recovery to prove no history rewrite.

## RB-6 — Notification outage

- **Trigger:** notification backlog alert or approver reports a missing request.
- **Diagnosis:** unpublished `intent.transitioned` rows with `to_state` `PENDING_APPROVAL_*`; notification attempts and backoff on those rows.
- **Containment:** approvals remain pending (safe); approvals may still be made through the authenticated API — notification is a convenience, not an authorization path.
- **Recovery:** restore the sink; the publisher redelivers; deterministic notification ids let consumers drop duplicates.
- **Evidence preservation:** delivery attempts live on the outbox rows; delivered notification ids are audited via `notification.delivered` events.
