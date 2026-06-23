"""Provider backends for the ROBOPORT runtime.

Two implementations:
  - OllamaProvider   — local server via raw HTTP /api/chat
  - AnthropicProvider — Anthropic SDK with prompt caching + tool use

Selected via the ROBOPORT_PROVIDER env var (default: ollama).
"""
from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Protocol

from .pricing import cost_for


class TransientProviderError(Exception):
    """A *retryable* provider failure — HTTP 5xx, timeout, or connection error.

    The executor's Layer-1 (call-level) policy retries these a bounded number of
    times before failing loudly; anything else is treated as fatal immediately.
    """


class Message(Protocol):
    role: str
    content: str


class Provider(ABC):
    """Common interface for the runtime to call.

    `chat_with_tools` runs ONE round: send messages + tools, get back either
    a list of tool calls (each: {"name", "arguments"}) or a final assistant
    string. Caller drives the loop.
    """

    name: str

    @abstractmethod
    def chat_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        force_json: bool,
        model_hint: str,
    ) -> dict[str, Any]:
        """Returns dict with keys:
          - "tool_calls": list of {"id", "name", "arguments": dict} (may be empty)
          - "content":    assistant text (string, may be empty)
          - "usage":      optional telemetry dict (Phase 4) —
                          {"provider", "model", "prompt_tokens", "completion_tokens",
                           "cost_usd" (float|None), "latency_ms"}. Callers must
                          tolerate its absence (the fault harness omits it).
        """
        ...


# ----- Ollama --------------------------------------------------------------

class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self) -> None:
        import requests  # noqa: PLC0415
        self._requests = requests
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self.model_reasoning = os.environ.get("OLLAMA_MODEL_REASONING", "qwen3.5:latest")
        self.model_default = os.environ.get("OLLAMA_MODEL_DEFAULT", "gemma4:latest")

    def model_for(self, hint: str) -> str:
        return self.model_reasoning if hint == "reasoning-strong" else self.model_default

    def health_check(self) -> None:
        try:
            r = self._requests.get(f"{self.host}/api/tags", timeout=5)
            r.raise_for_status()
        except Exception as e:
            raise RuntimeError(
                f"Ollama not reachable at {self.host}. Start the server "
                f"(or set OLLAMA_HOST). Underlying: {e}"
            ) from e
        available = {m["name"] for m in r.json().get("models", [])}
        needed = {self.model_reasoning, self.model_default}
        missing = needed - available
        if missing:
            raise RuntimeError(
                f"Ollama up but models missing: {sorted(missing)}. "
                + "; ".join(f"ollama pull {m}" for m in sorted(missing))
            )

    def chat_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        force_json: bool,
        model_hint: str,
    ) -> dict[str, Any]:
        full_messages = [{"role": "system", "content": system}, *messages]
        model = self.model_for(model_hint)
        payload: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            "stream": False,
            "keep_alive": "30m",
            "think": False,
            "options": {"temperature": 0.2, "num_ctx": 16384},
        }
        if tools and not force_json:
            payload["tools"] = tools
        if force_json:
            payload["format"] = "json"

        exc = self._requests.exceptions
        t0 = time.perf_counter()
        try:
            r = self._requests.post(f"{self.host}/api/chat", json=payload, timeout=900)
            r.raise_for_status()
        except exc.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status and status >= 500:        # server-side → retryable
                raise TransientProviderError(f"ollama HTTP {status}") from e
            raise                                 # 4xx → fatal
        except (exc.Timeout, exc.ConnectionError) as e:
            raise TransientProviderError(f"ollama transport: {e}") from e
        latency_ms = int((time.perf_counter() - t0) * 1000)
        body = r.json()
        msg = body.get("message") or {}
        prompt_tokens = int(body.get("prompt_eval_count") or 0)
        completion_tokens = int(body.get("eval_count") or 0)
        return {
            "content": msg.get("content", "") or "",
            "tool_calls": [
                {
                    "id": tc.get("id") or f"call_{i}",
                    "name": (tc.get("function") or {}).get("name", ""),
                    "arguments": _coerce_args((tc.get("function") or {}).get("arguments")),
                }
                for i, tc in enumerate(msg.get("tool_calls") or [])
            ],
            "usage": {
                "provider": self.name,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_for(self.name, model, prompt_tokens, completion_tokens),
                "latency_ms": latency_ms,
            },
        }


# ----- Anthropic -----------------------------------------------------------

class AnthropicProvider(Provider):
    name = "anthropic"

    # Anthropic model selection — Opus 4.7 for reasoning, Sonnet 4.6 otherwise.
    MODEL_REASONING = os.environ.get("ANTHROPIC_MODEL_REASONING", "claude-opus-4-7")
    MODEL_DEFAULT = os.environ.get("ANTHROPIC_MODEL_DEFAULT", "claude-sonnet-4-6")

    def __init__(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it or use --provider ollama."
            )
        import anthropic  # noqa: PLC0415  (optional dep)
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()

    def model_for(self, hint: str) -> str:
        return self.MODEL_REASONING if hint == "reasoning-strong" else self.MODEL_DEFAULT

    def health_check(self) -> None:
        # The SDK validates the key on first call; we don't pre-flight to
        # avoid a wasted billable token.
        return

    def chat_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        force_json: bool,
        model_hint: str,
    ) -> dict[str, Any]:
        # Convert OpenAI-style tool history to Anthropic shape.
        anth_messages = self._convert_messages(messages)
        anth_tools = self._convert_tools(tools or [])

        model = self.model_for(model_hint)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 8000,
            "system": [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": anth_messages,
        }
        if anth_tools and not force_json:
            kwargs["tools"] = anth_tools

        t0 = time.perf_counter()
        resp = self.client.messages.create(**kwargs)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        tool_calls: list[dict] = []
        text_parts: list[str] = []
        for block in resp.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input or {},
                })
            elif block.type == "text":
                text_parts.append(block.text)

        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return {
            "content": "".join(text_parts),
            "tool_calls": tool_calls,
            "usage": {
                "provider": self.name,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_for(self.name, model, prompt_tokens, completion_tokens),
                "latency_ms": latency_ms,
            },
        }

    @staticmethod
    def _convert_tools(openai_tools: list[dict]) -> list[dict]:
        out = []
        for t in openai_tools:
            fn = t.get("function") or t
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    @staticmethod
    def _convert_messages(messages: list[dict]) -> list[dict]:
        """Convert OpenAI-style {role: tool, name, content} history into
        Anthropic's tool_result block format."""
        out: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "user":
                out.append({"role": "user", "content": m.get("content", "")})
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or tc
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or "call_0",
                        "name": fn.get("name", ""),
                        "input": args or {},
                    })
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                # Map back to a user-turn tool_result. tool_use_id must match
                # the matching tool_use block's id; we expect callers to keep
                # ids paired by appending in order.
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_use_id") or "call_0",
                        "content": m.get("content", ""),
                    }],
                })
        return out


# ----- Factory -------------------------------------------------------------

def get_provider(name: str | None = None) -> Provider:
    name = (name or os.environ.get("ROBOPORT_PROVIDER") or "ollama").lower()
    if name == "ollama":
        return OllamaProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"unknown provider: {name!r} (expected: ollama|anthropic)")


def _coerce_args(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return {}
