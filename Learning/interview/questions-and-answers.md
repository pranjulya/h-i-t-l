# Interview Questions and Answers

## 1. Why can the LLM not call infrastructure directly?

Model output is probabilistic and exposed to untrusted prompt/context data. Removing credentials and network access makes deterministic validation, policy, approval, revalidation, typed adapters, and audit unavoidable rather than advisory.

## 2. What exactly does a human approve?

One immutable intent revision identified by tenant, requester, tool, canonical material parameters, digest, risk/policy evidence, route, and expiry—not a chat message or future class of actions.

## 3. Risk versus policy?

Risk classifies potential harm. Policy decides whether that harm is permitted, blocked, or requires obligations/approval for the current actor and context.

## 4. Why plain Python before LangGraph?

Business invariants need direct unit tests, explicit transactions, and framework independence. LangGraph may coordinate conversation later, but its checkpoints cannot replace authoritative domain/database state.

## 5. Why immutable revisions?

They prevent old approvals from appearing to authorize changed parameters and preserve an unambiguous forensic history.

## 6. What does the digest guarantee?

It detects changes to the versioned canonical intent envelope. It does not authenticate an approver or prove current safety.

## 7. Why revalidate after approval?

Roles, policy, TTL, target identity/version, health, and obligations can change between decision and execution. Revalidation closes that TOCTOU gap as much as provider capabilities allow.

## 8. Why both optimistic versioning and row locks?

Versioning rejects a stale client command; a row lock serializes the short server-side critical section. Database constraints remain the final invariant across all processes.

## 9. Does Redis provide distributed correctness?

Not here. It may reduce contention or accelerate short-lived coordination, but PostgreSQL state, transactions, versions, and constraints decide correctness so Redis loss is only a performance degradation.

## 10. How does request idempotency work?

A tenant/actor/route-scoped key maps to a request hash and stored response. Same key and hash replay; same key with different content conflicts.

## 11. Can the service guarantee exactly-once infrastructure mutation?

Only with end-to-end provider support such as idempotency keys, conditional operations, and status lookup. Locally it guarantees one logical execution claim; uncertain remote outcomes are reconciled.

## 12. Why is a timeout not always retryable?

After a mutation may have reached the provider, retry could duplicate the effect. Certainty of send/outcome and provider idempotency determine safety, not the word “timeout.”

## 13. What is `EXECUTION_UNKNOWN`?

A nonterminal evidence state indicating the provider may have acted but local confirmation is absent. Reconciliation observes provider evidence; it does not blindly repeat the action.

## 14. How are CRITICAL approvals separated?

Requester, L1, and L2 must be distinct authenticated principals. L1 commits before L2 becomes eligible, and both require current matching roles/scopes.

## 15. Why store role snapshots and recheck current roles?

Snapshots explain why a decision was accepted historically. Current checks prevent revoked or expired authority from remaining executable.

## 16. Why use a transactional outbox?

It atomically records primary state and the obligation to publish evidence, avoiding a lost audit event when an external sink call fails after a state commit.

## 17. Is a hash-linked audit table immutable?

It is tamper-evident under normal controls, not invulnerable to a database superuser who can erase everything. Append-only permissions, a separate retained sink, backups, and provider evidence strengthen it.

## 18. How is tenant isolation enforced?

Tenant comes from authenticated context; every query and uniqueness scope includes it; authorization checks resource scope; foreign identifiers return non-disclosing denial/not-found behavior; tests attempt cross-tenant access.

## 19. What prevents prompt injection from becoming an operation?

The model has no credentials; its output enters a strict allow-listed schema; unknown fields/tools/targets fail; deterministic risk/policy/approval/revalidation still apply; adapters accept only typed commands.

## 20. What would justify LangGraph later?

Measured need for complex conversational interruption/resumption or visualization that plain orchestration handles poorly, while all domain/state authority remains in existing services and PostgreSQL.

## 21. What would justify Redis later?

Measured PostgreSQL contention or latency from cross-process locks/idempotency lookup. Redis can accelerate, but database invariants remain authoritative.

## 22. Why is this reusable but not a workflow SaaS?

The intent/risk/policy/approval/execution/audit concepts are generic, while V1 tools, rules, transitions, and adapter are explicit code. There is no user-defined workflow language, arbitrary plugin runtime, or policy marketplace.

