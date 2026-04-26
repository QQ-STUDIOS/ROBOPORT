"""Local-Ollama runtime for ROBOPORT.

Drop-in replacement for the stubs in scripts/benchmark.py. Calls a local
Ollama server (default http://localhost:11434) via raw HTTP. Pass --live to
benchmark.py to swap stubs for this runtime.

Configuration (env):
  OLLAMA_HOST              base URL (default: http://localhost:11434)
  OLLAMA_MODEL_REASONING   model for reasoning-strong agents (default: qwen3.5:latest)
  OLLAMA_MODEL_DEFAULT     model for all other agents (default: gemma4:latest)
"""
from __future__ import annotations

from .planner import call_planner
from .executor import call_executor
from .grader import call_grader

__all__ = ["call_planner", "call_executor", "call_grader"]
