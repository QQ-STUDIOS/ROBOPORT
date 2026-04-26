"""Shared Anthropic client + agent-spec loader.

Agent specs (markdown) are loaded once per process and cached. The spec text
is sent as a `cache_control: ephemeral` system block so the prompt cache holds
it across calls — first request writes, subsequent reads pay ~0.1x.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

import anthropic

REPO = Path(__file__).resolve().parent.parent.parent

# model_hint -> model id. Reasoning-heavy work goes to Opus 4.7; tool-use and
# generic agents to Sonnet 4.6; deterministic / no-LLM agents shouldn't reach
# this layer at all (the executor short-circuits them).
MODEL_BY_HINT = {
    "reasoning-strong": "claude-opus-4-7",
    "tool-use-capable": "claude-sonnet-4-6",
    "writing-strong":   "claude-sonnet-4-6",
    "any":              "claude-sonnet-4-6",
    "none":             "claude-sonnet-4-6",
}


@functools.lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it or pass --live=false to "
            "use the stub runtime."
        )
    return anthropic.Anthropic()


@functools.lru_cache(maxsize=64)
def load_agent_spec(spec_path: str) -> str:
    """Load an agent spec markdown file, relative to the repo root."""
    p = REPO / spec_path
    return p.read_text(encoding="utf-8")


def model_for(agent_meta: dict) -> str:
    return MODEL_BY_HINT.get(agent_meta.get("model_hint", "any"), "claude-sonnet-4-6")


def call_model_json(
    *,
    system_spec: str,
    user_prompt: str,
    model: str,
    max_tokens: int = 8000,
    schema: dict | None = None,
) -> dict[str, Any]:
    """Call Claude with a cached system prompt and force a JSON response.

    `schema` is an optional JSON schema for output_config.format. If omitted,
    the model is told to produce JSON in the user prompt and we parse the
    first text block.
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{
            "type": "text",
            "text": system_spec,
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if schema is not None:
        kwargs["output_config"] = {
            "format": {"type": "json_schema", "schema": schema},
        }

    resp = client.messages.create(**kwargs)
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last-resort: pull the largest {...} block.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
