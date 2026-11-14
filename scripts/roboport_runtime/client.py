"""Shared spec loader + JSON parser + provider singleton.

The provider abstraction lives in providers.py. This module is the
narrow surface the planner / executor / grader pull from.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Callable

from .providers import Provider, get_provider
from .budget import governed_chat

REPO = Path(__file__).resolve().parent.parent.parent


@functools.lru_cache(maxsize=1)
def _provider_cached() -> Provider:
    return get_provider()


def provider() -> Provider:
    # Providers capture configuration when built, so a runtime config scope
    # gets its own instance instead of the process-wide one.
    from .config import scope_cache
    cache = scope_cache()
    if cache is None:
        return _provider_cached()
    if 'provider' not in cache:
        cache['provider'] = get_provider()
    return cache['provider']


def health_check() -> None:
    provider().health_check()


@functools.lru_cache(maxsize=64)
def load_agent_spec(spec_path: str) -> str:
    return (REPO / spec_path).read_text(encoding="utf-8")


def call_model_json(
    *,
    system_spec: str,
    user_prompt: str,
    model_hint: str = "any",
    schema: dict | None = None,
    user_prompt_fallbacks: list[str] | None = None,
    prompt_selected: Callable[[str], None] | None = None,
    response_observer: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """One-shot JSON call. prompt_selected observes the exact dispatched user prompt."""
    p = provider()
    if schema is not None:
        system_spec += '\n\nOUTPUT JSON SCHEMA\n' + json.dumps(schema, ensure_ascii=False, separators=(',', ':'))
    out = governed_chat(p,
        prompt_selected=prompt_selected,
        message_fallbacks=([[{"role": "user", "content": prompt}] for prompt in user_prompt_fallbacks]
                           if user_prompt_fallbacks is not None else None),
        system=system_spec,
        messages=[{"role": "user", "content": user_prompt}],
        tools=None,
        force_json=schema if schema is not None else True,
        model_hint=model_hint,
    )
    if response_observer is not None:
        response_observer(out["content"])
    result = _parse_json(out["content"])
    if schema is not None:
        import jsonschema
        jsonschema.validate(result, schema)
    return result


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise json.JSONDecodeError("empty content", text, 0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
