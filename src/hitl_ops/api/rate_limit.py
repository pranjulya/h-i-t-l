"""In-process sliding-window rate limiter.

Single-instance protection for the learning deployment; a multi-replica
deployment replaces this with a shared counter. Correctness never depends on
it: this is abuse mitigation, not authorization.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class SlidingWindowRateLimiter:
    limit_per_minute: int
    _hits: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def allow(self, identity: str, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        window_start = current - 60.0
        hits = self._hits[identity]
        while hits and hits[0] <= window_start:
            hits.popleft()
        if len(hits) >= self.limit_per_minute:
            return False
        hits.append(current)
        return True
