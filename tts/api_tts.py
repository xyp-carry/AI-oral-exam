"""Provider adapters for HTTP and streaming WebSocket TTS APIs."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect

from .types import AudioPayload, TTSRequest


class MiniMaxAPIBackend:
    """Adapter for the existing MiniMax text-to-audio API configuration."""

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = "Chinese (Mandarin)_Male_Announcer",
        model: str = "speech-2.8-turbo",
        url: str = "https://api.minimaxi.com/v1/t2a_v2",
        sample_rate: int = 32000,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("MiniMax API key is required")
        self.voice_id = voice_id
        self.model = model
        self.url = url
        self.sample_rate = sample_rate
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def synthesize(self, request: TTSRequest) -> AsyncIterator[AudioPayload]:
        if not request.text.strip():
            return
        payload: dict[str, Any] = {
            "model": self.model,
            "text": request.text,
            "stream": False,
            "voice_setting": {
                "voice_id": request.voice_id or self.voice_id,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": self.sample_rate,
                "format": "wav",
                "channel": 1,
            },
        }
        response = await self.client.post(self.url, json=payload)
        response.raise_for_status()
        body = response.json()
        audio_hex = (body.get("data") or {}).get("audio")
        if not isinstance(audio_hex, str) or not audio_hex:
            raise ValueError(f"MiniMax returned no audio: {body.get('base_resp')}")
        try:
            audio = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise ValueError("MiniMax audio is not valid hexadecimal data") from exc
        yield AudioPayload(data=audio, encoding="wav")

    async def close(self) -> None:
        await self.client.aclose()


# The V3 protocol wraps JSON and PCM in binary WebSocket frames.
_DOUBAO_HEADER = b"\x11\x14\x10\x00"
_DOUBAO_SAMPLE_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}


def _validate_speech_rate(speech_rate: int) -> None:
    if isinstance(speech_rate, bool) or not isinstance(speech_rate, int) or not -50 <= speech_rate <= 100:
        raise ValueError("Doubao TTS speech_rate must be an integer from -50 to 100")


def _doubao_request(event: int, payload: dict[str, Any], session_id: str | None = None) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    packet = bytearray(_DOUBAO_HEADER)
    packet.extend(struct.pack(">I", event))
    if session_id is not None:
        identifier = session_id.encode()
        packet.extend(struct.pack(">I", len(identifier)))
        packet.extend(identifier)
    packet.extend(struct.pack(">I", len(body)))
    packet.extend(body)
    return bytes(packet)


@dataclass(frozen=True)
class _DoubaoResponse:
    event: int | None
    payload: bytes
    session_id: str | None = None
    error_code: int | None = None


def _doubao_response(data: bytes) -> _DoubaoResponse:
    if len(data) < 4 or data[0] >> 4 != 1:
        raise ValueError("Invalid Doubao TTS protocol header")
    offset = (data[0] & 15) * 4
    if offset < 4 or offset > len(data) or data[2] & 15:
        raise ValueError("Unsupported Doubao TTS header or compression")
    kind = data[1] >> 4

    def read_bytes() -> bytes:
        nonlocal offset
        if offset + 4 > len(data):
            raise ValueError("Truncated Doubao TTS response")
        size = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        if offset + size > len(data):
            raise ValueError("Truncated Doubao TTS payload")
        value = data[offset:offset + size]
        offset += size
        return value

    if kind == 15:
        if offset + 4 > len(data):
            raise ValueError("Truncated Doubao TTS error")
        code = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        response = _DoubaoResponse(None, read_bytes(), error_code=code)
    elif kind in (9, 11) and data[1] & 4:
        if offset + 4 > len(data):
            raise ValueError("Truncated Doubao TTS event")
        event = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        identifier = read_bytes().decode()
        response = _DoubaoResponse(
            event, read_bytes(), session_id=identifier if event >= 100 else None
        )
    else:
        raise ValueError(f"Unsupported Doubao TTS message type: {kind}")
    if offset != len(data):
        raise ValueError("Unexpected bytes after Doubao TTS payload")
    return response


class VolcengineStreamingBackend:
    """Stream Doubao 2.0 PCM audio as AudioPayload objects."""

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = "zh_male_jieshuoxiaoming_uranus_bigtts",
        resource_id: str = "seed-tts-2.0",
        app_id: str | None = None,
        speech_rate: int = 0,
        sample_rate: int = 32000,
        url: str = "wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("Doubao API key is required")
        if sample_rate not in _DOUBAO_SAMPLE_RATES:
            raise ValueError(f"Unsupported Doubao TTS sample rate: {sample_rate}")
        _validate_speech_rate(speech_rate)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key = api_key
        self.app_id = app_id.strip() if app_id is not None else None
        if app_id is not None and not self.app_id:
            raise ValueError("Volcengine app_id cannot be empty")
        self.voice_id = voice_id
        self.resource_id = resource_id
        self.speech_rate = speech_rate
        self.sample_rate = sample_rate
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._connection: ClientConnection | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _receive(self, connection: ClientConnection) -> _DoubaoResponse:
        message = await asyncio.wait_for(connection.recv(), self.timeout_seconds)
        if isinstance(message, str):
            raise RuntimeError(f"Doubao TTS returned a text error: {message[:500]}")
        response = _doubao_response(message)
        if response.error_code is not None or response.event in (51, 151, 153):
            detail = response.payload.decode(errors="replace")
            raise RuntimeError(
                f"Doubao TTS failed ({response.error_code or response.event}): {detail[:500]}"
            )
        if response.event == 152:
            status = json.loads(response.payload).get("status_code")
            if status is not None and status != 20000000:
                raise RuntimeError(f"Doubao TTS session failed: {response.payload[:500]!r}")
        return response

    async def _connect(self) -> ClientConnection:
        if self._connection is not None:
            return self._connection
        headers = {
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        if self.app_id:
            headers["X-Api-App-Id"] = self.app_id
            headers["X-Api-Access-Key"] = self.api_key
        else:
            headers["X-Api-Key"] = self.api_key
        connection = await connect(
            self.url,
            additional_headers=headers,
            open_timeout=self.timeout_seconds,
            max_size=16 * 1024 * 1024,
        )
        try:
            await connection.send(_doubao_request(1, {}))
            if (await self._receive(connection)).event != 50:
                raise RuntimeError("Doubao TTS connection was not started")
        except BaseException:
            await connection.close()
            raise
        self._connection = connection
        return connection

    async def _disconnect(self, *, finish: bool = False) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        if finish:
            with suppress(Exception):
                await connection.send(_doubao_request(2, {}))
        await connection.close()

    async def synthesize(self, request: TTSRequest) -> AsyncIterator[AudioPayload]:
        if not request.text.strip():
            return
        async with self._lock:
            if self._closed:
                raise RuntimeError("Doubao TTS backend is closed")
            connection = await self._connect()
            session_id = uuid.uuid4().hex
            try:
                await connection.send(_doubao_request(
                    100,
                    {
                        "namespace": "BidirectionalTTS",
                        "user": {"uid": "ai-oral-exam"},
                        "req_params": {
                            "speaker": request.voice_id or self.voice_id,
                            "audio_params": {
                                "format": "pcm",
                                "sample_rate": self.sample_rate,
                                "speech_rate": self.speech_rate,
                            },
                        },
                    },
                    session_id,
                ))
                started = await self._receive(connection)
                if started.event != 150 or started.session_id != session_id:
                    raise RuntimeError("Doubao TTS session was not started")
                await connection.send(_doubao_request(
                    200, {"req_params": {"text": request.text}}, session_id
                ))
                await connection.send(_doubao_request(102, {}, session_id))
                while True:
                    response = await self._receive(connection)
                    if response.session_id != session_id:
                        raise RuntimeError("Doubao TTS returned the wrong session ID")
                    if response.event == 352 and response.payload:
                        yield AudioPayload(
                            response.payload, "pcm_s16le", self.sample_rate, 1
                        )
                    elif response.event == 152:
                        break
            except BaseException:
                await self._disconnect()
                raise

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self._disconnect(finish=True)


class VolcengineHTTPStreamingBackend:
    """Stream PCM chunks from Volcengine's HTTP Chunked TTS endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = "zh_male_jieshuoxiaoming_uranus_bigtts",
        resource_id: str = "seed-tts-2.0",
        speech_rate: int = 0,
        sample_rate: int = 24000,
        url: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("Doubao API key is required")
        if sample_rate not in _DOUBAO_SAMPLE_RATES:
            raise ValueError(f"Unsupported Doubao TTS sample rate: {sample_rate}")
        _validate_speech_rate(speech_rate)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key = api_key
        self.voice_id = voice_id
        self.resource_id = resource_id
        self.speech_rate = speech_rate
        self.sample_rate = sample_rate
        self.url = url
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    async def synthesize(self, request: TTSRequest) -> AsyncIterator[AudioPayload]:
        if not request.text.strip():
            return
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        payload = {
            "req_params": {
                "text": request.text,
                "speaker": request.voice_id or self.voice_id,
                "additions": json.dumps({
                    "disable_markdown_filter": False,
                    "disable_emoji_filter": False,
                    "enable_latex_tn": True,
                }),
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "speech_rate": self.speech_rate,
                },
            }
        }
        completed = False
        async with self.client.stream("POST", self.url, headers=headers, json=payload) as response:
            if response.is_error:
                await response.aread()
                response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Doubao TTS returned an invalid response")
                code = message.get("code", 0)
                if code == 20000000:
                    completed = True
                    break
                if code != 0:
                    raise RuntimeError(f"Doubao TTS failed ({code}): {message.get('message', '')}")
                encoded = message.get("data")
                if encoded:
                    audio = base64.b64decode(encoded, validate=True)
                    if audio:
                        yield AudioPayload(audio, "pcm_s16le", self.sample_rate, 1)
        if not completed:
            raise RuntimeError("Doubao TTS stream ended before completion")

    async def close(self) -> None:
        await self.client.aclose()


# Preserve the earlier public name for existing callers.
DoubaoStreamingBackend = VolcengineStreamingBackend
