"""Frontier 2 — the Insight-to-Action crew (data_engineering -> reporting ->
automation), proven offline.

The terminal `automation` station proposes side-effecting actions and escalates
every unsafe one through the Phase-3 Layer-3 policy — it never executes. These
tests prove the crew is registered and governed, the ActionProposal contract
holds (an unaudited proposal is rejected), and a proposed unsafe tool call is
blocked-and-escalated by the executor BEFORE any dispatch.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))
import governance  # noqa: E402
from roboport_runtime import executor  # noqa: E402
from fault_provider import FaultProvider  # noqa: E402

REGISTRY = json.loads((REPO / "agents" / "registry.json").read_text())
SCHEMA = json.loads((REPO / "resources" / "schemas" / "output.schema.json").read_text())

jsonschema = pytest.importorskip("jsonschema")


# --- registry / governance ---------------------------------------------------

def test_crew_registered_with_typed_edges():
    crew = REGISTRY["crews"]["insight_to_action_crew"]
    assert crew["entry"] == "data_engineering"
    edges = [(e["from"], e["to"], e["type"]) for e in crew["edges"]]
    assert edges == [
        ("data_engineering", "reporting", "CleanedDataset"),
        ("reporting", "automation", "DataReport"),
    ]
    # every station maps to a registered agent (R4)
    agents = set(REGISTRY["agents"])
    stations = {crew["entry"]} | {e["from"] for e in crew["edges"]} | {e["to"] for e in crew["edges"]}
    assert stations <= agents
    assert "automation" in stations            # the side-effecting terminal station


def test_governance_clean_with_insight_to_action_crew():
    eval_sets = governance.discover_eval_sets(REPO / "evals")
    targets = {es.get("target") for es in eval_sets}
    assert "insight_to_action_crew" in targets  # its proof set is discovered
    report = governance.run_governance(eval_sets, REGISTRY)
    assert report["ok"], report["violations"]


def test_eval_set_covers_four_kinds_with_escalation_blocker():
    es = next(es for es in governance.discover_eval_sets(REPO / "evals")
              if es.get("target") == "insight_to_action_crew")
    kinds = {e["kind"] for e in es["evals"]}
    assert kinds == {"happy_path", "empty", "schema_failure", "evidence_grounding"}
    # the crux of this crew: at least one blocker pins the unsafe-action escalation
    all_blockers = " ".join(b for e in es["evals"] for b in e.get("blockers", []))
    assert "unsafe_action" in all_blockers or "escalate" in all_blockers.lower()


# --- ActionProposal contract -------------------------------------------------

def _action_proposal_schema() -> dict:
    return {"$ref": "#/definitions/ActionProposal", "definitions": SCHEMA["definitions"]}


def _valid_proposal() -> dict:
    return {
        "generated_at": "2026-11-10T00:00:00Z",
        "source_report": {"title": "Weekly Sales", "source_row_count": 128},
        "proposed_actions": [{
            "tool": "send_email",
            "args": {"to": "lead@example.com", "subject": "Weekly summary"},
            "requires_approval": True,
            "rationale": "Trends section shows a 12% WoW drop worth flagging.",
            "idempotency_key": "wk-2026-45-email",
            "grounded_in": "Trends",
        }],
        "audit_event": {"actor": "automation", "action": "propose", "target": "send_email",
                        "ts": "2026-11-10T00:00:00Z"},
        "approval": {"status": "pending", "token": None, "escalated_to": "operator"},
    }


def test_action_proposal_accepts_well_formed():
    jsonschema.validate(_valid_proposal(), _action_proposal_schema())


def test_action_proposal_rejects_unaudited():
    bad = _valid_proposal()
    del bad["audit_event"]                      # an unaudited proposal must not validate
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _action_proposal_schema())


def test_empty_action_list_is_valid():
    # "the insight warrants no action" is a legitimate, auditable outcome
    p = _valid_proposal()
    p["proposed_actions"] = []
    jsonschema.validate(p, _action_proposal_schema())


# --- the crew-level unsafe-action escalation ---------------------------------

def _run(monkeypatch, script, *, output_type="ActionProposal", config=None):
    fp = FaultProvider(script)
    monkeypatch.setattr(executor, "provider", lambda: fp)
    monkeypatch.setattr(executor, "load_agent_spec", lambda *_: "spec")
    monkeypatch.setattr(executor, "_agent_tools_for", lambda *_: [])
    if config is not None:
        monkeypatch.setattr(executor, "_agent_config", lambda: config)
    step = {"id": "act", "owner": "automation", "wave": 2, "input": {},
            "output_type": output_type, "success_criteria": ["propose actions"]}
    reg = {"agents": {"automation": {"path": "agents/domain/automation.md",
                                     "model_hint": "tool-use-capable", "deterministic": False}}}
    return executor.call_executor(step, {}, reg), fp


def test_proposed_unsafe_action_escalates_not_executed(monkeypatch):
    """automation proposing a side-effecting tool call is blocked-and-escalated by
    Layer-3 BEFORE dispatch — proving the terminal station of the crew cannot act."""
    result, fp = _run(monkeypatch, [{"tool_calls": [
        {"id": "a0", "name": "send_email", "arguments": {"to": "lead@example.com"}}]}])
    assert result["status"] == "failed"
    assert result["layer"] == "unsafe_action"
    assert result["escalated_action"] == "send_email"
    assert fp.calls == 1                        # returned before any second round / dispatch


def test_proposed_destructive_action_escalates(monkeypatch):
    """A destructive proposal (delete_*) is escalated too — deny overrides any whitelist."""
    result, _ = _run(monkeypatch, [{"tool_calls": [
        {"id": "a0", "name": "delete_customer_record", "arguments": {"id": 7}}]}])
    assert result["status"] == "failed"
    assert result["layer"] == "unsafe_action"
    assert result["escalated_action"] == "delete_customer_record"


def test_well_formed_proposal_passes(monkeypatch):
    """A read-only ActionProposal that proposes-but-does-not-dispatch validates and passes."""
    content = json.dumps({
        "status": "ok",
        "output": _valid_proposal(),
        "criteria_results": [{"criterion": "actions grounded + escalated", "passed": True}],
        "error": None,
    })
    result, _ = _run(monkeypatch, [{"content": content}])
    assert result["status"] == "ok"
    assert [c for c in result["criteria_results"] if not c["passed"]] == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
