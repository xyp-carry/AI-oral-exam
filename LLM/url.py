import asyncio
import time
from typing import Any, Literal

import requests

from fastapi import Depends, HTTPException, status
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from Authentication.auth import get_current_user
from LLM.model_token_limits import clamp_max_input_tokens
from LLM.model_repository import (
    create_user_model,
    delete_user_model,
    get_user_model,
    list_user_models,
)


MODEL_TEST_PROMPT = "Reply only OK."
MODEL_TEST_TIMEOUT_SECONDS = 20
LOCAL_MODEL_PARAM_KEYS = {"max_context_tokens", "max_input_tokens"}


MODEL_PROVIDER_TEMPLATES: dict[str, dict[str, Any]] = {
    "volcengine": {
        "label": "火山引擎",
        "model_type": "tts",
        "base_url": "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
        "models": {
            "seed-tts-2.0": {
                "label": "语音合成 2.0 · 解说小明",
                "model_name": "seed-tts-2.0",
                "params_schema": {
                    "speaker": {
                        "type": "string",
                        "default": "zh_male_jieshuoxiaoming_uranus_bigtts",
                    },
                    "speech_rate": {
                        "type": "integer",
                        "default": 0,
                        "min": -50,
                        "max": 100,
                    },
                    "app_id": {
                        "type": "string",
                        "description": "Old-console APP ID; use model_api_key for its Access Token",
                    },
                    "sample_rate": {
                        "type": "integer",
                        "default": 24000,
                        "enum": [8000, 16000, 22050, 24000, 32000, 44100, 48000],
                    },
                    "timeout_seconds": {"type": "number", "default": 60, "min": 1},
                },
            },
        },
    },
    "mineru": {
        "label": "MinerU",
        "model_type": "file",
        "base_url": "https://mineru.net/api/v4/file-urls/batch",
        "models": {
            "vlm": {
                "label": "MinerU VLM",
                "model_name": "vlm",
                "params_schema": {},
            },
        },
    },
    "glm_embedding": {
        "label": "GLM Embedding",
        "model_type": "embedding",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/embeddings",
        "models": {
            "embedding-3": {
                "label": "Embedding-3",
                "model_name": "embedding-3",
                "params_schema": {
                    "dimensions": {
                        "type": "integer",
                        "default": 2048,
                        "min": 1,
                    },
                },
            },
        },
    },
    "kimi": {
        "label": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": {
            "kimi-k2.6": {
                "label": "Kimi K2.6",
                "model_name": "kimi-k2.6",
                "max_context_tokens": 262144,
                "max_input_tokens": 196608,
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.3, "min": 0, "max": 1},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                },
            },
            "moonshot-v1-8k": {
                "label": "Moonshot V1 8K",
                "model_name": "moonshot-v1-8k",
                "max_context_tokens": 8192,
                "max_input_tokens": 6144,
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.3, "min": 0, "max": 1},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                },
            },
        },
    },
    "glm": {
        "label": "GLM",
        "model_type": "chat",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": {
            "glm-5.1": {
                "label": "GLM-5.1",
                "model_name": "glm-5.1",
                "max_context_tokens": 200000,
                "max_input_tokens": 150000,
                "params_schema": {
                    "temperature": {"type": "number", "default": 1.0, "min": 0, "max": 1},
                    "top_p": {"type": "number", "default": 0.7, "min": 0, "max": 1},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                },
            },
            "glm-4-flash": {
                "label": "GLM-4-Flash",
                "model_name": "glm-4-flash",
                "max_context_tokens": 131072,
                "max_input_tokens": 98304,
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.7, "min": 0, "max": 1},
                    "top_p": {"type": "number", "default": 0.9, "min": 0, "max": 1},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                },
            },
        },
    },
    "deepseek": {
        "label": "DeepSeek",
        "model_type": "chat",
        "base_url": "https://api.deepseek.com",
        "models": {
            "deepseek-v4-flash": {
                "label": "DeepSeek V4 Flash",
                "model_name": "deepseek-v4-flash",
                "max_context_tokens": 1000000,
                "max_input_tokens": 750000,
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.7, "min": 0, "max": 2},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                    "thinking": {"type": "object", "default": {"type": "disabled"}},
                },
            },
            "deepseek-v4-pro": {
                "label": "DeepSeek V4 Pro",
                "model_name": "deepseek-v4-pro",
                "max_context_tokens": 1000000,
                "max_input_tokens": 750000,
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.7, "min": 0, "max": 2},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                    "thinking": {"type": "object", "default": {"type": "enabled"}},
                    "reasoning_effort": {
                        "type": "string",
                        "default": "medium",
                        "enum": ["low", "medium", "high"],
                    },
                },
            },
        },
    },
}


class ModelCreateRequest(BaseModel):
    provider: str
    provider_model_key: str
    model_api_key: str
    model_type: Literal["chat", "embedding", "file", "tts"] = "chat"
    display_name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ModelTestRequest(BaseModel):
    provider: str
    provider_model_key: str
    model_api_key: str
    model_type: Literal["chat", "embedding", "file", "tts"] = "chat"
    params: dict[str, Any] = Field(default_factory=dict)


def model_error_detail(code: str, message: str) -> dict:
    return {
        "code": code,
        "message": message,
    }


def raise_model_value_error(error: ValueError) -> None:
    message = str(error)
    error_map = {
        "OWNER_USER_ID_REQUIRED": (400, "user_id cannot be empty"),
        "MODEL_NAME_REQUIRED": (400, "model_name cannot be empty"),
        "MODEL_API_KEY_REQUIRED": (400, "model_api_key cannot be empty"),
        "MODEL_ID_REQUIRED": (400, "model_id cannot be empty"),
        "MODEL_TYPE_INVALID": (400, "model_type must be chat, embedding, file, or tts"),
    }
    if message in error_map:
        status_code, detail = error_map[message]
        raise HTTPException(
            status_code=status_code,
            detail=model_error_detail(message, detail),
        )
    raise HTTPException(status_code=400, detail=message)


def get_current_user_id(current_user: dict) -> str:
    user_id = str(current_user.get("uuid") or current_user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=model_error_detail("USER_ID_REQUIRED", "user identity is invalid"),
        )
    return user_id


def normalize_required_text(value, code: str, message: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=model_error_detail(code, message),
        )
    return value


def normalize_optional_text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def resolve_provider_model(provider: str, provider_model_key: str) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    provider = normalize_required_text(provider, "MODEL_PROVIDER_REQUIRED", "provider cannot be empty").lower()
    provider_model_key = normalize_required_text(
        provider_model_key,
        "MODEL_PROVIDER_MODEL_REQUIRED",
        "provider_model_key cannot be empty",
    )
    provider_template = MODEL_PROVIDER_TEMPLATES.get(provider)
    if not provider_template:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=model_error_detail("MODEL_PROVIDER_UNSUPPORTED", "provider is not supported"),
        )
    model_template = provider_template["models"].get(provider_model_key)
    if not model_template:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=model_error_detail("MODEL_PROVIDER_MODEL_UNSUPPORTED", "provider_model_key is not supported"),
        )
    return provider, provider_model_key, provider_template, model_template


def validate_model_params(params: dict[str, Any], model_template: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=model_error_detail("MODEL_PARAMS_INVALID", "params must be an object"),
        )

    schema = model_template.get("params_schema") or {}
    unknown = sorted(set(params) - set(schema))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                **model_error_detail("MODEL_PARAMS_UNKNOWN", "params contains unsupported fields"),
                "fields": unknown,
            },
        )

    normalized: dict[str, Any] = {}
    for name, definition in schema.items():
        if name in params:
            value = params[name]
        elif "default" in definition:
            value = definition["default"]
        elif definition.get("required"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    **model_error_detail("MODEL_PARAM_REQUIRED", "required param is missing"),
                    "field": name,
                },
            )
        else:
            continue
        normalized[name] = validate_param_value(name, value, definition)
    return normalized


def validate_param_value(name: str, value: Any, definition: dict[str, Any]) -> Any:
    param_type = definition.get("type")
    if "enum" in definition and value not in definition["enum"]:
        raise_param_error(name, "value is not in allowed enum")

    if param_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise_param_error(name, "value must be an integer")
        validate_number_range(name, value, definition)
        return value
    if param_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise_param_error(name, "value must be a number")
        validate_number_range(name, float(value), definition)
        return value
    if param_type == "string":
        if not isinstance(value, str):
            raise_param_error(name, "value must be a string")
        return value
    if param_type == "boolean":
        if not isinstance(value, bool):
            raise_param_error(name, "value must be a boolean")
        return value
    if param_type == "object":
        if not isinstance(value, dict):
            raise_param_error(name, "value must be an object")
        return value
    return value


def validate_number_range(name: str, value: float, definition: dict[str, Any]) -> None:
    minimum = definition.get("min")
    maximum = definition.get("max")
    if minimum is not None and value < minimum:
        raise_param_error(name, f"value must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise_param_error(name, f"value must be <= {maximum}")


def raise_param_error(name: str, message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            **model_error_detail("MODEL_PARAM_INVALID", message),
            "field": name,
        },
    )


def build_model_config(req: ModelCreateRequest | ModelTestRequest) -> dict[str, Any]:
    provider, provider_model_key, provider_template, model_template = resolve_provider_model(
        req.provider,
        req.provider_model_key,
    )
    raw_params = dict(req.params)
    if provider == "volcengine" and "voice_id" in raw_params:
        if "speaker" in raw_params:
            raise_param_error("speaker", "speaker and voice_id cannot both be set")
        raw_params["speaker"] = raw_params.pop("voice_id")
    params = validate_model_params(raw_params, model_template)
    if provider == "volcengine":
        params["speaker"] = params["speaker"].strip()
        if not params["speaker"]:
            raise_param_error("speaker", "value cannot be empty")
    if "app_id" in params:
        params["app_id"] = params["app_id"].strip()
        if not params["app_id"]:
            raise_param_error("app_id", "value cannot be empty")
    params.update(build_model_token_limits(model_template))
    model_type = str(req.model_type or "chat").strip().lower()
    expected_model_type = provider_template.get("model_type", "chat")
    if model_type != expected_model_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                **model_error_detail(
                    "MODEL_TYPE_MISMATCH",
                    "model_type does not match the selected provider",
                ),
                "expected_model_type": expected_model_type,
            },
        )
    return {
        "provider": provider,
        "provider_model_key": provider_model_key,
        "model_type": model_type,
        "base_url": provider_template["base_url"],
        "model_name": model_template["model_name"],
        "model_label": model_template["label"],
        "model_api_key": normalize_required_text(
            req.model_api_key,
            "MODEL_API_KEY_REQUIRED",
            "model_api_key cannot be empty",
        ),
        "display_name": normalize_optional_text(getattr(req, "display_name", None)),
        "params": params,
    }


def build_model_token_limits(model_template: dict[str, Any]) -> dict[str, int]:
    max_context_tokens = int(model_template.get("max_context_tokens") or 0)
    if max_context_tokens <= 0:
        return {}

    template_max_input_tokens = int(model_template.get("max_input_tokens") or 0)
    return {
        "max_context_tokens": max_context_tokens,
        "max_input_tokens": clamp_max_input_tokens(max_context_tokens, template_max_input_tokens),
    }


def build_api_extra_body(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (params or {}).items()
        if key not in LOCAL_MODEL_PARAM_KEYS
    }


def init_model(model_name: str, url: str, api_key: str, extra_body: dict[str, Any]):
    return ChatOpenAI(
        openai_api_base=url,
        openai_api_key=api_key,
        model=model_name,
        extra_body=extra_body,
    )


def message_to_text(message) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, list):
        return "".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")


async def test_model_response(config: dict[str, Any]) -> dict[str, Any]:
    started_at = time.perf_counter()
    if config.get("model_type") == "tts":
        return await _test_tts_model(config, started_at)
    if config.get("model_type") == "embedding":
        return await _test_embedding_model(config, started_at)
    if config.get("model_type") == "file":
        return await _test_mineru_token(config, started_at)
    model = init_model(
        config["model_name"],
        config["base_url"],
        config["model_api_key"],
        build_api_extra_body(config["params"]),
    )
    try:
        response = await asyncio.wait_for(
            model.ainvoke([HumanMessage(content=MODEL_TEST_PROMPT)]),
            timeout=MODEL_TEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=model_error_detail(
                "MODEL_TEST_TIMEOUT",
                f"model test timed out after {MODEL_TEST_TIMEOUT_SECONDS}s",
            ),
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                **model_error_detail("MODEL_TEST_FAILED", "model test failed"),
                "error_class": error.__class__.__name__,
                "error": str(error),
            },
        )

    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    response_text = message_to_text(response).strip()
    if not response_text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=model_error_detail("MODEL_EMPTY_RESPONSE", "model returned empty response"),
        )
    return {
        "success": True,
        "duration_ms": duration_ms,
        "response_preview": response_text[:200],
    }


async def _test_tts_model(config: dict[str, Any], started_at: float) -> dict[str, Any]:
    from contextlib import aclosing
    from tts.factory import create_tts_backend
    from tts.types import TTSRequest

    settings = dict(config["params"])
    settings.update({
        "provider": config["provider"],
        "api_key": config["model_api_key"],
        "resource_id": config["provider_model_key"],
        "url": config["base_url"],
    })
    backend = create_tts_backend(settings)

    async def receive_audio() -> tuple[int, int, float]:
        chunks = 0
        audio_bytes = 0
        first_chunk_ms = 0.0
        async with aclosing(backend.synthesize(TTSRequest("你好"))) as stream:
            async for payload in stream:
                if payload.data:
                    if chunks == 0:
                        first_chunk_ms = round((time.perf_counter() - started_at) * 1000, 2)
                    chunks += 1
                    audio_bytes += len(payload.data)
        if not chunks:
            raise ValueError("TTS returned no audio")
        return chunks, audio_bytes, first_chunk_ms

    try:
        chunks, audio_bytes, first_chunk_ms = await asyncio.wait_for(
            receive_audio(), timeout=MODEL_TEST_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=model_error_detail("MODEL_TEST_TIMEOUT", "TTS model test timed out"),
        ) from error
    except Exception as error:
        detail = {
            **model_error_detail("MODEL_TEST_FAILED", "TTS model test failed"),
            "error_class": error.__class__.__name__,
            "error": str(error),
        }
        response = getattr(error, "response", None)
        if response is not None:
            http_status = getattr(response, "status_code", None)
            if http_status is not None:
                detail["http_status"] = http_status
            log_id = getattr(response, "headers", {}).get("x-tt-logid")
            if log_id:
                detail["log_id"] = log_id
            try:
                provider_error = response.text.strip()
            except Exception:
                provider_error = ""
            if provider_error:
                detail["provider_error"] = provider_error.replace(
                    config["model_api_key"], "[REDACTED]"
                )[:500]
            if http_status == 403:
                detail["hint"] = (
                    "TTS authentication was rejected. Check the API Key and that "
                    "seed-tts-2.0 is enabled for this account."
                )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        ) from error
    finally:
        await backend.close()

    return {
        "success": True,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "response_preview": "audio received",
        "audio_chunks": chunks,
        "audio_bytes": audio_bytes,
        "first_chunk_ms": first_chunk_ms,
    }


async def _test_embedding_model(
    config: dict[str, Any],
    started_at: float,
) -> dict[str, Any]:
    def request_embedding():
        response = requests.post(
            config["base_url"],
            headers={"Authorization": f"Bearer {config['model_api_key']}"},
            json={
                "model": config["model_name"],
                "input": ["connection test"],
            },
            timeout=MODEL_TEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data or not data[0].get("embedding"):
            raise ValueError("embedding response does not contain a vector")

    try:
        await asyncio.wait_for(
            asyncio.to_thread(request_embedding),
            timeout=MODEL_TEST_TIMEOUT_SECONDS + 1,
        )
    except asyncio.TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=model_error_detail("MODEL_TEST_TIMEOUT", "embedding model test timed out"),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                **model_error_detail("MODEL_TEST_FAILED", "embedding model test failed"),
                "error_class": error.__class__.__name__,
                "error": str(error),
            },
        ) from error
    return {
        "success": True,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "response_preview": "embedding vector received",
    }


async def _test_mineru_token(
    config: dict[str, Any],
    started_at: float,
) -> dict[str, Any]:
    def request_status():
        status_url = config["base_url"].replace(
            "/file-urls/batch",
            "/extract-results/batch/credential-check",
        )
        response = requests.get(
            status_url,
            headers={"Authorization": f"Bearer {config['model_api_key']}"},
            timeout=MODEL_TEST_TIMEOUT_SECONDS,
        )
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        code = payload.get("code") if isinstance(payload, dict) else None
        if response.status_code in {401, 403} or code in {"A0202", "A0211"}:
            raise ValueError("MinerU token is invalid or expired")
        if response.status_code >= 500:
            response.raise_for_status()

    try:
        await asyncio.wait_for(
            asyncio.to_thread(request_status),
            timeout=MODEL_TEST_TIMEOUT_SECONDS + 1,
        )
    except asyncio.TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=model_error_detail("MODEL_TEST_TIMEOUT", "MinerU token test timed out"),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                **model_error_detail("MODEL_TEST_FAILED", "MinerU token test failed"),
                "error_class": error.__class__.__name__,
                "error": str(error),
            },
        ) from error
    return {
        "success": True,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "response_preview": "MinerU authentication accepted",
    }


def llm_routes(app, args):
    """Register user model configuration routes."""

    @app.get(
        "/model_providers",
        tags=["LLM"],
        summary="List supported model providers",
    )
    async def list_model_providers():
        return {
            "success": True,
            "providers": MODEL_PROVIDER_TEMPLATES,
        }

    @app.post(
        "/models/test",
        tags=["LLM"],
        summary="Test model configuration",
    )
    async def test_model_config(req: ModelTestRequest, current_user: dict = Depends(get_current_user)):
        get_current_user_id(current_user)
        config = build_model_config(req)
        test_result = await test_model_response(config)
        return {
            "success": True,
            "provider": config["provider"],
            "provider_model_key": config["provider_model_key"],
            "model_name": config["model_name"],
            "model_type": config["model_type"],
            "base_url": config["base_url"],
            "params": config["params"],
            "test_result": test_result,
        }

    @app.post(
        "/models",
        status_code=status.HTTP_201_CREATED,
        tags=["LLM"],
        summary="Create model configuration",
    )
    async def create_model_config(
        req: ModelCreateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        owner_user_id = get_current_user_id(current_user)
        config = build_model_config(req)
        test_result = await test_model_response(config)
        try:
            model = await create_user_model(
                owner_user_id=owner_user_id,
                model_name=config["model_name"],
                model_api_key=config["model_api_key"],
                provider=config["provider"],
                base_url=config["base_url"],
                display_name=config["display_name"] or config["model_label"],
                provider_model_key=config["provider_model_key"],
                params=config["params"],
                last_test_result=test_result,
                model_type=config["model_type"],
            )
        except ValueError as error:
            raise_model_value_error(error)
        return {
            "success": True,
            "model": model,
            "test_result": test_result,
        }

    async def list_model_configs_by_type(model_type: str, current_user: dict):
        owner_user_id = get_current_user_id(current_user)
        try:
            models = await list_user_models(
                owner_user_id,
                include_api_key=False,
                model_type=model_type,
            )
        except ValueError as error:
            raise_model_value_error(error)
        return {
            "success": True,
            "count": len(models),
            "models": models,
        }

    @app.get(
        "/chat_model",
        tags=["LLM"],
        summary="List chat model configurations",
    )
    async def list_chat_model_configs(current_user: dict = Depends(get_current_user)):
        return await list_model_configs_by_type("chat", current_user)

    @app.get(
        "/embedding_model",
        tags=["LLM"],
        summary="List embedding model configurations",
    )
    async def list_embedding_model_configs(current_user: dict = Depends(get_current_user)):
        return await list_model_configs_by_type("embedding", current_user)

    @app.get(
        "/tts_model",
        tags=["LLM"],
        summary="List TTS model configurations",
    )
    async def list_tts_model_configs(current_user: dict = Depends(get_current_user)):
        return await list_model_configs_by_type("tts", current_user)

    @app.get(
        "/file_model",
        tags=["LLM"],
        summary="List file model configurations",
    )
    async def list_file_model_configs(current_user: dict = Depends(get_current_user)):
        return await list_model_configs_by_type("file", current_user)

    @app.get(
        "/models/{model_id}",
        tags=["LLM"],
        summary="Get model configuration",
    )
    async def get_model_config(
        model_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        owner_user_id = get_current_user_id(current_user)
        try:
            model = await get_user_model(
                model_id=model_id,
                owner_user_id=owner_user_id,
                include_api_key=False,
            )
        except ValueError as error:
            raise_model_value_error(error)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=model_error_detail("MODEL_NOT_FOUND", "model configuration not found"),
            )
        return {
            "success": True,
            "model": model,
        }

    @app.delete(
        "/models/{model_id}",
        tags=["LLM"],
        summary="Delete model configuration",
    )
    async def delete_model_config(
        model_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        owner_user_id = get_current_user_id(current_user)
        try:
            deleted = await delete_user_model(
                model_id=model_id,
                owner_user_id=owner_user_id,
            )
        except ValueError as error:
            raise_model_value_error(error)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=model_error_detail("MODEL_NOT_FOUND", "model configuration not found"),
            )
        return {
            "success": True,
            "message": "model configuration deleted",
        }

    return app
