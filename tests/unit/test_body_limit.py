"""Bounded body middleware: replay, disconnect, and rejection behaviour."""

from __future__ import annotations

from typing import Any

from starlette.types import Message

from hitl_ops.api.body_limit import BodySizeLimitMiddleware


class _Recorder:
    """Captures what the middleware sent, and whether it reached the app."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.reached_app = False

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int | None:
        start = next((m for m in self.sent if m["type"] == "http.response.start"), None)
        return int(start["status"]) if start is not None else None

    @property
    def headers(self) -> dict[bytes, bytes]:
        start = next((m for m in self.sent if m["type"] == "http.response.start"), None)
        return dict(start["headers"]) if start is not None else {}


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    return {"type": "http", "method": "POST", "path": "/v1/intents", "headers": headers or []}


def _receiver(messages: list[Message]) -> Any:
    pending = list(messages)

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    return receive


def _chunks(*parts: bytes) -> list[Message]:
    return [
        {
            "type": "http.request",
            "body": part,
            "more_body": index < len(parts) - 1,
        }
        for index, part in enumerate(parts)
    ]


async def test_a_body_exactly_at_the_limit_is_accepted() -> None:
    recorder = _Recorder()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        recorder.reached_app = True
        message = await receive()
        assert message["body"] == b"abcd"

    middleware = BodySizeLimitMiddleware(app, max_bytes=4)
    await middleware(_scope(), _receiver(_chunks(b"abcd")), recorder.send)

    assert recorder.reached_app is True
    assert recorder.sent == []


async def test_a_body_one_byte_over_the_limit_is_rejected_with_security_headers() -> None:
    recorder = _Recorder()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # pragma: no cover
        recorder.reached_app = True

    middleware = BodySizeLimitMiddleware(app, max_bytes=4)
    await middleware(_scope(), _receiver(_chunks(b"abcde")), recorder.send)

    assert recorder.reached_app is False
    assert recorder.status == 413
    assert recorder.headers[b"x-content-type-options"] == b"nosniff"
    assert recorder.headers[b"x-frame-options"] == b"DENY"
    assert recorder.headers[b"cache-control"] == b"no-store"


async def test_a_body_split_across_chunks_is_summed_for_the_limit() -> None:
    recorder = _Recorder()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # pragma: no cover
        recorder.reached_app = True

    middleware = BodySizeLimitMiddleware(app, max_bytes=4)
    await middleware(_scope(), _receiver(_chunks(b"ab", b"cd", b"e")), recorder.send)

    assert recorder.reached_app is False
    assert recorder.status == 413


async def test_a_request_with_no_body_passes_through() -> None:
    recorder = _Recorder()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        recorder.reached_app = True
        message = await receive()
        assert message["body"] == b""

    middleware = BodySizeLimitMiddleware(app, max_bytes=4)
    await middleware(_scope(), _receiver(_chunks(b"")), recorder.send)

    assert recorder.reached_app is True


async def test_a_client_disconnect_mid_body_is_not_reported_as_too_large() -> None:
    """A dropped connection must not become a bogus 413 or a truncated body."""

    recorder = _Recorder()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # pragma: no cover
        recorder.reached_app = True

    messages: list[Message] = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.disconnect"},
    ]
    middleware = BodySizeLimitMiddleware(app, max_bytes=1024)
    await middleware(_scope(), _receiver(messages), recorder.send)

    assert recorder.reached_app is False
    assert recorder.sent == []


async def test_a_later_disconnect_is_forwarded_to_the_application() -> None:
    """After the buffered body is spent, the original channel is handed back."""

    seen: list[str] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        first = await receive()
        assert first["body"] == b"abcd"
        second = await receive()
        seen.append(second["type"])

    messages: list[Message] = [
        {"type": "http.request", "body": b"abcd", "more_body": False},
        {"type": "http.disconnect"},
    ]
    middleware = BodySizeLimitMiddleware(app, max_bytes=4)
    await middleware(_scope(), _receiver(messages), _Recorder().send)

    assert seen == ["http.disconnect"]


async def test_the_declared_length_is_trusted_when_it_already_exceeds_the_limit() -> None:
    recorder = _Recorder()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # pragma: no cover
        recorder.reached_app = True

    middleware = BodySizeLimitMiddleware(app, max_bytes=4)
    scope = _scope([(b"content-length", b"100")])
    await middleware(scope, _receiver(_chunks(b"x" * 100)), recorder.send)

    assert recorder.status == 413
    assert recorder.reached_app is False
