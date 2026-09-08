# Concept 04 — Revalidation, TOCTOU, Concurrency, and Idempotency

TOCTOU is the gap between checking safety and using the result. Approval may be valid at 10:00 while the target, policy, role, or incident state changes at 10:01. Execution therefore claims the revision exclusively and rechecks digest, TTL, approvals, current roles, current risk/policy, live target identity/version/health, and obligations immediately before send.

Different concurrency tools solve different problems. Optimistic `state_version` detects stale commands. Row locks serialize a short critical section. Unique constraints enforce invariants even when application processes race. Provider precondition tokens reject a mutation if remote state changed. A Redis lock can reduce contention but cannot replace any durable invariant.

Idempotency binds a client key to a request hash and stored semantic response. Same key plus same request replays. Same key plus different request conflicts. Execution uses a stable operation key per intent revision so provider-supported deduplication and status lookup can participate.

At-most-one local claim is not exactly-once remote effect. A crash after provider success but before local commit leaves uncertainty, which the execution/recovery concept addresses.

Self-check: for two simultaneous workers, describe the database update/constraint that permits one. Then explain why both acquiring a Redis lock at different moments still cannot prove remote uniqueness.

