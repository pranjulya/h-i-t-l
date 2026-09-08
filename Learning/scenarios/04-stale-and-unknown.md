# Scenario 04 — Stale Approval and Unknown Execution

Part A: staging scale from 3 to 6 receives policy-required approval. Before execution, deployment version and health change. Revalidation compares the live snapshot with approved material context and obligations. If the new state changes risk or invalidates a precondition, the revision becomes `STALE`; it is not silently retried or edited.

Part B: a production provision request passes HIGH approval and starts execution. The provider receives the operation but the connection times out before a response. The local state becomes `EXECUTION_UNKNOWN`. Reconciliation queries provider state/audit using the stable operation key. A confirmed resource yields `SUCCEEDED`; a conclusive provider rejection yields `FAILED`; missing evidence remains unknown and alerts an operator.

Questions to answer:

- Which changes are material enough to require a new revision?
- Why is a simple retry unsafe in Part B?
- Which audit events prove the system did not bypass approval?
- Which metrics distinguish stale-intent volume from provider ambiguity?

