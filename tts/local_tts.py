"""Adapt synchronous or asynchronous local TTS models to AudioPayload."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Iterator
from typing import Any, Callable

from .types import AudioEncoding, AudioPayload, TTSRequest


def _next_item(iterator: Iterator[Any]) -> tuple[bool, Any]:
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


def local_audio_payload(
    output: Any,
    *,
    sample_rate: int,
    channels: int = 1,
    encoding: AudioEncoding = "pcm_f32le",
) -> AudioPayload:
    """Convert common model results; custom models may pass a converter."""
    if isinstance(output, AudioPayload):
        return output
    if isinstance(output, dict):
        return local_audio_payload(
            output.get("audio"),
            sample_rate=int(output.get("sample_rate") or sample_rate),
            channels=int(output.get("channels") or channels),
            encoding=output.get("encoding") or encoding,
        )
    if isinstance(output, tuple) and len(output) in (2, 3):
        return local_audio_payload(
            output[0],
            sample_rate=int(output[1]),
            channels=int(output[2]) if len(output) == 3 else channels,
            encoding=encoding,
        )
    if isinstance(output, bytes):
        return AudioPayload(output, encoding, sample_rate, channels)
    if hasattr(output, "detach"):
        output = output.detach().cpu().numpy()

    import numpy as np

    audio = np.asarray(output)
    if audio.ndim == 2:
        if audio.shape[1] == channels:
            pass
        elif audio.shape[0] == channels:
            audio = audio.T
        else:
            raise ValueError("Local TTS channel count does not match the waveform")
    elif audio.ndim != 1 or channels != 1:
        raise ValueError("Local TTS waveform must be mono or samples-by-channels")
    if audio.dtype.kind == "f":
        data = np.asarray(audio, dtype="<f4").tobytes()
        return AudioPayload(data, "pcm_f32le", sample_rate, channels)
    if audio.dtype.kind in "iu":
        data = np.clip(audio, -32768, 32767).astype("<i2").tobytes()
        return AudioPayload(data, "pcm_s16le", sample_rate, channels)
    raise TypeError("Unsupported local TTS waveform dtype")


class LocalTTSBackend:
    """Wrap a model callable that accepts TTSRequest and returns audio."""

    def __init__(
        self,
        synthesizer: Callable[[TTSRequest], Any],
        *,
        sample_rate: int,
        channels: int = 1,
        encoding: AudioEncoding = "pcm_f32le",
        converter: Callable[[Any], AudioPayload] | None = None,
    ) -> None:
        self.synthesizer = synthesizer
        self.sample_rate = sample_rate
        self.channels = channels
        self.encoding = encoding
        self.converter = converter

    def _convert(self, output: Any) -> AudioPayload:
        if self.converter is not None:
            return self.converter(output)
        return local_audio_payload(
            output,
            sample_rate=self.sample_rate,
            channels=self.channels,
            encoding=self.encoding,
        )

    async def synthesize(self, request: TTSRequest) -> AsyncIterator[AudioPayload]:
        if not request.text.strip():
            return
        result = await asyncio.to_thread(self.synthesizer, request)
        if inspect.isawaitable(result):
            result = await result
        if hasattr(result, "__aiter__"):
            async for item in result:
                yield self._convert(item)
        elif isinstance(result, Iterator):
            while True:
                has_item, item = await asyncio.to_thread(_next_item, result)
                if not has_item:
                    break
                yield self._convert(item)
        else:
            yield self._convert(result)

    async def close(self) -> None:
        close = getattr(self.synthesizer, "close", None)
        if callable(close):
            result = await asyncio.to_thread(close)
            if inspect.isawaitable(result):
                await result