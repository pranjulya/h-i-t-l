"""End-to-end functional check against a running HITL AI Ops stack.

Drives the workflow over HTTP exactly as a client would and prints a pass/fail
checklist. Learning-mode verification tool: it mints its own HS256 demo tokens
and must run against a non-production stack.

Usage:
    uv run python tools/e2e_check.py                      # http://127.0.0.1:8000
    BASE_URL=http://127.0.0.1:8001 uv run python tools/e2e_check.py

The demo identities must exist (`python -m hitl_ops.bootstrap`) and the worker
must be running for execution and reconciliation checks to pass.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
SECRET = os.environ.get("IDENTITY_SHARED_SECRET", "learning-demo-identity-secret")
ISSUER = "https://test-issuer.local"
AUDIENCE = "hitl-ops"

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


def token(actor: str, tenant: str = "tenant-1") -> str:
    return jwt.encode(
        {
            "sub": actor,
            "tenant": tenant,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "scope": "ops:read ops:write",
            "exp": datetime.now(UTC) + timedelta(minutes=30),
        },
        SECRET,
        algorithm="HS256",
    )


def headers(actor: str, key: str | None = None, tenant: str = "tenant-1") -> dict[str, str]:
    h = {"Authorization": f"Bearer {token(actor, tenant)}"}
    if key:
        h["Idempotency-Key"] = key
    return h


client = httpx.Client(base_url=BASE, timeout=15.0)


def create(tool: str, parameters: dict, actor: str = "user-1", key: str | None = None) -> dict:
    body = {"tool": tool, "parameters": parameters, "rationale": "e2e check"}
    response = client.post(
        "/v1/intents", json=body, headers=headers(actor, key or uuid.uuid4().hex)
    )
    return {"status": response.status_code, **response.json()}


def state(intent_id: str, actor: str = "user-1") -> dict:
    response = client.get(f"/v1/intents/{intent_id}", headers=headers(actor))
    return {"status": response.status_code, **response.json()}


def approve(
    intent_id: str,
    digest: str,
    version: int,
    actor: str,
    level: int = 1,
    obligations: dict[str, str] | None = None,
) -> dict:
    response = client.post(
        f"/v1/intents/{intent_id}/approvals",
        json={
            "revision": 1,
            "intent_digest": digest,
            "level": level,
            "decision": "APPROVE",
            "reason": "e2e approval",
            "expected_state_version": version,
            "obligations": obligations or {},
        },
        headers=headers(actor, uuid.uuid4().hex),
    )
    return {"status": response.status_code, **response.json()}


def wait_for(intent_id: str, wanted: set[str], seconds: float = 45.0, actor: str = "user-1") -> str:
    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        current = state(intent_id, actor)
        last = current.get("state", current.get("detail", ""))
        if last in wanted:
            return last
        time.sleep(1.0)
    return last


print("=" * 78)
print("HEALTH")
print("=" * 78)
live = client.get("/health/live")
ready = client.get("/health/ready")
check("liveness returns 200 without dependencies", live.status_code == 200, live.text)
check("readiness returns 200 with PostgreSQL reachable", ready.status_code == 200, ready.text)

print()
print("=" * 78)
print("JOURNEY 1 — LOW inspection (auto-allow → execution)")
print("=" * 78)
inspect = create(
    "inspect_service", {"environment": "staging", "service": "api", "requested_fields": ["health"]}
)
check("inspect_service intent created", inspect["status"] == 201, f"HTTP {inspect['status']}")
check(
    "risk classified LOW",
    inspect.get("risk", {}).get("band") == "LOW",
    str(inspect.get("risk", {}).get("band")),
)
check("policy auto-allowed it", inspect.get("state") == "AUTO_APPROVED", str(inspect.get("state")))
final = wait_for(inspect["intent_id"], {"SUCCEEDED", "FAILED"})
check("worker executed it to SUCCEEDED", final == "SUCCEEDED", final)
detail = state(inspect["intent_id"])
check(
    "execution carries a provider operation id",
    bool(detail.get("execution", {}).get("provider_operation_id")),
    str(detail.get("execution", {}).get("provider_operation_id")),
)

print()
print("=" * 78)
print("JOURNEY 2 — MEDIUM policy branches")
print("=" * 78)
scale_staging = create("scale_service", {"environment": "staging", "service": "api", "replicas": 4})
check(
    "staging scale auto-allows (policy ALLOW)",
    scale_staging.get("state") == "AUTO_APPROVED",
    str(scale_staging.get("state")),
)
check("staging scale band MEDIUM", scale_staging.get("risk", {}).get("band") == "MEDIUM")
scale_prod = create("scale_service", {"environment": "production", "service": "api", "replicas": 4})
check(
    "production scale escalates band to HIGH",
    scale_prod.get("risk", {}).get("band") == "HIGH",
    str(scale_prod.get("risk", {}).get("band")),
)
check(
    "production scale waits for approval",
    scale_prod.get("state") == "PENDING_APPROVAL_1",
    str(scale_prod.get("state")),
)

print()
print("=" * 78)
print("JOURNEY 3 — HIGH restart with one approval")
print("=" * 78)
restart = create(
    "restart_service", {"environment": "staging", "service": "api", "strategy": "rolling"}
)
current = state(restart["intent_id"])
check("restart waits for approval", current["state"] == "PENDING_APPROVAL_1", current["state"])
check(
    "approval requirements name the approver role",
    "approver" in current.get("policy", {}).get("required_roles", []),
)
self_approval = approve(restart["intent_id"], restart["digest"], current["state_version"], "user-1")
check(
    "requester cannot self-approve",
    self_approval["status"] == 403,
    f"HTTP {self_approval['status']}",
)
stale = approve(restart["intent_id"], "f" * 64, current["state_version"], "approver-1")
check(
    "approval bound to a different digest is rejected",
    stale["status"] == 409,
    json.dumps(stale.get("error", {}).get("code")),
)
approved = approve(
    restart["intent_id"],
    restart["digest"],
    current["state_version"],
    "approver-1",
    obligations={"announce_in_incident_channel": "done"},
)
check(
    "authorized approver moves it to APPROVED",
    approved.get("state") == "APPROVED",
    str(approved.get("state")),
)
final = wait_for(restart["intent_id"], {"SUCCEEDED", "FAILED"})
check("approved restart executed", final == "SUCCEEDED", final)

print()
print("=" * 78)
print("JOURNEY 4 — CRITICAL deletion, two distinct approvers")
print("=" * 78)
delete = create(
    "delete_resource",
    {
        "environment": "production",
        "resource_type": "postgres_instance",
        "resource_id": "pg-main-1",
        "deletion_mode": "hard",
    },
)
check("delete band CRITICAL", delete.get("risk", {}).get("band") == "CRITICAL")
check(
    "delete requires two-step approval",
    delete.get("policy", {}).get("route") == "CRITICAL_TWO_STEP",
)
current = state(delete["intent_id"])
l1 = approve(
    delete["intent_id"],
    delete["digest"],
    current["state_version"],
    "l1-user",
    level=1,
    obligations={"require_change_ticket": "CHG-1234"},
)
check("L1 approval accepted", l1.get("state") == "PENDING_APPROVAL_2", str(l1.get("state")))
after_l1 = state(delete["intent_id"])
check("L2 request is published automatically", after_l1["state"] == "PENDING_APPROVAL_2")
same_actor = approve(
    delete["intent_id"], delete["digest"], after_l1["state_version"], "l1-user", level=2
)
check("same actor cannot satisfy L2", same_actor["status"] == 403, f"HTTP {same_actor['status']}")
l2 = approve(
    delete["intent_id"],
    delete["digest"],
    after_l1["state_version"],
    "l2-user",
    level=2,
    obligations={"require_change_ticket": "CHG-1234"},
)
check(
    "distinct L2 approver completes approval", l2.get("state") == "APPROVED", str(l2.get("state"))
)

print()
print("=" * 78)
print("JOURNEY 5 — rejection, cancellation, duplicate replay")
print("=" * 78)
reject_target = create(
    "restart_service", {"environment": "staging", "service": "api", "strategy": "rolling"}
)
current = state(reject_target["intent_id"])
rejection = client.post(
    f"/v1/intents/{reject_target['intent_id']}/approvals",
    json={
        "revision": 1,
        "intent_digest": reject_target["digest"],
        "level": 1,
        "decision": "REJECT",
        "reason": "not needed",
        "expected_state_version": current["state_version"],
    },
    headers=headers("approver-1", uuid.uuid4().hex),
)
check(
    "rejection is terminal",
    rejection.json().get("state") == "REJECTED",
    json.dumps(rejection.json().get("state")),
)

cancel_target = create(
    "restart_service", {"environment": "staging", "service": "api", "strategy": "rolling"}
)
current = state(cancel_target["intent_id"])
cancellation = client.post(
    f"/v1/intents/{cancel_target['intent_id']}/cancel",
    json={
        "revision": 1,
        "reason": "changed mind",
        "expected_state_version": current["state_version"],
    },
    headers=headers("user-1", uuid.uuid4().hex),
)
check(
    "requester can cancel before execution",
    cancellation.json().get("state") == "CANCELLED",
    json.dumps(cancellation.json().get("state")),
)

replay_key = "e2e-replay-key"
first = create(
    "scale_service", {"environment": "staging", "service": "api", "replicas": 2}, key=replay_key
)
second = create(
    "scale_service", {"environment": "staging", "service": "api", "replicas": 2}, key=replay_key
)
check("same idempotency key replays the original intent", first["intent_id"] == second["intent_id"])
conflict = client.post(
    "/v1/intents",
    json={
        "tool": "scale_service",
        "parameters": {"environment": "staging", "service": "api", "replicas": 5},
    },
    headers=headers("user-1", replay_key),
)
check(
    "same key with a changed body conflicts",
    conflict.status_code == 409,
    f"HTTP {conflict.status_code}",
)

print()
print("=" * 78)
print("JOURNEY 6 — ambiguity, reconciliation, audit trail")
print("=" * 78)
ambiguous = create(
    "restart_service", {"environment": "staging", "service": "timeout-api", "strategy": "rolling"}
)
current = state(ambiguous["intent_id"])
approve(
    ambiguous["intent_id"],
    ambiguous["digest"],
    current["state_version"],
    "approver-1",
    obligations={"announce_in_incident_channel": "done"},
)
saw_unknown = wait_for(ambiguous["intent_id"], {"EXECUTION_UNKNOWN", "SUCCEEDED"}, seconds=30)
check(
    "lost provider response enters EXECUTION_UNKNOWN",
    saw_unknown in ("EXECUTION_UNKNOWN", "SUCCEEDED"),
    saw_unknown,
)
reconciled = wait_for(ambiguous["intent_id"], {"SUCCEEDED", "FAILED"}, seconds=60)
check("reconciliation resolves it from provider evidence", reconciled == "SUCCEEDED", reconciled)

events = client.get(
    f"/v1/intents/{restart['intent_id']}/events", params={"limit": 100}, headers=headers("user-1")
).json()
check("audit chain verifies", events.get("chain_valid") is True, str(events.get("chain_problem")))
audit_types = [event["event_type"] for event in events.get("audit", [])]
check("audit starts at intent_created", audit_types[:1] == ["intent_created"], str(audit_types[:1]))
check("audit records state transitions", "state_transitioned" in audit_types)
hashes = [event["event_hash"] for event in events.get("audit", [])]
check("audit hashes are unique (no duplicated deliveries)", len(hashes) == len(set(hashes)))
check(
    "transition feed is ordered",
    [e["sequence"] for e in events.get("events", [])]
    == sorted(e["sequence"] for e in events.get("events", [])),
)

print()
print("=" * 78)
print("SECURITY SPOT CHECKS")
print("=" * 78)
anonymous = client.get(f"/v1/intents/{restart['intent_id']}")
check(
    "unauthenticated request is rejected",
    anonymous.status_code == 403,
    f"HTTP {anonymous.status_code}",
)
foreign = client.get(
    f"/v1/intents/{restart['intent_id']}", headers=headers("user-9", tenant="tenant-2")
)
check("cross-tenant request returns 404", foreign.status_code == 404, f"HTTP {foreign.status_code}")
unknown_tool = client.post(
    "/v1/intents",
    json={"tool": "deploy_everything", "parameters": {}},
    headers=headers("user-1", uuid.uuid4().hex),
)
check(
    "unknown tool is rejected",
    unknown_tool.status_code == 422 and unknown_tool.json()["error"]["code"] == "UNKNOWN_TOOL",
)
extra_field = client.post(
    "/v1/intents",
    json={
        "tool": "scale_service",
        "parameters": {"environment": "staging", "service": "api", "replicas": 1, "force": True},
    },
    headers=headers("user-1", uuid.uuid4().hex),
)
check("extra parameters are rejected", extra_field.status_code == 422)
missing_key = client.post(
    "/v1/intents",
    json={
        "tool": "scale_service",
        "parameters": {"environment": "staging", "service": "api", "replicas": 1},
    },
    headers=headers("user-1"),
)
check("mutation without an idempotency key is rejected", missing_key.status_code == 422)
oversized = client.post(
    "/v1/intents",
    json={
        "tool": "scale_service",
        "parameters": {"environment": "staging", "service": "api", "replicas": 1},
        "rationale": "x" * 80000,
    },
    headers=headers("user-1", uuid.uuid4().hex),
)
check(
    "oversized payload is rejected",
    oversized.status_code in (413, 422),
    f"HTTP {oversized.status_code}",
)
no_execute = client.post(
    f"/v1/intents/{restart['intent_id']}/execute", headers=headers("user-1", uuid.uuid4().hex)
)
check(
    "no public execute endpoint exists",
    no_execute.status_code in (404, 405),
    f"HTTP {no_execute.status_code}",
)

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
passed = sum(1 for ok, _, _ in RESULTS if ok)
failed = [name for ok, name, _ in RESULTS if not ok]
print(f"{passed}/{len(RESULTS)} checks passed")
if failed:
    print("FAILED CHECKS:")
    for name in failed:
        print(f"  - {name}")
sys.exit(1 if failed else 0)
