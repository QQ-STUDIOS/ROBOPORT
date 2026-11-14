"""The environment_investigation crew has a complete Phase-5 proof set."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import governance

PROOF = json.loads((REPO / "evals" / "environment_investigation_proof.json").read_text(encoding="utf-8"))
SMOKE = REPO / "evals" / "environment_investigation.json"


def test_proof_set_targets_the_crew():
    registry = json.loads((REPO / "agents" / "registry.json").read_text(encoding="utf-8"))
    assert PROOF["target"] == "environment_investigation"
    assert PROOF["target"] in registry["crews"]


def test_proof_set_covers_all_four_kinds():
    kinds = sorted(e["kind"] for e in PROOF["evals"])
    assert kinds == sorted(governance.REQUIRED_EVAL_KINDS)
    assert governance.check_kind_coverage(PROOF) == []


def test_every_blocker_is_an_expectation():
    for e in PROOF["evals"]:
        assert e["blockers"], e["id"]
        assert set(e["blockers"]) <= set(e["expectations"]), e["id"]


def test_ids_do_not_collide_with_the_smoke_eval():
    smoke_ids = {e["id"] for e in json.loads(SMOKE.read_text(encoding="utf-8"))["evals"]}
    assert smoke_ids.isdisjoint({e["id"] for e in PROOF["evals"]})


def test_fixtures_exist_and_empty_fixture_has_no_source():
    for e in PROOF["evals"]:
        assert (REPO / e["context"]["project_path"]).is_dir(), e["id"]
    empty = REPO / "tests" / "fixtures" / "rea_empty_project"
    assert not [p for p in empty.rglob("*") if p.suffix in {".js", ".mjs", ".cjs", ".ts"}]


def test_both_fixtures_are_approved_rea_roots():
    import yaml
    cfg = yaml.safe_load((REPO / "config" / "agent_config.yaml").read_text(encoding="utf-8"))
    roots = cfg["rea"]["approved_roots"]
    for e in PROOF["evals"]:
        assert e["context"]["project_path"] in roots, e["id"]
    deploy = (REPO / "scripts" / "windows_deploy.py").read_text(encoding="utf-8")
    assert "'rea_project', 'rea_empty_project'" in deploy


def test_eval_203_has_offline_fault_scenarios():
    faults = json.loads((REPO / "evals" / "fault_injection.json").read_text(encoding="utf-8"))
    ids = {s["id"] for s in faults["scenarios"]}
    assert {"ei203_repair_persists", "ei203_repair_invented_hash", "ei203_invented_hash_first"} <= ids
