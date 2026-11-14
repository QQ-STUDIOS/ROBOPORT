"""Regression tests for the automated review findings on the upstream PRs (#66-#68)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

from roboport_runtime import budget as budget_mod  # noqa: E402
from roboport_runtime import config as config_mod  # noqa: E402
from roboport_runtime.providers import TransientProviderError  # noqa: E402
from roboport_runtime.routing import RoutingProvider  # noqa: E402


class FakeProvider:
    def __init__(self, name="ollama", fail=None):
        self.name, self.fail, self.calls = name, fail, []
        self.config = {"defaults": {"max_tokens": 16, "context_tokens": 8192}}

    def model_for(self, hint):
        return "qwen3:14b"

    def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return {"content": "{}", "tool_calls": [], "usage": {"cost_usd": 0.0}}


def run_budget():
    return budget_mod.RunBudget({"budgets": {"per_run": {"max_usd": 1.0}}})


def chat(p):
    return budget_mod.governed_chat(p, system="s", messages=[], tools=None, force_json=True, model_hint="any")


def test_a_failed_call_releases_its_cost_reservation():
    b = run_budget()
    with budget_mod.run_budget(b):
        with pytest.raises(RuntimeError):
            chat(FakeProvider(fail=RuntimeError("HTTP 400")))
    assert b.reservations == {} and b.llm_calls == 1


def test_a_call_with_unknown_billing_keeps_its_hold(monkeypatch):
    b = run_budget()
    monkeypatch.setattr(budget_mod, "cost_for", lambda *a: 0.25)
    err = TransientProviderError("timeout")
    err.billing_unknown = True
    with budget_mod.run_budget(b):
        with pytest.raises(TransientProviderError):
            chat(FakeProvider(fail=err))
    assert sum(b.reservations.values()) == 0.25


def test_routing_reserves_and_dispatches_on_the_chosen_backend():
    backend = FakeProvider("ollama")
    policy = {"objective": "static", "constraints": {},
              "candidates": {"any": [{"provider": "ollama", "model": "qwen3:14b"}]}}
    router = RoutingProvider(policy, lambda name: backend)
    b = run_budget()
    with budget_mod.run_budget(b):
        out = chat(router)
    assert backend.calls[0]["model"] == "qwen3:14b"
    assert out["usage"]["routed_provider"] == "ollama" and b.llm_calls == 1


def test_wrapper_provider_keeps_the_configured_routes(monkeypatch):
    monkeypatch.setenv("ROBOPORT_PROVIDER", "routing")
    config = config_mod.load_runtime_config()
    assert config_mod.wrapper_provider() == "routing"
    assert all(r["provider"] in ("ollama", "anthropic") for h, r in config["models"].items() if h != "none")


def test_each_config_scope_gets_its_own_provider(monkeypatch):
    from roboport_runtime import client
    built = []
    monkeypatch.setattr(client, "get_provider", lambda: built.append(object()) or built[-1])
    with config_mod.runtime_config_scope({"models": {}}):
        first = client.provider()
        assert client.provider() is first
    with config_mod.runtime_config_scope({"models": {}}):
        assert client.provider() is not first


def test_a_plan_step_id_cannot_escape_the_run_directory(tmp_path, monkeypatch):
    import benchmark
    assert not benchmark._safe_step_id("../plan") and not benchmark._safe_step_id("/etc/x")
    assert not benchmark._safe_step_id("a..b") and benchmark._safe_step_id("ingest_1")
    monkeypatch.setattr(benchmark, "call_planner", lambda *a, **k: {"steps": [{"id": "../plan", "owner": "x"}]})
    result = benchmark.run_one({"prompt": "p", "target": "crew"}, tmp_path / "run", {})
    assert result["status"] == "failed" and "unsafe step id" in result["error"]
    assert not (tmp_path / "plan.json").exists()


def test_only_blocker_misses_set_blocker_failed(tmp_path, monkeypatch):
    from roboport_runtime import grader
    (tmp_path / 'final_output.json').write_text('{"e":"proof"}')
    rows = [{'expectation_id': 0, 'finding': 'SUPPORTED', 'citations': [{'source': 'final_output', 'quote': 'proof'}]},
            {'expectation_id': 1, 'finding': 'CONTRADICTED', 'citations': [{'source': 'final_output', 'quote': 'proof'}]}]
    monkeypatch.setattr(grader, "load_agent_spec", lambda p: "")
    monkeypatch.setattr(grader, "call_model_json", lambda **k: k["prompt_selected"](k["user_prompt"]) or {"results": rows})
    out = grader.call_grader(["a", "b"], None, tmp_path, blockers=['a'])
    assert out["blocker_failed"] is False and out["pass_rate"] == 0.5
    rows[0]["finding"] = "INSUFFICIENT"
    assert grader.call_grader(["a", "b"], None, tmp_path, blockers=['a'])["blocker_failed"] is True


def test_the_grader_sees_step_outputs(tmp_path, monkeypatch):
    from roboport_runtime import grader
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps/investigate.json").write_text(json.dumps({"status": "ok", "output": {"finding": "f1"}}))
    seen = {}
    monkeypatch.setattr(grader, "load_agent_spec", lambda p: "")
    monkeypatch.setattr(grader, "call_model_json", lambda **k: seen.update(k) or {"results": []})
    grader.call_grader([], None, tmp_path)
    assert '"finding":"f1"' in seen["user_prompt"]


def batch(root, name, summary=True):
    run = root / name / "run1"
    run.mkdir(parents=True)
    (run / "provenance.json").write_text(json.dumps({"status": "ok"}))
    if summary:
        os.utime(run / "provenance.json", (1, 1))
        (root / name / "summary.json").write_text("{}")


def test_pruning_skips_batches_without_a_newer_summary(tmp_path):
    from windows_deploy import prune_runs
    batch(tmp_path, "running", summary=False)
    batch(tmp_path, "done")
    prune_runs(tmp_path, protected=set(), max_runs=0, max_bytes=0)
    assert (tmp_path / "running").exists() and not (tmp_path / "done").exists()


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_reparse_points_are_detected_on_any_python(tmp_path):
    from dashboard.launchpad.artifacts import is_reparse_point
    (tmp_path / "real").mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "link"), str(tmp_path / "real")], check=True, capture_output=True)
    assert is_reparse_point(tmp_path / "link") and not is_reparse_point(tmp_path / "real")


def test_an_unfinished_operation_can_be_resumed(tmp_path):
    from dashboard.launchpad.store import Store
    store = Store(tmp_path / "s.sqlite3")
    command = {"operation_id": "op1", "intent": "start", "mission_id": "m"}
    assert store.claim_operation("u", command)["outcome"]["status"] == "pending"
    again = store.claim_operation("u", command)
    assert again["new"] and again["resumed"]
    store.finish_operation("op1", {"operation_id": "op1", "status": "accepted"})
    assert store.claim_operation("u", command) == {"new": False, "operation_id": "op1",
                                                   "outcome": {"operation_id": "op1", "status": "accepted"}}


def test_a_cancelled_mission_shows_no_running_step(tmp_path):
    from dashboard.launchpad.events import EventStore
    from dashboard.launchpad.store import Store
    store = Store(tmp_path / "s.sqlite3")
    mission = store.create_mission({"owner": "u", "classification": "internal"})
    events = EventStore(store)
    events.ingest(mission["id"], {"event": "step.start", "step_id": "s1", "agent": "a1"})
    assert events.snapshot("env", "network")["data"]["steps"][0]["state"] == "running"
    store.transition(mission["id"], 1, "cancelled", {})
    data = events.snapshot("env", "network")["data"]
    assert data["steps"][0]["state"] == "cancelled" and data["agents"][0]["state"] == "cancelled"


def test_a_rejected_run_does_not_count_its_failures_twice():
    sys.path.insert(0, str(REPO / "dashboard"))
    import bridge
    state = bridge.RunState()
    bridge.translate({"event": "step.failed", "step_id": "s1", "agent": "a1"}, state)
    bridge.translate({"event": "run.complete", "run_summary": {"status": "failed"}}, state)
    assert state.failed_total == 1


def test_the_core_registry_loads_without_the_pack_loader(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "control_surface/collector"))
    import runtime_feed
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents/registry.json").write_text(json.dumps({"agents": {}, "crews": {}}))
    monkeypatch.setattr(runtime_feed, "_repo_registry", lambda: tmp_path / "agents/registry.json")
    monkeypatch.setitem(sys.modules, "station_packs", None)  # importing it would fail
    assert runtime_feed._load_registry(tmp_path / "agents/registry.json") == {"agents": {}, "crews": {}}


# ---- second review round (#69) ----

def test_a_rejection_never_replaces_an_acceptance(tmp_path):
    from dashboard.launchpad.store import Store
    store = Store(tmp_path / "s.sqlite3")
    store.claim_operation("u", {"operation_id": "op", "intent": "start"})
    store.finish_operation("op", {"operation_id": "op", "status": "accepted"})
    out = store.finish_operation("op", {"operation_id": "op", "status": "rejected"})
    assert out["status"] == "accepted"


def test_a_retry_while_the_operation_runs_stays_pending():
    import threading
    from dashboard.launchpad.missions import MissionManager
    manager = object.__new__(MissionManager)
    manager.inflight, manager.lock = set(), threading.RLock()
    command = {"operation_id": "op", "mission_id": "m"}
    inner = manager._exclusive(command, lambda: manager._exclusive(command, lambda: "ran twice"))
    assert inner == {"operation_id": "op", "status": "pending", "mission_id": "m"}
    assert manager.inflight == set()


def test_replayed_event_text_is_escaped():
    sys.path.insert(0, str(REPO / "dashboard"))
    import bridge
    out = bridge.translate({"event": "step.failed", "step_id": "s1", "agent": "a1",
                            "error": '<img src=x onerror="alert(1)">'}, bridge.RunState())
    text = json.dumps(out)
    assert "<img" not in text and "&lt;img" in text


def test_the_ram_allowance_matches_setup():
    from windows_deploy import tier_fits
    spec = {"gpu_vendor": "NVIDIA", "min_vram_mib": 1, "min_ram_gib": 64, "models": ["m"]}
    assert tier_fits(spec, "NVIDIA RTX, 32000 MiB", int(63.5 * 2**30), "m")
    assert not tier_fits(spec, "NVIDIA RTX, 32000 MiB", 60 * 2**30, "m")


def test_declared_blockers_decide_blocker_failed(tmp_path, monkeypatch):
    from roboport_runtime import grader
    (tmp_path / 'final_output.json').write_text('{"e":"proof"}')
    rows = [{'expectation_id': 0, 'finding': 'CONTRADICTED', 'citations': [{'source': 'final_output', 'quote': 'proof'}]}]
    monkeypatch.setattr(grader, "load_agent_spec", lambda p: "")
    monkeypatch.setattr(grader, "call_model_json", lambda **k: k["prompt_selected"](k["user_prompt"]) or {"results": rows})
    assert grader.call_grader(["a"], None, tmp_path)["blocker_failed"] is False
    assert grader.call_grader(["a"], None, tmp_path, blockers=["a"])["blocker_failed"] is True


def test_the_fault_provider_runs_under_a_budget():
    from roboport_runtime.fault_provider import FaultProvider
    b = run_budget()
    with budget_mod.run_budget(b):
        out = chat(FaultProvider([{"content": "{}"}]))
    assert out["content"] == "{}" and b.llm_calls == 1 and b.spent == 0


def test_step_outputs_never_push_grading_past_the_context_ceiling(tmp_path, monkeypatch):
    from roboport_runtime import client, grader
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps/big.json").write_text(json.dumps({"status": "ok", "output": {"text": "x" * 40000}}))
    backend = FakeProvider()
    backend.config = {"defaults": {"context_tokens": 32768, "max_tokens": 2048}}
    def respond(**kwargs):
        backend.calls.append(kwargs)
        return {"content": json.dumps({"results": []})}
    backend.chat_with_tools = respond
    monkeypatch.setattr(grader, "load_agent_spec", lambda p: "")
    monkeypatch.setattr(client, "provider", lambda: backend)
    with config_mod.runtime_config_scope({"defaults": {"context_tokens": 32768, "max_tokens": 2048}}):
        with budget_mod.run_budget(run_budget()):
            grader.call_grader([], None, tmp_path)
        prompt = backend.calls[0]["messages"][0]["content"]
        assert "omitted" in prompt and "x" * 100 not in prompt
