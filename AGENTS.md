# Agent instructions

1. Read `Implementation.md` and the current phase file before changing anything.
2. Do not write application code until the user approves the planning package.
3. Keep business rules in plain Python domain/application services; never place them in prompts, routers, LangGraph nodes, or infrastructure adapters.
4. PostgreSQL is authoritative for intents, approvals, executions, and audit events. Redis may not be a source of truth.
5. The LLM may propose only allow-listed tool intents and must never hold credentials or call infrastructure SDKs.
6. Every state change uses a database transaction, optimistic version check, and audit outbox event.
7. Every material intent change creates a new intent revision and invalidates prior approval; never mutate an approved revision.
8. Implement one phase at a time with the tests and review gate named in that phase.
9. Update phase status only after its Definition of Done is satisfied.
10. New scope requires a PRD and architecture review update before implementation.

