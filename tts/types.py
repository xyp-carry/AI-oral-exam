"""Provider-independent TTS request and audio payload types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AudioEncoding = Literal["wav", "pcm_s16le", "pcm_f32le"]


@dataclass(frozen=True)
class TTSRequest:
    text: str
    voice_id: str | None = None


@dataclass(frozen=True)
class AudioPayload:
    data: bytes
    encoding: AudioEncoding
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(frozen=True)
class PCMChunk:
    audio: bytes
    sample_rate: int
    channels: int