"""Canonical intent construction, digest binding, and raw-proposal evidence.

The digest input is a versioned envelope serialized as UTF-8 JSON with sorted
keys and compact separators after NFC normalization. SHA-256 is an integrity
binding, not a signature.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from hitl_ops.agent.schemas import TOOL_PARAMETER_MODELS, UnknownToolError, _StrictModel
from hitl_ops.domain.enums import ToolName

DIGEST_VERSION = "1"
MAX_RAW_PROPOSAL_BYTES = 16384
_REDACTED = "[REDACTED]"
_SECRET_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)
_OVERFLOW_PREVIEW_BYTES = 2048


@dataclass(frozen=True, slots=True)
class CanonicalIntent:
    tool: ToolName
    tenant_id: str
    requester_id: str
    revision: int
    parameters: dict[str, Any]
    canonical_json: str
    digest: str


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _canonical_envelope(
    tool: ToolName, tenant_id: str, requester_id: str, revision: int, parameters: dict[str, Any]
) -> str:
    envelope = {
        "digest_version": DIGEST_VERSION,
        "tenant_id": tenant_id,
        "requester_id": requester_id,
        "tool": tool.value,
        "revision": revision,
        "parameters": parameters,
    }
    return json.dumps(
        _normalize(envelope), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def canonicalize(
    tool: ToolName,
    tenant_id: str,
    requester_id: str,
    revision: int,
    parameters: _StrictModel,
) -> CanonicalIntent:
    """Build the immutable canonical intent for an already-validated proposal."""

    if TOOL_PARAMETER_MODELS.get(tool.value) is not type(parameters):
        raise UnknownToolError("parameters model does not match the tool")
    if revision < 1:
        raise ValueError("revision must be >= 1")
    canonical_json = _canonical_envelope(
        tool, tenant_id, requester_id, revision, parameters.model_dump(mode="json")
    )
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return CanonicalIntent(
        tool=tool,
        tenant_id=tenant_id,
        requester_id=requester_id,
        revision=revision,
        parameters=parameters.model_dump(mode="json"),
        canonical_json=canonical_json,
        digest=digest,
    )


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(marker in normalized_key for marker in _SECRET_KEY_MARKERS):
                redacted[str(key)] = _REDACTED
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def sanitize_raw_proposal(payload: Any) -> dict[str, Any]:
    """Store untrusted proposal evidence redacted and size-bounded (FR-03/FR-14)."""

    sanitized = _redact(payload)
    serialized = json.dumps(sanitized, separators=(",", ":"), ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) <= MAX_RAW_PROPOSAL_BYTES:
        return sanitized if isinstance(sanitized, dict) else {"payload": sanitized}
    preview = serialized[:_OVERFLOW_PREVIEW_BYTES]
    return {
        "_truncated": True,
        "_original_bytes": len(serialized.encode("utf-8")),
        "_preview": preview,
    }
