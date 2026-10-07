"""The interface implemented by API and local TTS providers."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from .types import AudioPayload, TTSRequest


class TTSBackend(Protocol):
    def synthesize(self, request: TTSRequest) -> AsyncIterator[AudioPayload]:
        """Yield one or more provider audio payloads."""
        ...

    async def close(self) -> None:
        """Release provider resources."""
        ...