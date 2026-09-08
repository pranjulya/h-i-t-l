# Scenario 06 — HIGH Resource Provisioning

An operator asks the agent to provision a new cache resource in production (`provision_resource`).

Expected reasoning:

1. `provision_resource` starts at HIGH. Production environment, cost class, or policy flags can escalate further but never reduce the band.
2. The intent stores the exact resource type, name, region, size, and cost class; any material change creates a new revision with a new digest and invalidates prior approvals.
3. Exactly one authorized approver may approve; the requester cannot self-approve, and the decision is append-only. Concurrent approve/reject races resolve to one committed winner, with `STATE_CONFLICT` for the loser.
4. Before execution the worker rechecks the digest, approval TTL, the approver's current authorization, the current policy bundle, and live target preconditions (name/region must still match the precondition snapshot).
5. The executor persists `EXECUTING` with the operation key before the typed adapter call; a timeout after send becomes `EXECUTION_UNKNOWN` and reconciles by provider operation key—never a blind second create.

Failure variation: policy is updated to block new production provisioning after approval. Revalidation stales the revision even though the approval itself was valid when recorded.
