"""Stable error envelope shared by every API surface.

Every failure uses ``{error: {code, message, retryable, correlation_id, details}}``.
Unhandled exceptions never leak internals.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_405_METHOD_NOT_ALLOWED,
    HTTP_409_CONFLICT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from hitl_ops.agent.schemas import ParameterValidationError, UnknownToolError
from hitl_ops.domain.errors import DomainError

_STATUS_CODES: dict[int, str] = {
    HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    HTTP_403_FORBIDDEN: "FORBIDDEN",
    HTTP_404_NOT_FOUND: "NOT_FOUND",
    HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    HTTP_409_CONFLICT: "STATE_CONFLICT",
    HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_ERROR",
}


class ControlledError(Exception):
    """Domain-raised failure mapped onto the stable envelope."""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "Request rejected."
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}


def error_payload(
    code: str,
    message: str,
    retryable: bool,
    correlation_id: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "correlation_id": correlation_id,
            "details": details or {},
        }
    }


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", None) or uuid.uuid4().hex


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ControlledError)
    async def _controlled(request: Request, exc: ControlledError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                exc.code, exc.message, exc.retryable, _correlation_id(request), exc.details
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = {
            "errors": [
                {
                    "loc": [str(part) for part in error.get("loc", [])],
                    "type": str(error.get("type", "")),
                }
                for error in exc.errors()
            ]
        }
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "VALIDATION_FAILED",
                "Request failed validation.",
                False,
                _correlation_id(request),
                details,
            ),
        )

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_payload(exc.code, exc.message, exc.retryable, _correlation_id(request)),
        )

    @app.exception_handler(UnknownToolError)
    async def _unknown_tool(request: Request, exc: UnknownToolError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "UNKNOWN_TOOL", "tool is not on the allow-list", False, _correlation_id(request)
            ),
        )

    @app.exception_handler(ParameterValidationError)
    async def _parameter_validation(
        request: Request, exc: ParameterValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "VALIDATION_FAILED",
                "proposal parameters failed validation",
                False,
                _correlation_id(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "REQUEST_FAILED")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, str(exc.detail), False, _correlation_id(request)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Mutations carry idempotency keys, so a retry after an unexpected
        # failure is replay-safe.
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "INTERNAL_ERROR", "Unexpected server error.", True, _correlation_id(request)
            ),
        )
