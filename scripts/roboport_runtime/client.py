"""Shared spec loader + JSON parser + provider singleton.

The provider abstraction lives in providers.py. This module is the
narrow surface the planner / executor / grader pull from.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from .providers import Provider, get_provider

REPO = Path(__file__).resolve().parent.parent.parent


@functools.lru_cache(maxsize=1)
def _provider_cached() -> Provider:
    return get_provider()


def provider() -> Provider:
    return _provider_cached()


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
) -> dict[str, Any]:
    """One-shot JSON call with no tools. Used by planner + grader."""
    p = provider()
    out = p.chat_with_tools(
        system=system_spec,
        messages=[{"role": "user", "content": user_prompt}],
        tools=None,
        force_json=True,
        model_hint=model_hint,
    )
    return _parse_json(out["content"])


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
