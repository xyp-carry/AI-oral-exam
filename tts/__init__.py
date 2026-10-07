"""Extensible TTS backends and Pipecat audio bridge."""

from .api_tts import DoubaoStreamingBackend, MiniMaxAPIBackend, VolcengineHTTPStreamingBackend, VolcengineStreamingBackend
from .factory import create_tts_backend, create_tts_backend_from_binding
from .local_tts import LocalTTSBackend, local_audio_payload
from .normalize import AudioNormalizer
from .processor import TTSFrameProcessor
from .types import AudioPayload, PCMChunk, TTSRequest

__all__ = [
    "AudioNormalizer",
    "AudioPayload",
    "DoubaoStreamingBackend",
    "VolcengineHTTPStreamingBackend",
    "VolcengineStreamingBackend",
    "LocalTTSBackend",
    "MiniMaxAPIBackend",
    "PCMChunk",
    "TTSFrameProcessor",
    "TTSRequest",
    "create_tts_backend",
    "create_tts_backend_from_binding",
    "local_audio_payload",
]
