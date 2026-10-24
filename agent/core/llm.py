from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any


class LLMRequestError(RuntimeError):
    """An expected provider request or authentication failure."""


def completion(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
) -> Any:
    """Call the configured LiteLLM provider with OpenAI-compatible tool calling."""
    configured_model = model or os.getenv("LLM_MODEL")
    if not configured_model:
        raise LLMRequestError("LLM_MODEL must be set to the configured provider/model")

    api_key = os.getenv("LLM_API_KEY")
    api_base = os.getenv("LLM_API_BASE")
    if not api_key and not api_base:
        raise LLMRequestError(
            "Set LLM_API_KEY or LLM_API_BASE before starting an investigation"
        )

    try:
        from litellm import completion as litellm_completion
        from litellm.exceptions import APIError
    except ImportError as exc:
        raise LLMRequestError(
            "LiteLLM is not installed; install project dependencies with "
            "`python -m pip install -r requirements.txt`"
        ) from exc

    options: dict[str, Any] = {
        "model": configured_model,
        "messages": list(messages),
        "tools": list(tools),
        "tool_choice": "auto",
        "temperature": temperature,
    }
    if api_key:
        options["api_key"] = api_key
    if api_base:
        options["api_base"] = api_base
    try:
        return litellm_completion(**options)
    except (APIError, TimeoutError, OSError, ValueError) as exc:
        raise LLMRequestError(f"{type(exc).__name__}: {exc}") from exc