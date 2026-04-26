"""Ollama HTTP client + agent-spec loader.

Talks to the local Ollama server's /api/chat endpoint with `format` for
structured output. Newer Ollama versions accept a JSON schema for `format`;
older versions accept "json" as a string. We pass the schema by default and
fall back to "json" if the server rejects it.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parent.parent.parent

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL_REASONING = os.environ.get("OLLAMA_MODEL_REASONING", "qwen3.5:latest")
MODEL_DEFAULT = os.environ.get("OLLAMA_MODEL_DEFAULT", "gemma4:latest")

# model_hint -> ollama model
MODEL_BY_HINT = {
    "reasoning-strong": MODEL_REASONING,
    "tool-use-capable": MODEL_DEFAULT,
    "writing-strong":   MODEL_DEFAULT,
    "any":              MODEL_DEFAULT,
    "none":             MODEL_DEFAULT,
}


@functools.lru_cache(maxsize=64)
def load_agent_spec(spec_path: str) -> str:
    return (REPO / spec_path).read_text(encoding="utf-8")


def model_for(agent_meta: dict) -> str:
    return MODEL_BY_HINT.get(agent_meta.get("model_hint", "any"), MODEL_DEFAULT)


def call_model_json(
    *,
    system_spec: str,
    user_prompt: str,
    model: str,
    schema: dict | None = None,
    temperature: float = 0.2,
    timeout: int = 900,
) -> dict[str, Any]:
    """POST /api/chat with structured output. Returns the parsed JSON.

    `schema` is sent as `format: <schema>` (Ollama >= 0.5). On servers that
    reject a schema-shaped format, we retry with `format: "json"`.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_spec},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "keep_alive": "30m",
        "think": False,  # qwen3.5 etc. otherwise burn output budget on thinking
        "options": {"temperature": temperature, "num_ctx": 16384},
    }
    if schema is not None:
        payload["format"] = schema

    resp = _post_chat(payload, timeout)
    if resp.status_code == 400 and schema is not None:
        payload["format"] = "json"
        resp = _post_chat(payload, timeout)
    resp.raise_for_status()

    body = resp.json()
    content = (body.get("message") or {}).get("content", "")
    try:
        return _parse_json(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Ollama returned non-JSON content (model={model}, len={len(content)}): "
            f"{content[:300]!r}"
        ) from e


def _post_chat(payload: dict, timeout: int) -> requests.Response:
    return requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=timeout)


def _parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def health_check() -> None:
    """Raise with a clear message if Ollama isn't reachable."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(
            f"Ollama not reachable at {OLLAMA_HOST}. Start the server "
            f"(or set OLLAMA_HOST). Underlying error: {e}"
        ) from e
    available = {m["name"] for m in r.json().get("models", [])}
    needed = {MODEL_REASONING, MODEL_DEFAULT}
    missing = needed - available
    if missing:
        raise RuntimeError(
            f"Ollama is up but models are missing: {sorted(missing)}. "
            f"Pull them with: " + "; ".join(f"ollama pull {m}" for m in sorted(missing))
        )
