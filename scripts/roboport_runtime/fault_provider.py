"""A deterministic, offline Provider that injects faults on a script.

Implements the runtime's Provider seam so the executor's error-stack policy can
be proven without a real or local model — in unit tests and in the offline
fault-injection benchmark (`scripts/fault_bench.py`, `--provider fault`). Each
chat call consumes the next behavior in the script (the last one repeats):

    {"transient": True}      -> raise TransientProviderError (retryable 5xx)
    {"fatal": True}          -> raise RuntimeError (non-retryable)
    {"content": "<json>"}    -> return that assistant content, no tool calls
    {"tool_calls": [...]}    -> return those tool calls
    {"content": ..., "usage": {...}} -> also surface a Phase-4 telemetry block
"""
from __future__ import annotations

import json
import os

from .providers import Provider, TransientProviderError


class FaultProvider(Provider):
    name = "fault"

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls = 0

    def chat_with_tools(self, *, system, messages, tools, force_json, model_hint,
                        model=None):
        beh = self.script[min(self.calls, len(self.script) - 1)] if self.script else {"content": "{}"}
        self.calls += 1
        if beh.get("transient"):
            raise TransientProviderError("injected transient 5xx")
        if beh.get("fatal"):
            raise RuntimeError("injected fatal error")
        content = beh.get("content", "")
        if not isinstance(content, str):   # allow a nested object in scripts for readability
            content = json.dumps(content)
        out = {"content": content, "tool_calls": beh.get("tool_calls", [])}
        if "usage" in beh:
            out["usage"] = beh["usage"]
        return out

    def health_check(self) -> None:  # offline — nothing to probe
        pass


def fault_provider_from_env() -> FaultProvider:
    """Build a FaultProvider from the JSON script file named by ROBOPORT_FAULT_SCRIPT
    (a list of behaviors), or an empty script when unset."""
    path = os.environ.get("ROBOPORT_FAULT_SCRIPT")
    script: list[dict] = []
    if path:
        try:
            script = json.loads(open(path, encoding="utf-8").read())
        except (OSError, json.JSONDecodeError):
            script = []
    return FaultProvider(script)
