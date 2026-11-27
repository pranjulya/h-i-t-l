"""Shared request-body field types."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlankStr = Annotated[str, AfterValidator(_reject_blank)]
