"""Core domain enums. The five V1 tools and all lifecycle states are locked."""

from __future__ import annotations

import enum


class ToolName(enum.StrEnum):
    INSPECT_SERVICE = "inspect_service"
    RESTART_SERVICE = "restart_service"
    SCALE_SERVICE = "scale_service"
    PROVISION_RESOURCE = "provision_resource"
    DELETE_RESOURCE = "delete_resource"


class RiskBand(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PolicyDisposition(enum.StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


class ApprovalRoute(enum.StrEnum):
    NONE = "NONE"
    SINGLE = "SINGLE"
    CRITICAL_TWO_STEP = "CRITICAL_TWO_STEP"


class ApprovalDecision(enum.StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class IntentState(enum.StrEnum):
    REQUESTED = "REQUESTED"
    RISK_EVALUATED = "RISK_EVALUATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    AUTO_APPROVED = "AUTO_APPROVED"
    PENDING_APPROVAL_1 = "PENDING_APPROVAL_1"
    APPROVED_BY_LEVEL_1 = "APPROVED_BY_LEVEL_1"
    PENDING_APPROVAL_2 = "PENDING_APPROVAL_2"
    APPROVED = "APPROVED"
    REVALIDATING = "REVALIDATING"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"


class ExecutionOutcome(enum.StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class IntentSource(enum.StrEnum):
    AGENT = "AGENT"
    DIRECT = "DIRECT"
