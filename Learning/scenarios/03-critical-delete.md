# Scenario 03 — CRITICAL Resource Deletion

An operator proposes deleting database replica `replica-17` in production using the allowed deletion mode.

Expected reasoning:

1. Deletion is always CRITICAL. Policy also requires a current backup obligation and two-step approval.
2. L1 is a non-requester with matching scope. Only after L1 commits is L2 requested.
3. L2 is different from requester and L1 and has the L2 role/scope. Both approve the same digest and expiry window.
4. Revalidation confirms current roles, policy, target identity/version, replica status, backup obligation, and unchanged deletion mode.
5. Provider ambiguity never causes automatic re-delete; reconciliation queries by operation key/provider evidence.

Failure variation: the requester changes `replica-17` to `primary-01`. That is a new material revision and digest. Both approvals on the old revision are stale and cannot transfer.

