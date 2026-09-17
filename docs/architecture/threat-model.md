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


## 9. Residual risk evidence (Phase 08)

Automated evidence for the controls above lives in `tests/security/` and
`tests/failure/`, with operator procedures in `docs/operations/runbooks.md`.

| Threat | Control evidence | Residual risk |
|---|---|---|
| Prompt injection / model overreach | strict schema validation rejects unknown tools/fields (`test_injection`, `test_agent_orchestrator`); invalid proposals create no intent | Model cannot grant authority; provider is disabled by default and replaced wholesale per deployment |
| Notification deception | notifications render trusted server data only; approvals accepted only in the authenticated API (`test_notification_publisher`, `test_notification_delivery`) | Receiver compromise is out of scope; notification is convenience, never authorization |
| Cross-tenant enumeration | identical `NOT_FOUND` bodies and codes for foreign and missing intents (`test_tenancy`) | Timing side channels not measured in V1 |
| Forged/expired identity | issuer/audience/signature/time claims enforced; failures fail closed (`test_tokens`) | Production JWKS rotation is an ADR-008 deployment task |
| Revoked or scoped authority | decisions and revalidation evaluate current DB assignments with validity windows and environment scopes (`test_tokens`, `test_policy_auth_refresh`) | Assignment administration is trusted-operator bound; changes are audited |
| Dynamic execution / SSRF | adapters accept typed commands only; no URL/host fields; credential-shaped payloads rejected pre-send (`test_dynamic_execution_denial`) | Real cloud adapters must keep the same rejection tests in their contract suites |
| Secret exposure | raw proposals and result summaries redacted and size-bounded before persistence (`test_redaction`); error envelopes sanitized | Log aggregation endpoints are deployment-owned |
| Audit rewrite | append-only at the application layer and by a database trigger; hash chain detects edits/gaps (`test_audit_ordering`, `test_transactional_outbox`) | An attacker with direct database superuser access is out of threat scope |
| Dependency loss (DB, LLM, adapter, sink) | fail-closed mutation semantics verified; outbox retries without corrupting state; unknown outcomes preserved (`test_database_outage`, `test_llm_outage`, runbooks RB-1/RB-3/RB-5) | Prolonged PostgreSQL loss halts the service by design; recovery objective equals database RTO |
| Worker death at execution boundaries | claim leases recover abandoned claims; at-most-one logical provider operation per revision (`test_worker_crash`, `test_concurrent_claim`) | Reconciliation depends on provider status-lookup cooperation (ADR-010) |

Accepted residual risks require explicit user sign-off during the end-to-end
review; unresolved high risks block release (PRD §12).
