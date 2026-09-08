# Concept 05 — Safe Execution and Ambiguous Outcomes

The execution worker is the sole privileged path. It receives a process-local permit, converts its typed parameters to one allow-listed adapter command, persists `EXECUTING`, and sends a stable operation key and provider precondition. The LLM/API cannot import or reach this path.

Retry depends on certainty, not merely an error class. Failure before send can retry. A safe read can usually retry. A mutation after an acknowledged provider idempotency key may retry according to contract. A timeout after possible send is ambiguous: the system records `EXECUTION_UNKNOWN` and queries provider status/audit evidence with the same operation key. Blind retry could delete or provision twice.

Reconciliation is observation, not re-execution. It maps conclusive provider evidence to success or failure. Insufficient evidence leaves unknown and alerts an operator. Compensation is a new explicit action with its own risk; it is not an automatic eraser for history.

Typed adapter methods prevent arbitrary endpoints, shell fragments, and unknown resource types. Provider credentials are scoped and located only in the worker/adapter environment.

Self-check: place failures at before-send, after-send, after-response, and after-commit. For each, decide whether replay returns a record, retries transport, reconciles, or alerts.

