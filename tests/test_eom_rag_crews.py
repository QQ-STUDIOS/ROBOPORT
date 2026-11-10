"""Frontier 4b — the LegacyRAG and EOM-governance modeled crews.

LegacyRAG (PHI) is an offline retrieval->report flow whose crux is anti-hallucination
(no ungrounded answer, no citation to a non-existent doc). The EOM-governance crew
(auditor -> decision -> catalogue_steward) ends in a side-effecting Automation agent,
so its catalogue writes exercise the Phase-3 Layer-3 unsafe-action escalation — proposed
and escalated, never performed. All offline.
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

def test_legacyrag_crew_registered():
    crew = REGISTRY["crews"]["legacyrag_crew"]
    assert crew["entry"] == "theragen_legacyrag_retrieval"
    assert crew["classification"] == "PHI"
    assert crew["edges"] == [{"from": "theragen_legacyrag_retrieval", "to": "reporting",
                              "type": "RetrievalResult"}]
    assert "theragen_legacyrag_retrieval" in REGISTRY["theragen_agents"]


def test_eom_governance_crew_is_a_three_station_chain():
    crew = REGISTRY["crews"]["eom_governance_crew"]
    assert crew["entry"] == "theragen_eom_governance_auditor"
    chain = [(e["from"], e["to"], e["type"]) for e in crew["edges"]]
    assert chain == [
        ("theragen_eom_governance_auditor", "theragen_eom_decision_backlog", "GovernanceFindings"),
        ("theragen_eom_decision_backlog", "theragen_eom_catalogue_steward", "DecisionLog"),
    ]
    # the terminal station is a side-effecting Automation catalog agent
    steward = REGISTRY["theragen_agents"]["theragen_eom_catalogue_steward"]
    assert steward["role"] == "theragen.automation"


def test_governance_clean_with_both_crews():
    eval_sets = governance.discover_eval_sets(REPO / "evals")
    targets = {es.get("target") for es in eval_sets}
    assert {"legacyrag_crew", "eom_governance_crew"} <= targets
    report = governance.run_governance(eval_sets, REGISTRY)
    assert report["ok"], report["violations"]


# --- typed contracts ---------------------------------------------------------

def _schema(defn: str) -> dict:
    return {"$ref": f"#/definitions/{defn}", "definitions": SCHEMA["definitions"]}


def test_retrieval_result_contract():
    good = {"query": "q", "answer": "a", "grounded": True,
            "citations": [{"doc_id": "d1", "snippet": "s", "score": 0.9}]}
    jsonschema.validate(good, _schema("RetrievalResult"))
    # ungrounded-with-no-citations is a valid shape (the "not found" answer)
    jsonschema.validate({"query": "q", "answer": "not found", "grounded": False, "citations": []},
                        _schema("RetrievalResult"))
    bad = {"query": "q", "answer": "a", "grounded": True, "citations": [{"snippet": "s"}]}
    with pytest.raises(jsonschema.ValidationError):      # citation missing doc_id
        jsonschema.validate(bad, _schema("RetrievalResult"))


def test_decision_log_enum_is_closed():
    good = {"generated_at": "2026-11-10T00:00:00Z",
            "decisions": [{"finding_id": "f1", "decision": "escalate", "rationale": "r"}]}
    jsonschema.validate(good, _schema("DecisionLog"))
    bad = json.loads(json.dumps(good))
    bad["decisions"][0]["decision"] = "ignore"           # outside the enum
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _schema("DecisionLog"))


def test_governance_findings_contract():
    good = {"generated_at": "2026-11-10T00:00:00Z",
            "findings": [{"id": "g1", "rule": "R-EOM-1", "severity": "high",
                          "subject": "catalogue entry 42", "evidence": "missing owner"}]}
    jsonschema.validate(good, _schema("GovernanceFindings"))
    bad = json.loads(json.dumps(good))
    bad["findings"][0]["severity"] = "urgent"            # outside the enum
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _schema("GovernanceFindings"))


# --- the EOM steward's unsafe-action escalation ------------------------------

def _run(monkeypatch, script, *, owner, output_type):
    fp = FaultProvider(script)
    monkeypatch.setattr(executor, "provider", lambda: fp)
    monkeypatch.setattr(executor, "load_agent_spec", lambda *_: "spec")
    monkeypatch.setattr(executor, "_agent_tools_for", lambda *_: [])
    step = {"id": "s", "owner": owner, "wave": 2, "input": {},
            "output_type": output_type, "success_criteria": ["act"]}
    reg = {"agents": {owner: {"path": "agents/theragen/eom_catalogue_steward.md",
                              "model_hint": "tool-use-capable", "deterministic": False}}}
    return executor.call_executor(step, {}, reg), fp


def test_steward_catalogue_write_escalates_not_executed(monkeypatch):
    """The terminal EOM steward proposing a catalogue write is blocked-and-escalated by
    Layer-3 BEFORE dispatch — the governance loop cannot mutate the catalogue."""
    result, fp = _run(monkeypatch, [{"tool_calls": [
        {"id": "w0", "name": "update_catalogue_entry", "arguments": {"id": 42, "owner": "x"}}]}],
        owner="theragen_eom_catalogue_steward", output_type="ActionProposal")
    assert result["status"] == "failed"
    assert result["layer"] == "unsafe_action"
    assert result["escalated_action"] == "update_catalogue_entry"
    assert fp.calls == 1                                  # returned before any dispatch


def test_steward_delete_escalates(monkeypatch):
    result, _ = _run(monkeypatch, [{"tool_calls": [
        {"id": "w0", "name": "delete_catalogue_entry", "arguments": {"id": 7}}]}],
        owner="theragen_eom_catalogue_steward", output_type="ActionProposal")
    assert result["status"] == "failed"
    assert result["layer"] == "unsafe_action"
    assert result["escalated_action"] == "delete_catalogue_entry"


def test_eom_eval_pins_escalation_blocker():
    es = next(es for es in governance.discover_eval_sets(REPO / "evals")
              if es.get("target") == "eom_governance_crew")
    blockers = " ".join(b for e in es["evals"] for b in e.get("blockers", []))
    assert "unsafe_action" in blockers or "escalat" in blockers.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
