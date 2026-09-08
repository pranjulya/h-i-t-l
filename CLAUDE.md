# Project context

Project 05 is a learning-first Human-in-the-Loop AI Ops service. The application converts an agent proposal into a canonical immutable action intent, then applies deterministic risk, policy, approval, revalidation, execution, and audit controls.

Authoritative documents:

- Product contract: `docs/product/PRD.md`
- State rules: `docs/architecture/approval-state-machine.md`
- Risk and policy: `docs/architecture/risk-policy-model.md`
- Component design: `docs/architecture/HLD.md` and `docs/architecture/LLD.md`
- Execution order: `Implementation.md` and `implementation/`

Hard boundaries: no application code before user approval; no LLM-to-infrastructure path; no approval of mutable or vaguely described actions; no execution without current authorization, current policy, target-state revalidation, and an idempotency key.

