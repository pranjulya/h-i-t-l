# Product Requirements Document

**Product:** Project 05 — Human-in-the-Loop AI Ops Workflow  
**Status:** PROPOSED_FOR_REVIEW  
**V1 promise:** An AI may propose an operational action; only deterministic controls and authorized humans may permit its execution.

## 1. Problem

AI Ops agents can summarize incidents and recommend changes, but directly connecting probabilistic model output to infrastructure credentials creates unacceptable risks: hallucinated parameters, excessive privilege, duplicated execution, stale context, bypassed approvals, and weak forensic evidence. V1 demonstrates how to preserve useful automation while keeping authority in testable application code.

## 2. Users and roles

| Role | Need | Authority |
|---|---|---|
| Operator | Investigate and propose routine remediation | Create intents; view permitted resources |
| Approver | Evaluate elevated operational actions | Approve/reject HIGH and eligible MEDIUM intents within assigned scope |
| Critical approver L1 | Confirm operational need | First CRITICAL approval within assigned scope |
| Critical approver L2 | Independently confirm critical risk | Second CRITICAL approval; must be a different principal from requester and L1 |
| Auditor | Reconstruct decisions and executions | Read audit views; cannot approve or execute |
| Administrator | Manage identities, roles, and policy bundles | Cannot silently alter existing approval evidence |
| Service identity | Run controlled orchestration/execution | Least-privilege machine permissions; no human approval role |

## 3. Goals

1. Accept an allow-listed AI Ops action proposal and convert it to a validated, canonical, immutable intent revision.
2. Classify risk deterministically and evaluate versioned policy.
3. Route LOW, MEDIUM, HIGH, and CRITICAL actions through the locked approval model.
4. Bind every approval to the intent digest, policy version, risk result, approver identity, scope, and expiry.
5. Revalidate policy, authorization, approval freshness, intent digest, and live target preconditions immediately before execution.
6. Execute once through a typed allow-listed adapter and produce complete, tamper-evident audit evidence.
7. Recover safely from retries, process crashes, provider timeouts, and ambiguous adapter outcomes.
8. Teach the architecture through paired phase and learning documents.

## 4. Non-goals

- A generic workflow SaaS, visual workflow builder, arbitrary user-defined tools, or plugin marketplace.
- Autonomous remediation without deterministic application controls.
- Multi-region active-active operation, Kubernetes, event sourcing, or a custom policy language in V1.
- Replacing enterprise IAM, incident management, or infrastructure-native audit logs.
- Letting LangGraph, prompts, or model output define business state transitions.
- Guaranteeing infrastructure rollback for operations that are inherently irreversible.

## 5. V1 tools

| Tool | Purpose | Minimum risk | Material parameters |
|---|---|---:|---|
| `inspect_service` | Read health, replicas, version, and recent status | LOW | tenant, environment, service, requested fields |
| `restart_service` | Restart one service deployment | HIGH | tenant, environment, service, strategy |
| `scale_service` | Set desired replica count | MEDIUM | tenant, environment, service, replicas |
| `provision_resource` | Create an allow-listed resource type | HIGH | tenant, environment, resource type, name, region, size, cost class |
| `delete_resource` | Delete one identified resource | CRITICAL | tenant, environment, resource type, resource ID, deletion mode |

Rules may raise risk but never lower it below the minimum. Production environment, broad blast radius, high cost, degraded dependencies, or policy flags can escalate any action. `delete_resource` remains CRITICAL in V1.

## 6. Approval policy

| Risk | Decision |
|---|---|
| LOW | Auto-allow if policy and authorization pass |
| MEDIUM | Policy decides auto-allow, one approver, or block |
| HIGH | Exactly one authorized human approver; requester cannot self-approve |
| CRITICAL | L1 and L2 approvals from distinct authorized humans; requester cannot approve either level |

Approval expires after the policy-defined TTL, defaults to 15 minutes, and never exceeds 60 minutes. Rejection, cancellation, expiry, policy change requiring stricter treatment, loss of approver authorization, or any material parameter change prevents execution.

## 7. Core user journeys

### Read-only inspection

An operator asks the agent to inspect a service. The agent proposes structured parameters. The application validates and canonicalizes them, assigns LOW risk, evaluates policy, auto-allows, revalidates the target, executes the read, and returns sanitized results.

### High-risk restart

An operator proposes a production restart. The system records a HIGH intent and requests one authorized approval. The approver sees the exact parameters, rationale, risk evidence, live context age, and expiry. After approval the executor rechecks all gates, performs one idempotent restart, and records the outcome.

### Critical deletion

An operator proposes deletion. Two distinct approval levels independently approve the same digest. A changed resource ID or deletion mode creates a new revision; old approvals become stale. The executor refuses unless both current approvals, policy, authorization, target preconditions, and deletion guardrails remain valid.

## 8. Functional requirements

- **FR-01:** Reject unknown tools and unknown/extra parameters.
- **FR-02:** Canonicalize a versioned JSON envelope containing tool, parameters, tenant, requester, and revision, then calculate its SHA-256 digest.
- **FR-03:** Preserve the original model proposal as untrusted evidence, stored redacted and size-bounded per FR-14; never execute it directly.
- **FR-04:** Store risk factors, score/band, rule version, policy inputs, decision, obligations, and policy version.
- **FR-05:** Enforce only transitions listed in the state-machine document.
- **FR-06:** Authorize approvals using current identity claims plus server-side role/scope assignments.
- **FR-07:** Require distinct principals for requester, CRITICAL L1, and CRITICAL L2.
- **FR-08:** Make approval decisions append-only; corrections are new records.
- **FR-09:** Revalidate within the same execution claim flow and refuse stale or changed intent.
- **FR-10:** Deduplicate intent creation by tenant-scoped idempotency key and execution by intent revision.
- **FR-11:** Represent ambiguous adapter outcomes as `EXECUTION_UNKNOWN`; never blindly retry a destructive call.
- **FR-12:** Append an audit event for every accepted command and state transition using an outbox in the same database transaction.
- **FR-13:** Expose intent creation, retrieval, approval, rejection, cancellation, and execution-status APIs; do not expose a public “force execute” endpoint.
- **FR-14:** Redact secrets and bound model rationale/result payloads before logs or audit storage.
- **FR-15:** Correlate request, intent, approval, execution, trace, and actor identifiers.

## 9. Non-functional requirements

- **Security:** deny by default; least privilege; OIDC/JWT validation; tenant isolation; no credentials in LLM context; encryption in transit and at rest.
- **Durability:** acknowledged state changes survive process loss; PostgreSQL is authoritative.
- **Concurrency:** one execution claim per intent revision; optimistic state versioning; database uniqueness constraints enforce invariants.
- **Availability:** degraded Redis disables only accelerators; PostgreSQL loss makes mutating operations fail closed.
- **Latency:** p95 under 500 ms for non-LLM command APIs excluding external identity/provider latency; approval notification is asynchronous.
- **Audit:** append-only application permissions, hash-linked events, outbox delivery, retention policy, and reconciliation with provider audit IDs.
- **Observability:** structured logs, OpenTelemetry traces, metrics for decisions, queues, transition failures, stale approvals, execution latency, retries, and unknown outcomes.

## 10. Stable outcomes

API failures use `{error: {code, message, retryable, correlation_id, details}}`. Required codes include `VALIDATION_FAILED`, `UNKNOWN_TOOL`, `UNAUTHORIZED`, `FORBIDDEN`, `POLICY_BLOCKED`, `APPROVAL_REQUIRED`, `APPROVAL_STALE`, `APPROVAL_EXPIRED`, `STATE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `PRECONDITION_FAILED`, `EXECUTION_FAILED`, and `EXECUTION_UNKNOWN`.

## 11. Success criteria

- Every V1 tool follows its required approval route under tested policies.
- A one-byte material change produces a different digest and prevents use of previous approvals.
- Two concurrent execution requests cause at most one adapter invocation.
- Replayed API calls return the original semantic result or a deterministic conflict.
- Requester self-approval and duplicate CRITICAL approvers are rejected.
- Policy or authorization changes before execution are observed during revalidation.
- A crash between adapter response and local persistence is detectable and enters reconciliation, not blind retry.
- Audit reconstruction answers who proposed, evaluated, approved, attempted, and completed an action, with timestamps and versions.

## 12. Release gate

V1 is releasable only after threat-model controls, architecture review checks, phase acceptance criteria, migration tests, concurrency tests, failure injection, and an operator recovery drill pass. This planning package itself authorizes no implementation or deployment.
