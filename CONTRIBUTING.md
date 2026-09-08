# Contributing

`AGENTS.md` is the operating contract; read it before changing anything.

- Read `Implementation.md` and the current phase file first. Implement one phase at a time, in numeric order.
- Do not write application code until the user approves the planning package.
- Keep business rules in plain Python domain/application services — never in prompts, routers, LangGraph nodes, or infrastructure adapters.
- PostgreSQL is authoritative for intents, approvals, executions, and audit events. Redis may never be a source of truth.
- Every state change commits inside a database transaction with an optimistic version check and an audit outbox row.
- A material intent change creates a new revision and invalidates prior approval; never mutate an approved revision.
- Every phase ships the tests and review gate named in its file before status advances, and updates its paired learning content.
- New scope requires a PRD change and an architecture-review update before implementation.

Docs-only fixes (typos, contradictions, coverage gaps) are welcome at any time. When documents disagree, resolve per the precedence order in `Implementation.md`: PRD product rules → approval state machine → risk/policy model → accepted ADRs → LLD → phase documents.
