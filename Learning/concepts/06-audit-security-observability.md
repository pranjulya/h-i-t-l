# Concept 06 — Audit, Security, and Observability

Logs help debug, metrics reveal trends, traces connect latency across components, and audit events prove security-relevant decisions. They overlap but are not substitutes.

The transactional outbox solves a dual-write problem: state change and intent to publish audit commit together. A worker may deliver more than once, so consumers use event IDs and aggregate sequence for idempotency/order. Hash linking makes edits or gaps detectable; a separate retained sink and provider audit IDs make evidence harder to erase. A database superuser can still destroy local data, so hashes alone are not immutability.

Security begins with trust boundaries: client, model, target metadata, and external results are untrusted. Strict schemas, typed adapters, current scoped RBAC, tenant-scoped queries, network isolation, short-lived credentials, data minimization, and size/rate limits form defense in depth.

Telemetry uses correlation and trace IDs plus low-cardinality tool/state/risk/outcome fields. Tenant, user, intent, and resource identifiers belong in protected structured logs/traces as hashed or access-controlled fields, not metric labels. Redaction must be tested with secret-like fixtures.

Self-check: reconstruct a critical deletion from audit events, then name what would alert you to sequence gaps, outbox backlog, repeated authorization failures, stale approvals, or rising unknown executions.

