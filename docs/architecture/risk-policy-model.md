# Risk and Policy Model

**Status:** PROPOSED_FOR_REVIEW  
**Principle:** Risk describes potential harm; policy decides whether and how the exact intent may proceed.

## 1. Separation of concerns

- The **risk engine** is a pure deterministic function over a canonical intent and evaluated context. It returns a band, factors, rationale codes, and rule version.
- The **policy engine** consumes the intent, risk result, actor/tenant/environment context, and policy bundle. It returns `ALLOW`, `REQUIRE_APPROVAL`, or `BLOCK`, plus approval route, TTL, obligations, and policy version.
- The **state machine** enforces the result. Neither engine changes state or calls infrastructure.

## 2. Risk bands

| Band | Default treatment | Examples |
|---|---|---|
| LOW | Auto-allow if policy passes | Read-only inspection |
| MEDIUM | Policy-driven auto-allow, one approval, or block | Bounded non-production scale |
| HIGH | One authorized human approval | Restart; provisioning; production scale escalation |
| CRITICAL | Two-step distinct human approval | Resource deletion |

## 3. Deterministic factors

The engine evaluates allow-listed rules in priority order:

1. Tool minimum risk.
2. Environment (`production` escalates mutable MEDIUM operations to at least HIGH).
3. Blast radius (resource count, tenant scope, availability-zone/service criticality).
4. Magnitude (`scale_service` delta and resulting replica count).
5. Reversibility (irreversible deletion remains CRITICAL).
6. Cost class for provisioning/scaling.
7. Current service condition (degraded dependencies or active incident constraints).
8. Data sensitivity and compliance tags.

The highest resulting band wins. A result stores factor codes and sanitized facts; no free-form LLM rationale can alter the band.

## 4. V1 baseline rules

| Condition | Result |
|---|---|
| `inspect_service` and authorized target | LOW |
| `scale_service` in non-production, target 1–10 replicas, delta ≤ 3, normal cost | MEDIUM |
| `scale_service` outside those bounds or in production | at least HIGH |
| `restart_service` | at least HIGH |
| `provision_resource` | at least HIGH; CRITICAL if policy marks size/cost/data class critical |
| `delete_resource` | CRITICAL |
| Unknown tool/context, malformed target, or unsupported resource type | BLOCK via validation/policy, never guessed risk |

## 5. Policy decision schema

```text
PolicyDecision
  decision: ALLOW | REQUIRE_APPROVAL | BLOCK
  route: NONE | SINGLE | CRITICAL_TWO_STEP
  approval_ttl_seconds: 900 by default, maximum 3600
  required_roles: role names per level
  required_scopes: tenant/environment/resource patterns
  obligations: named preconditions such as maintenance_window or backup_verified
  reason_codes: stable machine-readable codes
  policy_version: immutable bundle identifier
  evaluated_at: server timestamp
```

Policies are deployment-owned versioned Python/data rules in V1, reviewed in source control. V1 does not build a policy DSL or administrative policy UI; registering/activating a reviewed bundle version is an audited Administrator command (LLD §6).

## 6. MEDIUM policy examples

- Non-production scale within 1–10 replicas, delta ≤ 3, no high-cost or protected tag: `ALLOW/NONE`.
- Production scale or protected service: risk escalates to HIGH, then `REQUIRE_APPROVAL/SINGLE`.
- Scale above configured tenant quota: `BLOCK`.
- Missing live inventory/context: `BLOCK` with retryable context-unavailable semantics; never auto-allow.

## 7. Authorization and RBAC

Authentication establishes principal identity. Authorization combines server-side assignments with intent scope:

- requester needs `intent:create` for tenant/environment/tool;
- L1/single approver needs `approval:level1` for the exact tenant/environment/resource class;
- L2 needs `approval:level2` for that scope;
- executor service needs `execution:claim` and adapter-specific least privilege;
- auditor needs `audit:read` within scope.

JWT roles are not enough alone: current database-backed assignments and revocations are checked. Approval snapshots support forensics but do not replace reauthorization before execution.

## 8. Re-evaluation semantics

Risk and policy are evaluated on creation and again during execution revalidation. If the current route is stricter, the revision becomes `STALE`. If unchanged, execution may proceed. If less strict, existing approvals remain acceptable but skipped approvals are never retroactively synthesized. A new immutable policy version never rewrites historic decisions.

## 9. Testing matrix

- Table-driven tests cover every tool, environment, threshold boundary, and escalation combination.
- Property tests assert result is never below tool minimum and added harmful factors never reduce risk.
- Policy tests cover allow/approval/block, TTL maximum, roles/scopes, missing context, and policy version changes.
- Authorization tests cover tenant isolation, revoked roles, self-approval, duplicate critical approvers, and scope mismatches.
- Mutation tests or explicit negative cases verify unknown fields/tools fail closed.

