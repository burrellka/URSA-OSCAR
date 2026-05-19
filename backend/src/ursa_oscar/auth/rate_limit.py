"""Brute-force rate limiting for /auth/login — Phase 6.4 Decision 9.

Tracks failed login attempts per source IP in an in-memory dict:

    {ip: (failure_count, window_start_time)}

After 5 failures within 15 minutes from a single IP, ``/auth/login``
returns ``429 Too Many Requests`` with a ``Retry-After`` header until
the window expires.

In-memory only — single-user single-process system; no need for
Redis or a DB table. Lost on restart, which is acceptable: a real
attacker who waits for the restart still hits 5 failures again.

Failed logins are logged at WARN level with source IP for operator
visibility via ``docker logs``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock

logger = logging.getLogger(__name__)


# Defaults per work-order Decision 9.
MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60  # 15 minutes


@dataclass
class _Bucket:
    failures: int
    window_started_at: float  # monotonic seconds


class LoginRateLimiter:
    """Thread-safe per-IP failed-login counter.

    Usage from the login route:

        ok, retry_after = limiter.check(client_ip)
        if not ok:
            raise HTTPException(429, ...)
        # ... verify password ...
        if not password_ok:
            limiter.record_failure(client_ip)
            raise HTTPException(401, ...)
        limiter.reset(client_ip)  # on success
    """

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window_seconds: int = WINDOW_SECONDS,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def check(self, ip: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``. ``allowed=False``
        means the IP is currently locked out. ``retry_after_seconds``
        is meaningful only when ``allowed=False``."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None:
                return True, 0
            elapsed = now - bucket.window_started_at
            if elapsed >= self._window:
                # Window expired; the counter resets on next failure.
                self._buckets.pop(ip, None)
                return True, 0
            if bucket.failures >= self._max:
                return False, max(1, int(self._window - elapsed))
            return True, 0

    def record_failure(self, ip: str) -> int:
        """Increment the failure counter for ``ip`` and return the new
        count. Logs at WARN — the operator should see brute-force
        attempts in container logs."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None or (now - bucket.window_started_at) >= self._window:
                # New window.
                bucket = _Bucket(failures=1, window_started_at=now)
                self._buckets[ip] = bucket
            else:
                bucket.failures += 1
            count = bucket.failures
        logger.warning(
            "login: failed attempt %d/%d from ip=%s",
            count, self._max, ip,
        )
        return count

    def reset(self, ip: str) -> None:
        """Clear the counter for ``ip``. Called on successful login."""
        with self._lock:
            self._buckets.pop(ip, None)
