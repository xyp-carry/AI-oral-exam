"""Process-local teacher registration code and failed-attempt tracking."""

import asyncio
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable


class TeacherRegistrationCodeStore:
    """Keep one random code in memory and replace it every 24 hours."""

    def __init__(
        self,
        period_seconds: int = 24 * 60 * 60,
        *,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(18),
    ) -> None:
        self._period_seconds = period_seconds
        self._clock = clock
        self._utcnow = utcnow
        self._token_factory = token_factory
        self._lock = Lock()
        self._code = ""
        self._deadline = 0.0
        with self._lock:
            self._rotate_locked(self._clock())

    def _rotate_locked(self, now: float) -> None:
        previous = self._code
        code = self._token_factory()
        while code == previous:
            code = self._token_factory()
        self._code = code
        self._deadline = now + self._period_seconds

    def _refresh_locked(self, now: float) -> None:
        if now >= self._deadline:
            self._rotate_locked(now)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            now = self._clock()
            self._refresh_locked(now)
            expires_at = self._utcnow() + timedelta(seconds=self._deadline - now)
            return {
                "registration_code": self._code,
                "expires_at": expires_at.isoformat(),
            }

    def verify(self, candidate: str | None) -> bool:
        with self._lock:
            self._refresh_locked(self._clock())
            return isinstance(candidate, str) and secrets.compare_digest(
                self._code, candidate.strip()
            )

    async def rotate_forever(self) -> None:
        """Rotate on schedule; reads also refresh after a delayed wake-up."""
        while True:
            with self._lock:
                now = self._clock()
                self._refresh_locked(now)
                delay = max(0.0, self._deadline - now)
            await asyncio.sleep(delay)


class FailedCodeLimiter:
    """Limit failed teacher code submissions by client address."""

    def __init__(
        self,
        *,
        limit: int = 5,
        window_seconds: int = 10 * 60,
        max_clients: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_clients = max_clients
        self._clock = clock
        self._lock = Lock()
        self._failures: dict[str, deque[float]] = {}

    def _recent_locked(self, client: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(client, deque())
        while failures and now - failures[0] >= self._window_seconds:
            failures.popleft()
        return failures

    def retry_after(self, client: str) -> int:
        with self._lock:
            now = self._clock()
            failures = self._recent_locked(client, now)
            if len(failures) < self._limit:
                return 0
            return max(1, int(self._window_seconds - (now - failures[0])) + 1)

    def record_failure(self, client: str) -> None:
        with self._lock:
            now = self._clock()
            if client not in self._failures and len(self._failures) >= self._max_clients:
                self._failures.pop(next(iter(self._failures)))
            self._recent_locked(client, now).append(now)

    def clear(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)
