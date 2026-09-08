# Phase 08 — Security Hardening and Failure Recovery

**Status:** TESTED — implemented with security (injection, tenancy, tokens, roles, redaction, dynamic-execution denial) and failure (database, LLM, adapter, audit sink, worker crash, Redis loss) suites plus operations runbooks and residual-risk evidence; `REVIEWED`/`COMPLETE` follow the user end-to-end review gate.

## Goal

Validate threat-model controls, harden trust boundaries, inject dependency/process failures, and create recovery runbooks for stale, unknown, backlog, credential, and database incidents.

## Why

Happy-path tests cannot show that the system remains fail-closed under attack, races, partial outages, or operator mistakes.

## Prerequisites

Phase 07 `COMPLETE`; threat model reviewed; test environment permits safe failure injection.

## Concepts

Defense in depth, least privilege, SSRF/injection prevention, tenant isolation, secret lifecycle, abuse limits, failure injection, recovery objectives, reconciliation and forensic preservation.

## Files to create or modify

- Harden auth/config/adapter/database permissions and request/rate/size limits.
- Create `tests/security/` cases for injection, tenancy, tokens, roles, redaction, dynamic execution denial.
- Create `tests/failure/` cases for database, Redis, LLM, adapter, audit sink, and worker crash points.
- Create `docs/operations/runbooks.md` and update threat model residual-risk evidence.

## Architecture impact

No new business features. Strengthens existing boundaries and proves degraded-mode semantics. Redis remains optional; tests verify its outage cannot weaken correctness.

## Data flow

Malicious or degraded input/dependency → boundary validation/timeout/circuit behavior → stable fail-closed result or recoverable pending/unknown state → audit/alert → documented operator action → verified recovery.

## Edge and failure scenarios

Prompt injection, SSRF-like target, oversized input/result, forged/expired JWT, cross-tenant enumeration, revoked approver, credential exposure, database disconnect mid-transaction, Redis loss, model outage, adapter ambiguous timeout, audit backlog, worker death at each execution boundary.

## Tests

Automate each threat control and outage; run race suite repeatedly; verify no secret in captured telemetry; prove no mutation during DB/auth uncertainty; prove Redis loss falls back safely; execute unknown-outcome and audit-backlog runbooks in the demo environment.

## Acceptance criteria

All high threats have tested controls; remaining risk is documented; services use least-privilege identities; timeouts/limits are explicit; failure states are observable and recoverable; runbooks name trigger, diagnosis, containment, recovery, and evidence preservation.

## Learning outcomes

Explain trust boundaries, fail closed/open trade-offs, SSRF prevention through typed targets, chaos/failure testing, credential rotation, and why recovery procedures are part of architecture.

## Interview questions

1. Which dependency failures permit reads but not mutations?
2. How does typed adapter input prevent SSRF and command injection?
3. What evidence must be preserved during an unknown execution incident?

## Definition of Done

Security/failure suites and runbook drills pass; critical/high scanner findings are resolved or explicitly accepted by the user; threat model and architecture review contain evidence; residual risks are accurate; learning material is complete; phase is `COMPLETE`.

