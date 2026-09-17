"""Lightweight telemetry: bounded-label counters for operational signals.

V1 keeps metrics in-process with fixed label keys and caller-supplied bounded
values; an OTel exporter can read the same registry without behavior change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class _Metrics:
    def __init__(self) -> None:
        self._counters: dict[str, float] = {}

    def increment(self, name: str, **labels: Any) -> None:
        key = self._key(name, labels)
        self._counters[key] = self._counters.get(key, 0.0) + 1.0

    def add(self, name: str, value: float, **labels: Any) -> None:
        key = self._key(name, labels)
        self._counters[key] = self._counters.get(key, 0.0) + value

    @staticmethod
    def _key(name: str, labels: dict[str, Any]) -> str:
        if not labels:
            return name
        rendered = ",".join(
            f"{key}={labels[key]}" for key in sorted(labels) if labels[key] is not None
        )
        return f"{name}{{{rendered}}}"

    def snapshot(self) -> dict[str, float]:
        return dict(self._counters)

    def reset(self) -> None:
        self._counters.clear()


metrics = _Metrics()


@dataclass
class TickObservation:
    executed: int = 0
    reconciled: int = 0
    skipped: int = 0
    labels: dict[str, Any] = field(default_factory=dict)
