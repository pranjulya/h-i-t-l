# Scenario 02 — HIGH Production Restart

An operator proposes a rolling restart of production `payments-api`.

Expected reasoning:

1. `restart_service` starts at HIGH. Policy requires one authorized human; requester cannot self-approve.
2. The approver sees exact environment, service, strategy, digest, risk reasons, context age, and expiry.
3. Approval commits against expected state/version. Another concurrent decision loses with `STATE_CONFLICT`.
4. The worker rechecks the digest, approval TTL, approver's current scope, current policy, live deployment identity/version, and restart preconditions.
5. It persists execution start before invoking the typed restart with operation/precondition keys.

Failure variation: the approver role is revoked after approval. Revalidation marks the revision stale. Restoring the role does not revive old evidence automatically; a fresh revision/approval is the clearest safe workflow.

