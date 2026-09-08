# Scenario 05 — MEDIUM Scale Service

An operator asks the agent to set `payments-api` in staging to four replicas (`scale_service`).

Expected reasoning:

1. `scale_service` starts at MEDIUM. Policy—not the model—decides the branch: auto-allow, one authorized approver, or block.
2. Under the current staging policy bundle the action auto-allows. Risk evidence, disposition, route, policy version, and obligations are still persisted, and the intent is still revalidated against the live deployment before execution.
3. Policy variation: the same request against the production bundle requires one authorized human approver who is not the requester. The approval binds the digest, policy version, scope snapshot, and TTL (default 15 minutes, maximum 60).
4. Escalation variation: a cost or blast-radius factor raises the band to HIGH regardless of the MEDIUM minimum. Rules may raise risk but never lower it.
5. Failure variation: the approval TTL elapses before the worker claims execution. The revision becomes `EXPIRED`; execution requires a fresh approval, and the current policy may demand a different route than the expired one.
