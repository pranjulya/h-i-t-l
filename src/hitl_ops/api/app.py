"""FastAPI application factory.

Phase 00 contains no workflow behavior: only configuration, structured logs,
liveness/readiness, and lifespan-owned database plumbing.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from hitl_ops.api.errors import register_error_handlers
from hitl_ops.config import Settings
from hitl_ops.infrastructure.database import build_engine, check_connectivity, verify_migration_head
from hitl_ops.observability.logging import configure_logging

_READINESS_TIMEOUT_SECONDS = 2.0


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    configure_logging(resolved.log_level, resolved.app_env.value)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = build_engine(resolved.database_url)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="HITL AI Ops", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved
    register_error_handlers(app)

    @app.middleware("http")
    async def correlation_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
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
