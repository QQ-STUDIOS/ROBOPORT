"""Phase 3 — prove the error stack fires, offline, via the FaultProvider.

Each test injects a fault on the Provider seam and asserts the executor's
documented behavior: retry at the call layer, repair on schema-invalid output,
fail loudly on exhaustion, and never pass on a quiet-200 empty result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))
from roboport_runtime import executor  # noqa: E402
from fault_provider import FaultProvider  # noqa: E402

REGISTRY = {"agents": {"tester": {"path": "agents/core/planner.md",
                                  "model_hint": "any", "deterministic": False}}}

VALID = json.dumps({
    "status": "ok",
    "output": {"generated_at": "t", "summary": {"total_jobs": 3, "verdicts": {"apply": 2}},
               "ranked_matches": []},
    "criteria_results": [{"criterion": "ok", "passed": True}],
    "error": None,
})
# missing required `summary` -> fails FinalReport schema
INVALID = json.dumps({
    "status": "ok",
    "output": {"generated_at": "t", "ranked_matches": []},
    "criteria_results": [], "error": None,
})


TOOL_CALL = [{"id": "c0", "name": "x", "arguments": {}}]


def _run(monkeypatch, script, *, output_type="FinalReport", config=None):
    fp = FaultProvider(script)
    monkeypatch.setattr(executor, "provider", lambda: fp)
    monkeypatch.setattr(executor, "load_agent_spec", lambda *_: "spec")
    monkeypatch.setattr(executor, "_agent_tools_for", lambda *_: [])
    if config is not None:
        monkeypatch.setattr(executor, "_agent_config", lambda: config)
    step = {"id": "s1", "owner": "tester", "wave": 0, "input": {},
            "output_type": output_type, "success_criteria": ["produce output"]}
    return executor.call_executor(step, {}, REGISTRY), fp


def _failed_criteria(result):
    return [c for c in result["criteria_results"] if not c["passed"]]


def test_transient_then_success_retries(monkeypatch):
    """Layer 1: a transient 5xx is retried, then the step succeeds."""
    result, _ = _run(monkeypatch, [{"transient": True}, {"content": VALID}])
    assert result["status"] == "ok"
    assert result["retries"] == 1
    assert not _failed_criteria(result)


def test_persistent_transient_fails_loudly(monkeypatch):
    """Layer 1 exhausted: persistent 5xx fails with the call-level layer named."""
    result, _ = _run(monkeypatch, [{"transient": True}])  # repeats
    assert result["status"] == "failed"
    assert result["layer"] == "provider_5xx"
    assert "transient" in result["error"]


def test_fatal_provider_error_fails(monkeypatch):
    """A non-retryable provider error fails immediately (not retried)."""
    result, fp = _run(monkeypatch, [{"fatal": True}])
    assert result["status"] == "failed"
    assert result["layer"] == "provider_error"
    assert fp.calls == 1  # no retries on a fatal error


def test_schema_invalid_is_repaired(monkeypatch):
    """Layer 1: schema-invalid output triggers one repair pass that fixes it."""
    result, _ = _run(monkeypatch, [{"content": INVALID}, {"content": VALID}])
    assert result["status"] == "ok"
    assert result["repaired"] is True
    assert not _failed_criteria(result)


def test_schema_invalid_persists_is_flagged(monkeypatch):
    """Repair attempted but output still invalid -> recorded, not silently passed."""
    result, _ = _run(monkeypatch, [{"content": INVALID}, {"content": INVALID}])
    assert result["repaired"] is True
    assert any("schema" in c["criterion"] for c in _failed_criteria(result))


def test_quiet_200_empty_list_is_flagged(monkeypatch):
    """The forbidden quiet-200: an empty search result must fail loudly."""
    empty = json.dumps({"status": "ok", "output": [], "criteria_results": [], "error": None})
    result, _ = _run(monkeypatch, [{"content": empty}], output_type="list[Job]")
    assert any("quiet-200" in c["criterion"] for c in _failed_criteria(result))


def test_llm_budget_exceeded_aborts(monkeypatch):
    """Layer 2: too many LLM calls aborts loudly with layer=budget_exceeded."""
    cfg = {"budgets": {"per_agent": {"max_llm_calls": 2, "max_tool_calls": 50}}}
    result, _ = _run(monkeypatch, [{"tool_calls": TOOL_CALL}], config=cfg)  # loops on tool calls
    assert result["status"] == "failed"
    assert result["layer"] == "budget_exceeded"
    assert "llm calls" in result["error"]


def test_tool_budget_exceeded_aborts(monkeypatch):
    """Layer 2: too many tool calls aborts loudly with layer=budget_exceeded."""
    cfg = {"budgets": {"per_agent": {"max_llm_calls": 10, "max_tool_calls": 1}}}
    result, _ = _run(monkeypatch, [{"tool_calls": TOOL_CALL}], config=cfg)
    assert result["status"] == "failed"
    assert result["layer"] == "budget_exceeded"
    assert "tool calls" in result["error"]


# --- Layer 3: unsafe-action escalation ----------------------------------------

UNSAFE_CALL = [{"id": "u0", "name": "delete_account", "arguments": {"id": 7}}]


def test_unsafe_action_escalates_without_side_effect(monkeypatch):
    """Layer 3: a requested unsafe action escalates loudly and is NEVER dispatched."""
    dispatched: list[str] = []
    monkeypatch.setattr(executor, "dispatch",
                        lambda name, args: dispatched.append(name))
    result, fp = _run(monkeypatch, [{"tool_calls": UNSAFE_CALL}, {"content": VALID}])
    assert result["status"] == "failed"
    assert result["layer"] == "unsafe_action"
    assert result["escalated_action"] == "delete_account"
    assert "escalat" in result["error"].lower()
    assert dispatched == []          # no tool side effect
    assert fp.calls == 1             # aborted on the unsafe request, did not continue


def test_unsafe_policy_overrides_whitelist(monkeypatch):
    """Deny overrides allow: an unsafe action escalates even if it's whitelisted."""
    dispatched: list[str] = []
    monkeypatch.setattr(executor, "dispatch",
                        lambda name, args: dispatched.append(name))
    monkeypatch.setattr(executor, "_agent_tools_for", lambda *_: ["delete_account"])
    result, _ = _run(monkeypatch, [{"tool_calls": UNSAFE_CALL}])
    assert result["layer"] == "unsafe_action"
    assert dispatched == []


def test_safe_whitelist_miss_is_recoverable_not_escalated(monkeypatch):
    """A benign (non-unsafe) tool outside the whitelist feeds an error back so the
    model can self-correct — it does NOT escalate as an unsafe action."""
    safe_call = [{"id": "s0", "name": "lookup_comp_band", "arguments": {}}]
    result, fp = _run(monkeypatch, [{"tool_calls": safe_call}, {"content": VALID}])
    assert result["status"] == "ok"
    assert result.get("layer") != "unsafe_action"
    assert fp.calls == 2  # looped past the not-allowed tool to the final answer


def test_unsafe_policy_disabled_by_empty_list(monkeypatch):
    """An explicit empty `policy.unsafe_actions` disables escalation (opt-out)."""
    cfg = {"policy": {"unsafe_actions": []}}
    result, _ = _run(monkeypatch, [{"tool_calls": UNSAFE_CALL}, {"content": VALID}],
                     config=cfg)
    assert result.get("layer") != "unsafe_action"
    assert result["status"] == "ok"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
