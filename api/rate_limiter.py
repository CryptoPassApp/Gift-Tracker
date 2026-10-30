"""In-memory sliding-window rate limiter for public / demo API endpoints."""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request, status


class InMemoryRateLimiter:
    """Sliding-window rate limiter by client IP address."""

    def __init__(self, max_requests: int = 60, window_sec: int = 60):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, request: Request) -> None:
        client_host = request.client.host if request.client else "unknown"
        # Bypassed for internal unit test client
        if client_host == "testclient":
            return

        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else client_host

        now = time.monotonic()
        cutoff = now - self.window_sec

        with self._lock:
            timestamps = self._requests[ip]
            valid = [t for t in timestamps if t > cutoff]
            if len(valid) >= self.max_requests:
                self._requests[ip] = valid
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many demo requests. Please slow down.",
                    headers={"Retry-After": str(self.window_sec)},
                )
            valid.append(now)
            self._requests[ip] = valid


__all__ = ["InMemoryRateLimiter"]
