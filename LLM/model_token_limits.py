MODEL_INPUT_TOKEN_BUDGET_RATIO = 0.75

MODEL_TOKEN_LIMITS: dict[str, dict[str, int]] = {
    "kimi-k2.6": {"max_context_tokens": 262144, "max_input_tokens": 196608},
    "moonshot-v1-8k": {"max_context_tokens": 8192, "max_input_tokens": 6144},
    "glm-5.1": {"max_context_tokens": 200000, "max_input_tokens": 150000},
    "glm-4-flash": {"max_context_tokens": 131072, "max_input_tokens": 98304},
    "deepseek-v4-flash": {"max_context_tokens": 1000000, "max_input_tokens": 750000},
    "deepseek-v4-pro": {"max_context_tokens": 1000000, "max_input_tokens": 750000},
}


def clamp_max_input_tokens(max_context_tokens: int, max_input_tokens: int | None = None) -> int:
    safe_max_input_tokens = int(max_context_tokens * MODEL_INPUT_TOKEN_BUDGET_RATIO)
    if max_input_tokens is None or max_input_tokens <= 0:
        return safe_max_input_tokens
    return min(int(max_input_tokens), safe_max_input_tokens)


def get_model_token_limits(model_name: str | None) -> dict[str, int]:
    limits = MODEL_TOKEN_LIMITS.get(str(model_name or "").strip())
    if not limits:
        return {}
    max_context_tokens = int(limits["max_context_tokens"])
    return {
        "max_context_tokens": max_context_tokens,
        "max_input_tokens": clamp_max_input_tokens(
            max_context_tokens,
            int(limits.get("max_input_tokens") or 0),
        ),
    }
