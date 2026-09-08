# Demo Script — V1 learning stack

One documented command starts the stack: `docker compose up --build`
(or locally: `uv run uvicorn hitl_ops.api.app:create_app --factory --reload`
with PostgreSQL 16 running and migrations applied).

Prerequisites: demo identities exist (role assignments below), the seed policy
bundle `policy-1` is active, and the demo adapter is the configured target.

| Identity | Role | Purpose |
|---|---|---|
| `user-1` | requester (+ approver for demos) | proposes intents |
| `approver-1` | approver | approves HIGH/MEDIUM |
| `l1-user` | critical_approver_l1 | first CRITICAL approval |
| `l2-user` | critical_approver_l2 | second CRITICAL approval |
| `admin-1` | administrator | grants roles, registers/activates policy bundles |

## Journey 1 — LOW inspection (auto-allow)

1. `POST /v1/intents` — inspect_service on staging; observe `AUTO_APPROVED` with
   persisted risk (`LOW`), policy version, and obligations.
2. Worker tick executes; `GET /v1/intents/{id}` shows `SUCCEEDED` with the
   provider operation id; the audit chain verifies.

## Journey 2 — MEDIUM policy branches (scale)

1. Staging scale request auto-allows.
2. Production scale request escalates to HIGH and waits in `PENDING_APPROVAL_1`
   — policy plus monotonic risk escalation decide, never the model.
3. Approve as `approver-1`; the approval binds digest, policy version, scope
   snapshot, and TTL.

## Journey 3 — HIGH restart with one approval

1. `POST /v1/intents` — restart_service.
2. Approval against the exact digest; concurrent conflicting decisions lose
   with `STATE_CONFLICT`.
3. Worker revalidates (digest, TTL, current authorization, policy, live target)
   and executes once.

## Journey 4 — CRITICAL deletion with two-step approval

1. `POST /v1/intents` — delete_resource.
2. L1 approves (`PENDING_APPROVAL_2` published automatically); the same actor
   cannot satisfy L2; L2 approves.
3. A one-byte material change would produce a new revision and stale approvals.

## Journey 5 — Ambiguity and reconciliation

1. Restart `timeout-api`: the demo provider's response is lost after send —
   the intent enters `EXECUTION_UNKNOWN`; it is never guessed or retried.
2. The next worker pass reconciles by provider operation key and maps the
   intent to `SUCCEEDED` from provider evidence.

## Journey 6 — Forensics

1. `GET /v1/intents/{id}/events` — ordered transitions plus the hash-linked
   audit view with `chain_valid`.
2. Every who/what/when/why/version/outcome is reconstructable from the audit
   trail without touching the primary tables.
