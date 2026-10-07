"""Pipecat boundary for provider-independent TTS audio."""

from __future__ import annotations

import asyncio
import uuid
import wave
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import EndFrame, Frame, OutputAudioRawFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .backend import TTSBackend
from .normalize import AudioNormalizer
from .types import TTSRequest


class TTSFrameProcessor(FrameProcessor):
    """Consume speech requests and send normalized audio to the next processor."""

    def __init__(
        self,
        backend: TTSBackend,
        *,
        output_sample_rate: int,
        chunk_ms: int = 40,
        max_pending: int = 8,
        recordings_dir: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.backend = backend
        self.normalizer = AudioNormalizer(
            sample_rate=output_sample_rate, chunk_ms=chunk_ms
        )
        self._queue: asyncio.Queue[tuple[TTSRequest, FrameDirection]] = asyncio.Queue(
            maxsize=max_pending
        )
        self._worker: asyncio.Task | None = None
        self._closed = False
        self.recordings_dir = Path(recordings_dir) if recordings_dir is not None else None
        self._recording_index = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            print(f"Received TTSSpeakFrame with text {frame.text}")
            if not self._closed and frame.text.strip():
                if self._worker is None:
                    self._worker = asyncio.create_task(self._run())
                await self._queue.put((TTSRequest(frame.text), direction))
            return
        if isinstance(frame, EndFrame):
            self._closed = True
            await self._queue.join()
            await self._stop_worker()
        await self.push_frame(frame, direction)

    async def _run(self) -> None:
        while True:
            request, direction = await self._queue.get()
            try:
                recording = bytearray()
                async for payload in self.backend.synthesize(request):
                    chunks = await asyncio.to_thread(self.normalizer.normalize, payload)
                    for chunk in chunks:
                        await self.push_frame(
                            OutputAudioRawFrame(
                                chunk.audio,
                                sample_rate=chunk.sample_rate,
                                num_channels=chunk.channels,
                            ),
                            direction,
                        )
                        if self.recordings_dir is not None:
                            recording.extend(chunk.audio)
                if recording and self.recordings_dir is not None:
                    self._recording_index += 1
                    path = self.recordings_dir / (
                        f"{self._recording_index:04d}_{uuid.uuid4().hex}.wav"
                    )
                    try:
                        await asyncio.to_thread(
                            self._write_recording,
                            path,
                            bytes(recording),
                            self.normalizer.sample_rate,
                        )
                    except Exception:
                        logger.exception("TTS recording could not be saved")
                    else:
                        logger.info("TTS recording saved to {}", path)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TTS synthesis failed")
            finally:
                self._queue.task_done()

    @staticmethod
    def _write_recording(path: Path, audio: bytes, sample_rate: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def cleanup(self) -> None:
        self._closed = True
        await self._stop_worker()
        await self.backend.close()
        await super().cleanup()