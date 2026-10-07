"""Decode provider audio and produce transport-ready PCM16 chunks."""

from __future__ import annotations

import io
import math
import wave

from .types import AudioPayload, PCMChunk


def _decode_audio(payload: AudioPayload):
    import numpy as np

    data = payload.data
    encoding = payload.encoding
    sample_rate = payload.sample_rate
    channels = payload.channels
    print(f"Decoding {encoding} audio with sample rate {sample_rate} and {channels} channels")
    if encoding == "wav":
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getcomptype() != "NONE":
                raise ValueError("Compressed WAV is not supported")
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            data = wav.readframes(wav.getnframes())
        if width == 1:
            samples = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128) / 128
        elif width == 2:
            samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
        elif width == 3:
            raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            values = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
            values = np.where(values >= (1 << 23), values - (1 << 24), values)
            samples = values.astype(np.float32) / (1 << 23)
        elif width == 4:
            samples = np.frombuffer(data, dtype="<i4").astype(np.float32) / (1 << 31)
        else:
            raise ValueError(f"Unsupported WAV sample width: {width}")
    elif encoding == "pcm_s16le":
        if len(data) % 2:
            raise ValueError("PCM16 payload has an incomplete sample")
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
    elif encoding == "pcm_f32le":
        if len(data) % 4:
            raise ValueError("Float32 PCM payload has an incomplete sample")
        samples = np.frombuffer(data, dtype="<f4")
    else:
        raise ValueError(f"Unsupported TTS audio encoding: {encoding}")

    if not sample_rate or not channels or sample_rate <= 0 or channels <= 0:
        raise ValueError("TTS audio must include a positive sample rate and channel count")
    if len(samples) % channels:
        raise ValueError("TTS audio has an incomplete channel frame")
    return samples.reshape(-1, channels), sample_rate


def _resample(mono, source_rate: int, target_rate: int):
    import numpy as np

    if source_rate == target_rate or mono.size == 0:
        return mono
    try:
        from scipy.signal import resample_poly
    except ImportError:
        output_size = max(1, round(mono.size * target_rate / source_rate))
        source_positions = np.arange(mono.size)
        target_positions = np.arange(output_size) * source_rate / target_rate
        return np.interp(target_positions, source_positions, mono).astype(np.float32)
    divisor = math.gcd(source_rate, target_rate)
    return resample_poly(
        mono, target_rate // divisor, source_rate // divisor
    ).astype(np.float32)


class AudioNormalizer:
    """Convert WAV or raw PCM to mono PCM16 at the configured transport rate."""

    def __init__(self, *, sample_rate: int, chunk_ms: int = 40) -> None:
        if sample_rate <= 0 or chunk_ms <= 0:
            raise ValueError("sample_rate and chunk_ms must be positive")
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms

    def normalize(self, payload: AudioPayload) -> list[PCMChunk]:
        import numpy as np

        frames, source_rate = _decode_audio(payload)
        if frames.size == 0:
            return []
        mono = frames.mean(axis=1)
        mono = _resample(mono, source_rate, self.sample_rate)
        mono = np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)
        pcm = np.rint(np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
        frames_per_chunk = max(1, round(self.sample_rate * self.chunk_ms / 1000))
        return [
            PCMChunk(pcm[start:start + frames_per_chunk].tobytes(), self.sample_rate, 1)
            for start in range(0, pcm.size, frames_per_chunk)
        ]