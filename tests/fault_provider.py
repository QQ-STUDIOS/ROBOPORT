"""A deterministic, offline Provider that injects faults on a script.

Implements the runtime's Provider seam so the executor's error-stack policy can
be proven without a real or local model. Each chat call consumes the next
behavior in the script (the last one repeats):

    {"transient": True}      -> raise TransientProviderError (retryable 5xx)
    {"fatal": True}          -> raise RuntimeError (non-retryable)
    {"content": "<json>"}    -> return that assistant content, no tool calls
    {"content": ..., "usage": {...}} -> also surface a Phase-4 telemetry block
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from roboport_runtime.providers import Provider, TransientProviderError  # noqa: E402


class FaultProvider(Provider):
    name = "fault"

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls = 0

    def chat_with_tools(self, *, system, messages, tools, force_json, model_hint):
        beh = self.script[min(self.calls, len(self.script) - 1)] if self.script else {"content": "{}"}
        self.calls += 1
        if beh.get("transient"):
            raise TransientProviderError("injected transient 5xx")
        if beh.get("fatal"):
            raise RuntimeError("injected fatal error")
        out = {"content": beh.get("content", ""), "tool_calls": beh.get("tool_calls", [])}
        if "usage" in beh:
            out["usage"] = beh["usage"]
        return out

    def health_check(self) -> None:  # pragma: no cover - unused in tests
        pass
