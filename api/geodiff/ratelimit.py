"""Small in-process sliding-window rate limiter.

Adequate for a single free-tier instance. Keys are client IPs; nothing else is retained.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Request

from geodiff.config import get_settings
from geodiff.errors import ApiError


class RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                retry = max(1, int(self.window_s - (now - q[0])))
                raise ApiError(
                    429,
                    "rate_limited",
                    "Too many requests from this client.",
                    f"Wait about {retry} seconds and try again.",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory
                self._hits = {
                    k: v for k, v in self._hits.items() if v and now - v[-1] < self.window_s
                }


def client_key(request: Request) -> str:
    """Rate-limit key: the client IP from the configured trusted header, else the TCP peer."""
    header = get_settings().client_ip_header.strip().lower()
    if header and header != "x-forwarded-for":
        value = request.headers.get(header, "").strip()
        if value:
            return value[:64]
    return request.client.host if request.client else "unknown"


_s = get_settings()
writes = RateLimiter(_s.rate_limit_writes_per_min, 60.0)
workspace_creation = RateLimiter(_s.rate_limit_workspaces_per_hour, 3600.0)
