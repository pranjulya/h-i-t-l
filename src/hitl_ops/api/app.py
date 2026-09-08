"""FastAPI application factory.

HTTP and the LLM are adapters around the deterministic control plane. No
adapter invokes the execution path, and no route owns risk/policy/state rules.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from hitl_ops.agent.orchestrator import AgentOrchestrator, DisabledLLMProvider
from hitl_ops.api import admin, approvals, intents
from hitl_ops.api.errors import register_error_handlers
from hitl_ops.api.rate_limit import SlidingWindowRateLimiter
from hitl_ops.config import Settings
from hitl_ops.infrastructure.database import (
    build_engine,
    build_sessionmaker,
    check_connectivity,
    verify_migration_head,
)
from hitl_ops.observability.logging import configure_logging

_READINESS_TIMEOUT_SECONDS = 2.0


def _payload_too_large(correlation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "error": {
                "code": "PAYLOAD_TOO_LARGE",
                "message": "request body exceeds the configured limit",
                "retryable": False,
                "correlation_id": correlation_id,
                "details": {},
            }
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    configure_logging(resolved.log_level, resolved.app_env.value)

    engine = build_engine(resolved.database_url)
    sessionmaker = build_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        try:
            yield
        finally:
            await engine.dispose()

    rate_limiter = SlidingWindowRateLimiter(resolved.rate_limit_per_minute)
    app = FastAPI(title="HITL AI Ops", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.orchestrator = AgentOrchestrator(DisabledLLMProvider())
    register_error_handlers(app)

    app.include_router(intents.router)
    app.include_router(approvals.router)
    app.include_router(admin.router)

    @app.middleware("http")
    async def hardening_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        request.state.correlation_id = correlation_id

        content_length = request.headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > resolved.max_request_bytes
        ):
            return _payload_too_large(correlation_id)
        # Chunked bodies and requests without Content-Length cannot be trusted
        # on headers: stream with a hard cap and stop reading once the limit
        # is exceeded, so an oversized body cannot exhaust memory.
        if content_length is None or not content_length.isdigit():
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > resolved.max_request_bytes:
                    return _payload_too_large(correlation_id)
        else:
            body = await request.body()
            if len(body) > resolved.max_request_bytes:
                return _payload_too_large(correlation_id)

        if not rate_limiter.allow(request.client.host if request.client else "anonymous"):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "request rate exceeded; retry later",
                        "retryable": True,
                        "correlation_id": correlation_id,
                        "details": {},
                    }
                },
            )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def readiness(request: Request) -> Response:
        engine = request.app.state.engine
        try:
            async with asyncio.timeout(_READINESS_TIMEOUT_SECONDS):
                async with engine.connect() as connection:
                    await check_connectivity(connection)
                    await verify_migration_head(connection)
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ready"})

    return app
