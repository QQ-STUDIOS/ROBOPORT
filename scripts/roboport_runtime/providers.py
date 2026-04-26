"""Provider backends for the ROBOPORT runtime.

Two implementations:
  - OllamaProvider   — local server via raw HTTP /api/chat
  - AnthropicProvider — Anthropic SDK with prompt caching + tool use

Selected via the ROBOPORT_PROVIDER env var (default: ollama).
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Protocol


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
        payload: dict[str, Any] = {
            "model": self.model_for(model_hint),
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

        r = self._requests.post(f"{self.host}/api/chat", json=payload, timeout=900)
        r.raise_for_status()
        body = r.json()
        msg = body.get("message") or {}
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

        kwargs: dict[str, Any] = {
            "model": self.model_for(model_hint),
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

        resp = self.client.messages.create(**kwargs)

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

        return {"content": "".join(text_parts), "tool_calls": tool_calls}

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
