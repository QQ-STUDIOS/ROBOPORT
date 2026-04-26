"""Anthropic-SDK-backed runtime for ROBOPORT.

Drop-in replacement for the stubs in scripts/benchmark.py. To use a real model
loop, pass --live to benchmark.py (or import call_planner / call_executor /
call_grader directly).

Requires:
  pip install anthropic>=0.45
  export ANTHROPIC_API_KEY=...
"""
from __future__ import annotations

from .planner import call_planner
from .executor import call_executor
from .grader import call_grader

__all__ = ["call_planner", "call_executor", "call_grader"]
