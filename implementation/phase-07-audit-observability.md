# Phase 07 — Audit Trail, Notification Delivery, and Observability

**Status:** NOT_STARTED

## Goal

Complete same-transaction outbox events, append-only hash-linked audit publication, the sanitized audit API, outbox-driven approval notification delivery to a pluggable sink, structured logs, metrics, traces, and actionable alerts.

## Why

A safety control that cannot prove what happened or alert on degraded guarantees is not production-operable, and an approval request that never reaches its approver stalls the entire workflow.

## Prerequisites

Phase 06 `COMPLETE`; ADR-009 accepted; retention/access policy and local audit sink selected; notification sink transport selected (demo/webhook for V1).

## Concepts

Transactional outbox, at-least-once delivery, idempotent consumers, tamper evidence, sequence/hash chains, correlation/causation, telemetry cardinality, data minimization, SLO alerts, untrusted notification content.

## Files to create or modify

- Complete `infrastructure/outbox.py`, add audit publisher/repository; migrate append-only audit tables and extend the Phase 01 minimal outbox with delivery indexes/attempt metadata and permissions guidance.
- Add the notification sink adapter and outbox-driven notification publisher for approval requests; notification content is a server-rendered exact intent summary with a digest link (per the threat model's notification-deception control), built from trusted server data only.
- Complete `observability/logging.py`, create `observability/telemetry.py`.
- Extend the Phase 06 auditor event route to the hash-linked audit store view and complete outbox worker wiring.
- Create unit `test_audit_hashing.py`, `test_redaction.py`, `test_notification_publisher.py`; integration `test_transactional_outbox.py`, `test_audit_ordering.py`, `test_notification_delivery.py`; observability contract tests.

## Architecture impact

Makes every accepted command/transition reconstructable without coupling primary transactions to an external sink, and delivers notifications through the same recoverable outbox machinery. Telemetry crosses components using safe identifiers and bounded labels.

## Data flow

Domain transaction → outbox row → retrying publisher → append-only event with aggregate sequence/hash → auditor view; approval-request events additionally fan out to the notification sink with idempotent delivery. Trace context links API/model/database/worker/adapter; metrics aggregate outcomes without user/resource labels.

## Edge and failure scenarios

Sink unavailable, duplicate message, out-of-order delivery, publisher crash after send, event payload too large, secret-like data, hash/sequence mismatch, audit backlog, telemetry collector unavailable, high-cardinality input, notification sink outage (approvals stay pending while the outbox retries), notification content truncation.

## Tests

Rollback produces neither state nor event; committed state always has outbox; duplicate delivery is idempotent; ordered hash chain verifies and detects mutation/gap; redaction fixtures exclude secrets; sink/collector outage does not corrupt primary state; duplicate notification delivery is idempotent; notification outage leaves approvals pending while the outbox retries; alert thresholds exercise synthetic signals.

## Acceptance criteria

Every required event type exists; audits answer who/what/when/why/version/outcome; app role cannot update/delete audit rows; separate sink retains evidence; approval notifications render the exact intent summary/digest link from trusted server data and are retried durably; dashboards/alerts cover approval, stale, execution unknown, illegal transitions, auth denials, outbox lag, and notification backlog; no unbounded labels.

## Learning outcomes

Explain why logging is not auditing, why outbox delivery is at least once, what hash linking detects, how notification content stays trusted while its trigger is an untrusted proposal, and how telemetry can leak sensitive/high-cardinality data.

## Interview questions

1. Why not call the audit sink inside the state transaction?
2. What can a hash chain not protect against?
3. How does an idempotent consumer handle publisher crash-after-send?
4. Why deliver notifications from the outbox instead of the approval transaction?

## Definition of Done

Audit/outbox/redaction/telemetry/notification tests pass; permission and retention configuration is documented; dashboards and alerts are demonstrated with synthetic events; notification outage and retry behavior is demonstrated; architecture review audit/observability sections have evidence; learning content is complete; phase is `COMPLETE`.
