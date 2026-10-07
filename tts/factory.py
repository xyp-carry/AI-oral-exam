"""Choose a TTS backend without coupling the pipeline to its provider."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Callable

from .api_tts import MiniMaxAPIBackend, VolcengineHTTPStreamingBackend, VolcengineStreamingBackend
from .backend import TTSBackend
from .local_tts import LocalTTSBackend


def create_tts_backend(
    settings: Mapping[str, Any],
    *,
    local_synthesizer: Callable | None = None,
) -> TTSBackend:
    provider = str(settings.get("provider") or "").strip().lower()
    if provider in {"minimax", "minimax_api"}:
        return MiniMaxAPIBackend(
            api_key=str(settings.get("api_key") or os.getenv("MINIMAX_API_KEY") or ""),
            voice_id=str(
                settings.get("voice_id") or "Chinese (Mandarin)_Male_Announcer"
            ),
            model=str(settings.get("model") or "speech-2.8-turbo"),
            url=str(settings.get("url") or "https://api.minimaxi.com/v1/t2a_v2"),
            sample_rate=int(settings.get("sample_rate") or 32000),
            timeout_seconds=float(settings.get("timeout_seconds") or 60),
        )
    if provider in {"doubao", "volcengine"}:
        url = str(settings.get("url") or "https://openspeech.bytedance.com/api/v3/tts/unidirectional")
        backend_class = VolcengineStreamingBackend if url.startswith("wss://") else VolcengineHTTPStreamingBackend
        options = dict(
            api_key=str(settings.get("api_key") or os.getenv("DOUBAO_API_KEY") or ""),
            voice_id=str(
                settings.get("speaker")
                or settings.get("voice_id")
                or "zh_male_jieshuoxiaoming_uranus_bigtts"
            ),
            resource_id=str(settings.get("resource_id") or "seed-tts-2.0"),
            speech_rate=settings.get("speech_rate", 0),
            sample_rate=int(settings.get("sample_rate") or 24000),
            url=url,
            timeout_seconds=float(settings.get("timeout_seconds") or 60),
        )
        if backend_class is VolcengineStreamingBackend:
            options["app_id"] = settings.get("app_id")
        return backend_class(**options)
    if provider == "local":
        if local_synthesizer is None:
            raise ValueError("local_synthesizer is required for local TTS")
        return LocalTTSBackend(
            local_synthesizer,
            sample_rate=int(settings["sample_rate"]),
            channels=int(settings.get("channels") or 1),
            encoding=settings.get("encoding") or "pcm_f32le",
        )
    raise ValueError(f"Unsupported TTS provider: {provider}")

def create_tts_backend_from_binding(binding: Mapping[str, Any] | None) -> TTSBackend:
    """Load a TTS backend from an exam item's bound user model."""
    if not isinstance(binding, Mapping):
        raise ValueError("TTS_MODEL_REQUIRED")
    model = binding.get("model")
    if not isinstance(model, Mapping) or model.get("model_type") != "tts":
        raise ValueError("TTS_MODEL_TYPE_INVALID")
    if not str(model.get("model_api_key") or "").strip():
        raise ValueError("TTS_MODEL_API_KEY_REQUIRED")

    params = model.get("params") or {}
    overrides = binding.get("model_settings") or {}
    if not isinstance(params, Mapping) or not isinstance(overrides, Mapping):
        raise ValueError("TTS_MODEL_SETTINGS_INVALID")
    settings = dict(params)
    settings.update(overrides)
    settings.update({
        "provider": model.get("provider"),
        "api_key": model["model_api_key"],
        "resource_id": model.get("provider_model_key"),
        "url": model.get("base_url"),
    })
    return create_tts_backend(settings)
