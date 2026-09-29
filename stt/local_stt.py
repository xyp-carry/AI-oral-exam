import asyncio
from datetime import datetime
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def _save_audio_file(audio: bytes, filename: str, sample_rate: int, channels: int) -> None:
    with wave.open(filename, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)


def store_audio(audio: bytes, filename: str | Path, sample_rate: int, channels: int) -> None:
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    _save_audio_file(audio, str(path), sample_rate, channels)


def clear_audio(filename: str | Path) -> None:
    try:
        Path(filename).unlink()
    except FileNotFoundError:
        pass


def _pcm_to_float32_mono(audio: bytes, channels: int):
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError('numpy is required for STT PCM conversion.') from exc
    waveform = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1 and waveform.size >= channels:
        waveform = waveform[: waveform.size - (waveform.size % channels)]
        waveform = waveform.reshape(-1, channels).mean(axis=1)
    return waveform


def _load_stt_model(model_dir: str, device: str) -> Tuple[Any, dict]:
    from OralService.model import SenseVoiceSmall
    model, stt_kwargs = SenseVoiceSmall.from_pretrained(model=model_dir, device=device)
    model.eval()
    return model, dict(stt_kwargs or {})


def _load_vad_model(vad_model: str, device: str) -> Any:
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise RuntimeError('FunASR AutoModel is required for streaming VAD. Install funasr in the runtime environment.') from exc
    return AutoModel(model=vad_model, device=device, disable_pbar=True)


def _infer_stt_text(model: Any, stt_kwargs: dict, waveform: Any, sample_rate: int) -> str:
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
    inference_kwargs = dict(stt_kwargs)
    inference_kwargs['disable_pbar'] = True
    result = model.inference(
        data_in=waveform,
        fs=sample_rate,
        language='auto',
        use_itn=False,
        ban_emo_unk=False,
        output_timestamp=True,
        **inference_kwargs,
    )
    if not result or not result[0]:
        return ''
    return rich_transcription_postprocess(result[0][0].get('text', ''))


async def transcribe_audio(
    audio: bytes,
    sample_rate: int,
    channels: int,
    model_dir: str = 'iic/SenseVoiceSmall',
    device: str = 'cuda:0',
) -> str:
    if not audio or not sample_rate or not channels:
        return ''
    waveform = _pcm_to_float32_mono(audio, channels)
    model, stt_kwargs = _load_stt_model(model_dir, device)
    return await asyncio.to_thread(_infer_stt_text, model, stt_kwargs, waveform, sample_rate)


class LocalStreamingSTTService:
    def __init__(
        self,
        vad_chunk_ms: int = 200,
        sample_width: int = 2,
        transcribe_on_speech_end: bool = True,
        save_audio_segments: bool = False,
        audio_save_dir: str | Path = None,
        stt_window_ms: int = 5000,
        stt_model_dir: str = 'iic/SenseVoiceSmall',
        vad_model: str = 'fsmn-vad',
        device: str = 'cuda:0',
    ):
        self.stt_model_dir = stt_model_dir
        self.vad_model_name = vad_model
        self.device = device
        self.vad_chunk_ms = vad_chunk_ms
        self.sample_width = sample_width
        self.transcribe_on_speech_end = transcribe_on_speech_end
        self.save_audio_segments = save_audio_segments
        if self.save_audio_segments:
            self.audio_save_dir: Optional[Path] = Path(audio_save_dir or 'recordings/funasr')
        else:
            self.audio_save_dir = Path(audio_save_dir) if audio_save_dir else None

        self.stt_window_ms = stt_window_ms
        self.vad_cache: Dict[str, Any] = {}
        self.vad_buffer = bytearray()
        self.stt_window_audio = bytearray()
        self.recording_audio = bytearray()
        self.recording_started = False
        self.pending_transcriptions: List[asyncio.Task] = []
        self.stt_model: Optional[Any] = None
        self.stt_kwargs: dict = {}
        self.vad_model: Optional[Any] = None
        self.stt_model_lock = asyncio.Lock()
        self.vad_model_lock = asyncio.Lock()
        self.saved_audio_count = 0
        self.stt_segment_count = 0
        self.sample_rate: Optional[int] = None
        self.channels: Optional[int] = None
        self.is_speaking = False
        self.started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._get_vad_model()
        self._get_stt_model()
        self.started = True

    async def feed_audio(self, audio: bytes, sample_rate: int, channels: int) -> List[Dict[str, Any]]:
        if not audio:
            return []
        if not self.started:
            await self.start()
        if not sample_rate or not channels:
            return []
        async with self._lock:
            events = self._collect_completed_transcriptions()
            if self.sample_rate is None:
                self.sample_rate = sample_rate
                self.channels = channels
            elif not self.sample_rate == sample_rate or not self.channels == channels:
                events.extend(await self.flush())
                self.sample_rate = sample_rate
                self.channels = channels

            self.vad_buffer.extend(audio)
            chunk_bytes = self._chunk_bytes(sample_rate, channels)
            while len(self.vad_buffer) >= chunk_bytes:
                chunk = bytes(self.vad_buffer[:chunk_bytes])
                del self.vad_buffer[:chunk_bytes]
                events.extend(await self._process_chunk(chunk, sample_rate, channels, False))
                events.extend(self._collect_completed_transcriptions())
            return events

    async def flush(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if self.sample_rate and self.channels and self.vad_buffer:
            chunk = bytes(self.vad_buffer)
            self.vad_buffer.clear()
            events.extend(await self._process_chunk(chunk, self.sample_rate, self.channels, True))
        if self.sample_rate and self.channels and (self.is_speaking or self.stt_window_audio or self.recording_audio):
            events.extend(await self._finish_current_speech(self.sample_rate, self.channels, 'flush'))
        events.extend(await self._drain_pending_transcriptions())
        return events

    async def close(self) -> List[Dict[str, Any]]:
        events = await self.flush()
        self.reset()
        self.started = False
        return events

    def reset(self) -> None:
        self.vad_cache = {}
        self.vad_buffer.clear()
        self.stt_window_audio.clear()
        self.clear_recording_audio()
        self._cancel_pending_transcriptions()
        self.sample_rate = None
        self.channels = None
        self.is_speaking = False

    async def _process_chunk(self, chunk: bytes, sample_rate: int, channels: int, is_final: bool) -> List[Dict[str, Any]]:
        vad_result = await self._run_vad(chunk, channels, is_final)
        segments = self._extract_vad_segments(vad_result)
        has_start = any(start >= 0 and end == -1 for start, end in segments)
        has_end = any(start == -1 and end >= 0 for start, end in segments)
        has_complete = any(start >= 0 and end >= 0 for start, end in segments)
        events: List[Dict[str, Any]] = []

        if (has_start or has_complete) and not self.is_speaking:
            self.is_speaking = True
            self.recording_started = True
            events.append({'type': 'speech_start'})

        if self.is_speaking:
            self._append_stt_audio(chunk, sample_rate, channels)
            self._append_recording_audio(chunk)
            events.append({'type': 'speech_active'})

        if (has_end or has_complete or is_final) and self.is_speaking:
            events.extend(await self._finish_current_speech(sample_rate, channels, 'speech_end'))
        return events

    async def _finish_current_speech(self, sample_rate: int, channels: int, reason: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if self.is_speaking:
            self.is_speaking = False
            events.append({'type': 'speech_end', 'reason': reason})

        saved_audio_path = self._save_recording_audio(sample_rate, channels, reason)
        if saved_audio_path:
            events.append({'type': 'audio_saved', 'path': str(saved_audio_path)})

        if self.transcribe_on_speech_end and self.stt_window_audio:
            self._schedule_stt_window(sample_rate, channels, is_final=True, reason=reason)
        events.extend(await self._drain_pending_transcriptions())
        return events

    async def _run_vad(self, chunk: bytes, channels: int, is_final: bool) -> Any:
        waveform = _pcm_to_float32_mono(chunk, channels)

        def infer() -> Any:
            model = self._get_vad_model()
            return model.generate(
                input=waveform,
                cache=self.vad_cache,
                is_final=is_final,
                chunk_size=self.vad_chunk_ms,
                disable_pbar=True,
            )

        async with self.vad_model_lock:
            return await asyncio.to_thread(infer)

    def _get_stt_model(self) -> Tuple[Any, dict]:
        if self.stt_model is None:
            self.stt_model, self.stt_kwargs = _load_stt_model(self.stt_model_dir, self.device)
        return self.stt_model, dict(self.stt_kwargs)

    def _get_vad_model(self) -> Any:
        if self.vad_model is None:
            self.vad_model = _load_vad_model(self.vad_model_name, self.device)
        return self.vad_model

    def _append_stt_audio(self, chunk: bytes, sample_rate: int, channels: int) -> None:
        if not self.transcribe_on_speech_end:
            return
        self.stt_window_audio.extend(chunk)
        window_bytes = self._duration_bytes(self.stt_window_ms, sample_rate, channels)
        while len(self.stt_window_audio) >= window_bytes:
            audio = bytes(self.stt_window_audio[:window_bytes])
            del self.stt_window_audio[:window_bytes]
            self._start_transcription(audio, sample_rate, channels, is_final=False, reason='window')

    def _append_recording_audio(self, chunk: bytes) -> None:
        if self.save_audio_segments and self.recording_started:
            self.recording_audio.extend(chunk)

    def _schedule_stt_window(self, sample_rate: int, channels: int, is_final: bool, reason: str) -> None:
        audio = bytes(self.stt_window_audio)
        self.stt_window_audio.clear()
        if audio:
            self._start_transcription(audio, sample_rate, channels, is_final=is_final, reason=reason)

    def _start_transcription(self, audio: bytes, sample_rate: int, channels: int, is_final: bool, reason: str) -> None:
        self.stt_segment_count += 1
        segment_index = self.stt_segment_count
        task = asyncio.create_task(
            self._transcribe_segment(audio, sample_rate, channels, is_final, reason, segment_index)
        )
        self.pending_transcriptions.append(task)

    async def _transcribe_segment(
        self,
        audio: bytes,
        sample_rate: int,
        channels: int,
        is_final: bool,
        reason: str,
        segment_index: int,
    ) -> Optional[Dict[str, Any]]:
        text = await self._transcribe_audio(audio, sample_rate, channels)
        if not text:
            return None
        return {
            'type': 'text',
            'text': text,
            'is_final': is_final,
            'reason': reason,
            'segment_index': segment_index,
        }

    async def _transcribe_audio(self, audio: bytes, sample_rate: int, channels: int) -> str:
        if not audio or not sample_rate or not channels:
            return ''
        waveform = _pcm_to_float32_mono(audio, channels)

        def infer() -> str:
            model, stt_kwargs = self._get_stt_model()
            return _infer_stt_text(model, stt_kwargs, waveform, sample_rate)

        async with self.stt_model_lock:
            return await asyncio.to_thread(infer)

    def _collect_completed_transcriptions(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        while self.pending_transcriptions and self.pending_transcriptions[0].done():
            task = self.pending_transcriptions.pop(0)
            event = task.result()
            if event:
                events.append(event)
        return events

    async def _drain_pending_transcriptions(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        while self.pending_transcriptions:
            task = self.pending_transcriptions.pop(0)
            event = await task
            if event:
                events.append(event)
        return events

    def _cancel_pending_transcriptions(self) -> None:
        for task in self.pending_transcriptions:
            if not task.done():
                task.cancel()
        self.pending_transcriptions.clear()

    def _save_recording_audio(self, sample_rate: int, channels: int, reason: str) -> Optional[Path]:
        if not self.save_audio_segments or not self.recording_started or not self.recording_audio:
            self.clear_recording_audio()
            return None
        self.saved_audio_count += 1
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = self.audio_save_dir / f'{timestamp}_{self.saved_audio_count:04d}_{reason}.wav'
        store_audio(bytes(self.recording_audio), path, sample_rate, channels)
        self.clear_recording_audio()
        return path

    def clear_recording_audio(self) -> None:
        self.recording_audio.clear()
        self.recording_started = False

    def _chunk_bytes(self, sample_rate: int, channels: int) -> int:
        samples = max(1, int(sample_rate * self.vad_chunk_ms / 1000))
        return samples * channels * self.sample_width

    def _duration_bytes(self, duration_ms: int, sample_rate: int, channels: int) -> int:
        samples = max(1, int(sample_rate * duration_ms / 1000))
        return samples * channels * self.sample_width

    @staticmethod
    def _extract_vad_segments(vad_result: Any) -> List[Tuple[int, int]]:
        segments: List[Tuple[int, int]] = []
        if isinstance(vad_result, dict):
            values = [vad_result.get('value', [])]
        elif isinstance(vad_result, list):
            values = [item.get('value', item) if isinstance(item, dict) else item for item in vad_result]
        else:
            values = []
        for value in values:
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    try:
                        segments.append((int(item[0]), int(item[1])))
                    except (TypeError, ValueError):
                        continue
        return segments

def read_audio_pcm(path: str | Path) -> Tuple[bytes, int, int]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.wav':
        return read_wav_pcm(path)
    if suffix == '.mp3':
        return _read_mp3_pcm(path)
    raise ValueError(f'Unsupported audio format: {suffix or path.name}')


def read_wav_pcm(path: str | Path) -> Tuple[bytes, int, int]:
    with wave.open(str(path), 'rb') as wav:
        sample_width = wav.getsampwidth()
        if sample_width == 2:
            return wav.readframes(wav.getnframes()), wav.getframerate(), wav.getnchannels()
        raise ValueError(f'Only 16-bit PCM wav is supported, got {sample_width * 8}-bit')


def _read_mp3_pcm(path: Path) -> Tuple[bytes, int, int]:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is required to convert mp3 files to 16-bit PCM')
    completed = subprocess.run(
        [
            ffmpeg,
            '-hide_banner',
            '-loglevel',
            'error',
            '-i',
            str(path),
            '-ar',
            '16000',
            '-ac',
            '1',
            '-sample_fmt',
            's16',
            '-f',
            's16le',
            'pipe:1',
        ],
        check=True,
        stdout=subprocess.PIPE,
    )
    return completed.stdout, 16000, 1
