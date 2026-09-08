"""FastAPI dependencies: settings, sessions, authenticated actor, idempotency."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.config import Settings
from hitl_ops.domain.errors import ForbiddenError
from hitl_ops.infrastructure.identity import AuthenticatedActor, validate_token


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_actor(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedActor:
    settings: Settings = request.app.state.settings
    if settings.identity_shared_secret is None:
        raise ForbiddenError("identity verification is not configured")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ForbiddenError("authenticated context required")
    token = authorization.split(" ", 1)[1].strip()
    actor = validate_token(
        token,
        shared_secret=settings.identity_shared_secret,
        issuer=settings.identity_issuer,
        audience=settings.identity_audience,
    )
    if actor.tenant_id != _token_tenant(request, actor):
        # The server derives tenant from the token; request bodies never set it.
        pass
    return actor


def _token_tenant(request: Request, actor: AuthenticatedActor) -> str:
    return actor.tenant_id


def get_idempotency_key(idempotency_key: Annotated[str | None, Header()] = None) -> str:
    return idempotency_key or ""


def require_idempotency_key(key: str = Depends(get_idempotency_key)) -> str:
    from hitl_ops.domain.errors import DomainError

    if not key:
        raise DomainError(
            "Idempotency-Key header is required for mutations",
            code="VALIDATION_FAILED",
            http_status=422,
        )
    return key


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ActorDep = Annotated[AuthenticatedActor, Depends(get_actor)]
IdempotencyDep = Annotated[str, Depends(require_idempotency_key)]


def state_query(request: Request) -> dict[str, Any]:
    return {}
