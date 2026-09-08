# Threat Model

**Status:** PROPOSED_FOR_REVIEW  
**Method:** Trust-boundary analysis with STRIDE-style threats.

## 1. Assets

Infrastructure availability and data; provider credentials; tenant boundaries; action intent integrity; approval authenticity; policy integrity; execution uniqueness; audit evidence; personal data and secrets; operator trust.

## 2. Trust boundaries

1. User/browser to API.
2. API to identity provider.
3. Application to LLM provider.
4. API/orchestrator to deterministic domain services.
5. Control plane to PostgreSQL/Redis.
6. Execution worker to privileged adapter/infrastructure API.
7. Transactional outbox to observability/audit/notification sinks.

Model output, user rationale, infrastructure labels, logs from external systems, and notification content are untrusted data.

## 3. Threats and controls

| Threat | Example | Preventive controls | Detection/recovery |
|---|---|---|---|
| Prompt/tool injection | Incident text tells model to delete resources | Fixed tool allow-list; strict Pydantic schemas; extra fields forbidden; model has no credentials/network path | Rejected proposal metric and sanitized evidence |
| Intent tampering | Resource ID changes after approval | Immutable revisions; canonical digest bound to approval; DB constraints | Revalidation creates `STALE`; audit diff |
| Approval spoofing | Forged approver claim | OIDC signature/audience/issuer checks; DB-backed current roles/scopes; authenticated actor from server context | Auth failure alerts; immutable actor evidence |
| Self/collusive approval | Requester approves own critical delete | distinct-principal constraints and transactional guards | Conflict event and security metric |
| Cross-tenant access | Guess another intent UUID | tenant from identity context; scoped queries; no existence disclosure | denied-access audit and anomaly alerts |
| Policy downgrade | Admin changes policy before execution | versioned reviewed bundles; re-evaluation; no historic rewrite | policy-version audit; stricter change stales intent |
| TOCTOU | Target becomes production/degraded after approval | live precondition/version check immediately after claim and before call; provider conditional requests when available | `PRECONDITION_FAILED`/`STALE`, retry only via new revision |
| Replay/duplicate | Client or worker repeats delete | request idempotency records; one execution row/revision; provider operation key | duplicate metric; reconciliation |
| Concurrent approval/execution | Rejection races execution | row lock/CAS and expected state version | one winner; `STATE_CONFLICT` audit |
| Ambiguous timeout | Provider acted but response was lost | never blind-retry mutation; provider operation key/status API | `EXECUTION_UNKNOWN`, reconciliation runbook |
| Audit deletion/tampering | Privileged user edits history | append-only DB role, hash chain, outbox, separate retained sink | hash verification and sequence-gap alarm |
| Secret leakage | Token appears in prompt/log | credential isolation; structured redaction; payload size limits; no raw headers/prompts by default | secret scanning and incident rotation |
| Denial of service | Flood creates pending approvals | rate/size limits, quotas, bounded LLM calls, database indexes | queue/latency alerts and throttling |
| SSRF/dynamic execution | Model supplies arbitrary endpoint/command | typed adapters, enumerated resource types, no shell, no client URLs | unknown target rejection |
| Privilege escalation | API service calls cloud directly | network policy and separate worker identity; least-privilege adapter permissions | provider IAM/audit reconciliation |
| Notification deception | Approval message omits destructive detail | server-rendered exact intent summary and digest link; approval accepted only in authenticated API | compare notification ID to approval record |

## 4. Highest-risk abuse cases

### Stale approval reused for another target

An attacker obtains approval for a harmless target, then substitutes production. Canonical parameters, tenant, requester, tool, and revision are hashed. Approved rows are immutable. Execution recomputes and constant-time compares the digest, then checks live target identity.

### Double execution under retry

Two workers claim the same revision. A unique `executions(intent_id, revision)` constraint and atomic state/version update select one. The adapter receives a stable provider operation key. A response-loss case becomes unknown, not a second invocation.

### Compromised LLM

The model can only return untrusted JSON. It has no database, Redis, SDK, credential, adapter, or privileged network access. All proposal fields pass allow-list validation, risk, policy, approvals, and revalidation.

## 5. Security requirements

- Validate JWT algorithm, signature, issuer, audience, expiry, not-before, and token ID where revocation is supported.
- Derive actor/tenant from authenticated context, never request body.
- Use parameterized SQL through SQLAlchemy; disallow shell command construction.
- Encrypt transport; use managed secret storage and short-lived workload identity.
- Redact authorization headers, credentials, model prompts, and sensitive adapter output.
- Enforce request body, rationale, result, and audit attribute size limits.
- Separate application writer, migration owner, audit appender, and read-only auditor database roles.
- Protect policy/role changes with source review or administrative audit controls.

## 6. Residual risks

Authorized humans may approve harmful actions; provider APIs may lack conditional/idempotency primitives; compromised infrastructure credentials can bypass this service; hash chaining detects but cannot alone prevent deletion by a database superuser. Mitigations are least privilege, provider-native safeguards, separate audit retention, alerting, backups, and periodic access review.

## 7. Verification

Security tests include injection payloads, unknown fields, cross-tenant UUIDs, revoked approvers, self-approval, role changes before execution, race tests, forged/replayed tokens in a test identity setup, secret-redaction fixtures, audit-chain mutation detection, and ambiguous provider-response recovery.

