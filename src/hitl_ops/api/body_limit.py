"""Bounded request-body middleware.

Chunked requests and requests without a numeric ``Content-Length`` cannot be
trusted on headers, so the body must be read under a hard cap. Reading it
consumes the ASGI receive channel, which would leave the route with an
exhausted body and turn valid chunked requests into validation errors. This
middleware therefore buffers the body within the cap and replays it to the
application, so downstream code sees exactly the bytes that arrived.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CORRELATION_HEADER = b"x-correlation-id"


class BodySizeLimitMiddleware:
    """Reject oversized bodies and make the accepted body readable again."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id = headers.get("X-Correlation-ID") or uuid.uuid4().hex
        scope.setdefault("state", {}).setdefault("correlation_id", correlation_id)

        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            await self._reject(send, correlation_id)
            return

        buffered, disconnected = await self._read_within_limit(receive)
        if disconnected:
            # The client went away mid-body: there is nobody to answer, and
            # replaying a truncated body as if it were complete would turn a
            # dropped connection into a confusing validation error.
            return
        if buffered is None:
            await self._reject(send, correlation_id)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                # Hand the original channel back once the buffered body is
                # spent, so a later receive() observes a real http.disconnect
                # rather than being told the body is simply empty again.
                return await receive()
            replayed = True
            return {"type": "http.request", "body": buffered, "more_body": False}

        await self._app(scope, replay, send)

    async def _read_within_limit(self, receive: Receive) -> tuple[bytes | None, bool]:
        """Buffer the body.

        Returns ``(body, disconnected)``: ``body`` is ``None`` when the cap is
        exceeded, and ``disconnected`` is true when the client dropped the
        connection before the body ended.
        """

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None, True
            body.extend(message.get("body", b""))
            if len(body) > self._max_bytes:
                return None, False
            if not message.get("more_body", False):
                return bytes(body), False

    async def _reject(self, send: Send, correlation_id: str) -> None:
        payload = json.dumps(
            {
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": "request body exceeds the configured limit",
                    "retryable": False,
                    "correlation_id": correlation_id,
                    "details": {},
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": _response_headers(payload, correlation_id),
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _response_headers(payload: bytes, correlation_id: str) -> list[tuple[bytes, bytes]]:
    headers: Mapping[str, str] = {
        "content-type": "application/json",
        "content-length": str(len(payload)),
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
    }
    raw: list[tuple[bytes, bytes]] = [
        (key.encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()
    ]
    raw.append((_CORRELATION_HEADER, correlation_id.encode("latin-1")))
    return raw
