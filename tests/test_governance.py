"""Phase 5 — prove the crew-expansion governance gate, offline."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import governance  # noqa: E402


def _eval(id, blockers=None, kind=None):
    e = {"id": id, "prompt": "p", "expectations": ["x"]}
    if blockers is not None:
        e["blockers"] = blockers
    if kind is not None:
        e["kind"] = kind
    return e


def _eval_set(evals, target="jd_crew"):
    return {"target": target, "evals": evals}


# --- R1: blockers ------------------------------------------------------------

def test_r1_flags_eval_without_blocker():
    es = _eval_set([_eval(1, blockers=["b"], kind="happy_path"), _eval(2, kind="empty")])
    v = governance.check_blockers(es)
    assert len(v) == 1 and v[0]["rule"] == "R1"
    assert "eval-2" in v[0]["subject"]


def test_r1_clean_when_all_have_blockers():
    es = _eval_set([_eval(1, blockers=["b"]), _eval(2, blockers=["c"])])
    assert governance.check_blockers(es) == []


# --- R2: kind coverage -------------------------------------------------------

def test_r2_flags_missing_kinds():
    es = _eval_set([_eval(1, kind="happy_path"), _eval(2, kind="empty")])
    v = governance.check_kind_coverage(es)
    missing = {x["subject"].split(":")[1] for x in v}
    assert missing == {"schema_failure", "evidence_grounding"}
    assert all(x["rule"] == "R2" for x in v)


def test_r2_clean_when_all_kinds_present():
    es = _eval_set([_eval(i, kind=k) for i, k in enumerate(governance.REQUIRED_EVAL_KINDS, 1)])
    assert governance.check_kind_coverage(es) == []


# --- R4: stations / contracts ------------------------------------------------

def test_r4_flags_unregistered_station():
    reg = {"agents": {"job_scout": {}, "synth": {}},
           "crews": {"c": {"entry": "job_scout",
                           "edges": [{"from": "job_scout", "to": "ghost", "type": "list[Job]"}]}}}
    v = governance.check_stations(reg)
    assert any(x["rule"] == "R4" and "ghost" in x["subject"]
               and "not a registered agent" in x["message"] for x in v)


def test_r4_flags_edge_without_contract():
    reg = {"agents": {"a": {}, "b": {}},
           "crews": {"c": {"entry": "a", "edges": [{"from": "a", "to": "b"}]}}}
    v = governance.check_stations(reg)
    assert any(x["rule"] == "R4" and "output contract" in x["message"] for x in v)


def test_r4_clean_registry():
    reg = {"agents": {"a": {}, "b": {}},
           "crews": {"c": {"entry": "a", "edges": [{"from": "a", "to": "b", "type": "T"}]}}}
    assert governance.check_stations(reg) == []


# --- orchestration + rule subset ---------------------------------------------

def test_run_governance_aggregates_and_flags():
    es = _eval_set([_eval(1, kind="happy_path")])  # no blocker, missing 3 kinds
    reg = {"agents": {}, "crews": {}}
    report = governance.run_governance([es], reg)
    assert report["ok"] is False
    assert report["counts_by_rule"]["R1"] == 1
    assert report["counts_by_rule"]["R2"] == 3


def test_run_governance_rule_subset_skips_others():
    es = _eval_set([_eval(1, kind="happy_path")])  # would fail R1 and R2
    report = governance.run_governance([es], {"agents": {}, "crews": {}}, rules=("R4",))
    assert report["ok"] is True  # only R4 ran, and there are no crews


# --- crew coverage: every registry crew needs an eval set --------------------

def test_crew_without_eval_set_is_flagged():
    registry = {"agents": {"a": {}}, "crews": {"jd_crew": {"entry": "a", "edges": []},
                                               "ghost_crew": {"entry": "a", "edges": []}}}
    v = governance.check_crews_have_evals(registry, [_eval_set([], target="jd_crew")])
    assert len(v) == 1 and v[0]["rule"] == "R2" and v[0]["subject"] == "ghost_crew"


def test_crew_coverage_clean_when_all_crews_have_sets():
    registry = {"agents": {}, "crews": {"jd_crew": {"entry": "a", "edges": []}}}
    assert governance.check_crews_have_evals(registry, [_eval_set([], target="jd_crew")]) == []


def test_discover_eval_sets_skips_non_eval_sets(tmp_path):
    (tmp_path / "set.json").write_text(json.dumps({"target": "c", "evals": [_eval(1)]}))
    (tmp_path / "faults.json").write_text(json.dumps({"target": "x", "scenarios": []}))
    (tmp_path / "junk.json").write_text("{ not json")
    found = governance.discover_eval_sets(tmp_path)
    assert len(found) == 1 and found[0]["target"] == "c"


# --- real-repo integration: the gate must stay green for every crew ----------

def test_repo_is_governance_clean():
    eval_sets = governance.discover_eval_sets(REPO / "evals")
    registry = json.loads((REPO / "agents" / "registry.json").read_text())
    report = governance.run_governance(eval_sets, registry)
    assert report["ok"], report["violations"]


def test_both_crews_are_covered():
    eval_sets = governance.discover_eval_sets(REPO / "evals")
    registry = json.loads((REPO / "agents" / "registry.json").read_text())
    assert {"jd_crew", "dataops_crew"} <= set(registry["crews"])
    assert governance.check_crews_have_evals(registry, eval_sets) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
