# Learning package

This folder turns the implementation roadmap into a guided systems-design course. Read `learning-path.md`, study the concept notes before the matching phase, run the scenario thought experiments, then answer the interview questions without looking at the answers.

## Coverage map

| Major concept | Primary reading | Applied phase |
|---|---|---:|
| Safety problem, trust boundaries, and engineering baseline | `learning-path.md` Stage 1, `threat-model.md` | 00 |
| Intent, canonicalization, immutable revisions | `concepts/01-intent-integrity.md` | 01 |
| Deterministic risk and policy | `concepts/02-risk-policy.md` | 02 |
| State machines, identity, human authority | `concepts/03-approval-state-machine.md` | 03 |
| TOCTOU, concurrency, idempotency | `concepts/04-revalidation-concurrency.md` | 04 |
| Typed adapters, retry, unknown outcomes | `concepts/05-execution-recovery.md` | 05 |
| API/agent boundaries and untrusted proposals | `concepts/06-audit-security-observability.md`, Stage 6 | 06 |
| Audit, telemetry, threat boundaries | `concepts/06-audit-security-observability.md` | 07–08 |
| Complete flows and failure reasoning | `scenarios/` | 01–09 |
| Synthesis and defense of deliberate omissions | `learning-path.md` Stage 7, `interview/questions-and-answers.md` | 09 |
| Interview preparation | `interview/questions-and-answers.md` | Final review |

Learning is complete when the learner can explain the full flow, predict state under each scenario, identify the authoritative data source, distinguish failure certainty from retryability, and defend the deliberate V1 omissions.

