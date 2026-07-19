import asyncio
import time
from typing import Any

from fastapi import Depends, HTTPException, status
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from Authentication.auth import get_current_user
from LLM.model_repository import (
    create_user_model,
    delete_user_model,
    get_user_model,
    list_user_models,
)


MODEL_TEST_PROMPT = "Reply only OK."
MODEL_TEST_TIMEOUT_SECONDS = 20


MODEL_PROVIDER_TEMPLATES: dict[str, dict[str, Any]] = {
    "kimi": {
        "label": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": {
            "kimi-k2.6": {
                "label": "Kimi K2.6",
                "model_name": "kimi-k2.6",
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.3, "min": 0, "max": 1},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                },
            },
            "moonshot-v1-8k": {
                "label": "Moonshot V1 8K",
                "model_name": "moonshot-v1-8k",
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.3, "min": 0, "max": 1},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                },
            },
        },
    },
    "glm": {
        "label": "GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": {
            "glm-5.1": {
                "label": "GLM-5.1",
                "model_name": "glm-5.1",
                "params_schema": {
                    "temperature": {"type": "number", "default": 1.0, "min": 0, "max": 1},
                    "top_p": {"type": "number", "default": 0.7, "min": 0, "max": 1},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                },
            },
            "glm-4-flash": {
                "label": "GLM-4-Flash",
                "model_name": "glm-4-flash",
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
        "base_url": "https://api.deepseek.com",
        "models": {
            "deepseek-v4-flash": {
                "label": "DeepSeek V4 Flash",
                "model_name": "deepseek-v4-flash",
                "params_schema": {
                    "temperature": {"type": "number", "default": 0.7, "min": 0, "max": 2},
                    "max_tokens": {"type": "integer", "default": 1024, "min": 1},
                    "thinking": {"type": "object", "default": {"type": "disabled"}},
                },
            },
            "deepseek-v4-pro": {
                "label": "DeepSeek V4 Pro",
                "model_name": "deepseek-v4-pro",
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
    display_name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ModelTestRequest(BaseModel):
    provider: str
    provider_model_key: str
    model_api_key: str
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
    return {
        "provider": provider,
        "provider_model_key": provider_model_key,
        "base_url": provider_template["base_url"],
        "model_name": model_template["model_name"],
        "model_label": model_template["label"],
        "model_api_key": normalize_required_text(
            req.model_api_key,
            "MODEL_API_KEY_REQUIRED",
            "model_api_key cannot be empty",
        ),
        "display_name": normalize_optional_text(getattr(req, "display_name", None)),
        "params": validate_model_params(req.params, model_template),
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
    model = init_model(
        config["model_name"],
        config["base_url"],
        config["model_api_key"],
        config["params"],
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
            )
        except ValueError as error:
            raise_model_value_error(error)
        return {
            "success": True,
            "model": model,
            "test_result": test_result,
        }

    @app.get(
        "/models",
        tags=["LLM"],
        summary="List model configurations",
    )
    async def list_model_configs(current_user: dict = Depends(get_current_user)):
        owner_user_id = get_current_user_id(current_user)
        try:
            models = await list_user_models(owner_user_id, include_api_key=False)
        except ValueError as error:
            raise_model_value_error(error)
        return {
            "success": True,
            "count": len(models),
            "models": models,
        }

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
