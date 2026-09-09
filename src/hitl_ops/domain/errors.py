"""Domain error types mapped onto the stable API envelope at the boundary."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for domain failures with a stable error code."""

    code = "DOMAIN_ERROR"
    message = "Domain failure."
    retryable = False
    http_status = 400

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable


class IllegalTransitionError(DomainError):
    code = "STATE_CONFLICT"
    message = "Transition is not legal for the current state."
    http_status = 409


class StateConflictError(DomainError):
    code = "STATE_CONFLICT"
    message = "Concurrent modification lost the race."
    retryable = False
    http_status = 409


class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    message = "Actor is not authorized for this action."
    http_status = 403


class ApprovalStaleError(DomainError):
    code = "APPROVAL_STALE"
    message = "Decision does not match the current intent revision."
    http_status = 409


class ApprovalExpiredError(DomainError):
    code = "APPROVAL_EXPIRED"
    message = "Approval window elapsed before the decision."
    http_status = 409


class IdempotencyConflictError(DomainError):
    code = "IDEMPOTENCY_CONFLICT"
    message = "Idempotency key was already used with a different request."
    http_status = 409


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    message = "Resource not found."
    http_status = 404
