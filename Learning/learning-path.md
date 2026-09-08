# Learning Path

## Stage 1 — Establish the safety problem

Read the PRD and threat model. Draw the trust boundaries from memory. Be able to explain why “the model called a tool” is not an authorization decision and why the direct structured-intent API remains valuable.

Evidence exercise: take each V1 tool and list its material parameters, minimum risk, possible escalation factors, and infrastructure privilege.

## Stage 2 — Build the immutable decision subject

Study `01-intent-integrity.md` with Phase 01. Manually canonicalize two equivalent JSON inputs and explain why their digest matches; then change tenant, requester, revision, or one parameter and explain why it must differ.

Checkpoint: distinguish integrity binding, authentication, authorization, and audit evidence.

## Stage 3 — Separate risk, policy, and workflow

Study `02-risk-policy.md` and `03-approval-state-machine.md` with Phases 02–03. Build a decision table for five tools across staging/production. Walk every legal state transition and name its guards. Trace both MEDIUM branches in `scenarios/05-medium-scale.md`.

Checkpoint: explain how LOW can be blocked, MEDIUM can be auto-allowed, and CRITICAL still needs two humans even if a model is confident.

## Stage 4 — Close the time and retry gaps

Study `04-revalidation-concurrency.md` and the stale scenario with Phase 04. Simulate two workers, a revocation, a policy update, and a changed live target. Identify which mechanism—lock, version, constraint, digest, or precondition—addresses each problem.

Checkpoint: explain why Redis locks are optional and why an approval alone is insufficient at execution time.

## Stage 5 — Cross the privileged boundary safely

Study `05-execution-recovery.md` and the ambiguous execution scenario with Phase 05. Draw timelines for failure before send, after send, after response, and before local commit. Decide which may retry and which must reconcile.

Checkpoint: explain why exactly-once execution is usually an end-to-end protocol, not a local database setting.

## Stage 6 — Make the system usable and operable

Read LLD API contracts, `06-audit-security-observability.md`, and Phases 06–08. Trace correlation IDs and audit events across one action. Design an alert for a growing `EXECUTION_UNKNOWN` count without using high-cardinality labels.

Checkpoint: distinguish logs, metrics, traces, and append-only audit evidence.

## Stage 7 — Demonstrate and defend the architecture

Run all written scenarios, then answer the interview set. Re-read the architecture review and justify every omission: no workflow DSL, no dynamic plugins, no Kubernetes, no mandatory Redis, and no LangGraph authority.

Final exercise: present the critical delete journey in five minutes, including stale approval and ambiguous provider recovery.

