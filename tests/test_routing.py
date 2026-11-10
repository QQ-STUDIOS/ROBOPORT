"""Phase 4 task 4 — prove the routing decision engine and provider, offline."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from roboport_runtime import routing  # noqa: E402
from roboport_runtime.providers import Provider  # noqa: E402

_DEFAULT_CANDS = {
    "reasoning-strong": [
        {"provider": "ollama", "model": "qwen3:14b"},
        {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    ]
}


def _policy(objective="blocker_pass_then_cost", constraints=None, candidates=None):
    return {
        "objective": objective,
        "constraints": constraints or {},
        "candidates": _DEFAULT_CANDS if candidates is None else candidates,
    }


# --- policy + scoreboard parsing ---------------------------------------------

def test_load_routing_policy():
    cfg = {"routing": {"objective": "static",
                       "constraints": {"max_latency_ms_per_step": 5000},
                       "candidates": {"h": [{"provider": "ollama", "model": "m"}]}}}
    pol = routing.load_routing_policy(cfg)
    assert pol["objective"] == "static"
    assert pol["constraints"]["max_latency_ms_per_step"] == 5000
    assert pol["candidates"]["h"] == [{"provider": "ollama", "model": "m"}]


def test_load_routing_policy_empty():
    pol = routing.load_routing_policy({})
    assert pol["candidates"] == {} and pol["objective"] == "blocker_pass_then_cost"


def test_scoreboard_from_report():
    rows = [{"provider": "anthropic", "model": "claude-sonnet-4-6", "steps": 4,
             "latency_ms": 800, "cost_usd": 0.04, "blocker_pass_rate": 0.9}]
    sb = routing.scoreboard_from_report(rows)
    e = sb[("anthropic", "claude-sonnet-4-6")]
    assert e["blocker_pass_rate"] == 0.9
    assert e["cost_per_step"] == 0.01
    assert e["latency_per_step"] == 200.0


# --- select_candidate --------------------------------------------------------

def test_no_candidates_returns_none():
    d = routing.select_candidate("missing", _policy(candidates={}))
    assert d["chosen"] is None and "no candidates" in d["reason"]


def test_static_order_without_scoreboard():
    d = routing.select_candidate("reasoning-strong", _policy())
    assert d["chosen"] == {"provider": "ollama", "model": "qwen3:14b"}
    assert "static" in d["reason"]


def test_static_objective_ignores_scoreboard():
    sb = {("anthropic", "claude-sonnet-4-6"): {"blocker_pass_rate": 1.0,
                                               "cost_per_step": 0.001, "latency_per_step": 10}}
    d = routing.select_candidate("reasoning-strong", _policy(objective="static"), sb)
    assert d["chosen"]["provider"] == "ollama"  # first, telemetry ignored


def test_ranks_by_blocker_pass_rate():
    sb = {
        ("ollama", "qwen3:14b"): {"blocker_pass_rate": 0.6, "cost_per_step": 0.0, "latency_per_step": 100},
        ("anthropic", "claude-sonnet-4-6"): {"blocker_pass_rate": 0.95, "cost_per_step": 0.02, "latency_per_step": 300},
    }
    d = routing.select_candidate("reasoning-strong", _policy(), sb)
    assert d["chosen"]["provider"] == "anthropic"  # higher pass rate wins over cheaper


def test_tiebreak_by_cost():
    sb = {
        ("ollama", "qwen3:14b"): {"blocker_pass_rate": 0.9, "cost_per_step": 0.0, "latency_per_step": 100},
        ("anthropic", "claude-sonnet-4-6"): {"blocker_pass_rate": 0.9, "cost_per_step": 0.02, "latency_per_step": 100},
    }
    d = routing.select_candidate("reasoning-strong", _policy(), sb)
    assert d["chosen"]["provider"] == "ollama"  # equal pass rate -> cheaper wins


def test_latency_constraint_drops_slow_candidate():
    pol = _policy(constraints={"max_latency_ms_per_step": 250})
    sb = {
        ("ollama", "qwen3:14b"): {"blocker_pass_rate": 0.7, "cost_per_step": 0.0, "latency_per_step": 100},
        ("anthropic", "claude-sonnet-4-6"): {"blocker_pass_rate": 0.99, "cost_per_step": 0.02, "latency_per_step": 900},
    }
    d = routing.select_candidate("reasoning-strong", pol, sb)
    # anthropic has the best pass rate but blows the latency budget -> ollama.
    assert d["chosen"]["provider"] == "ollama"
    assert "dropped on latency" in d["reason"]


# --- RoutingProvider ---------------------------------------------------------

class _Fake(Provider):
    def __init__(self, name):
        self.name = name
        self.calls = []

    def chat_with_tools(self, *, system, messages, tools, force_json, model_hint, model=None):
        self.calls.append(model)
        return {"content": "{}", "tool_calls": [],
                "usage": {"provider": self.name, "model": model, "prompt_tokens": 1,
                          "completion_tokens": 1, "cost_usd": 0.0, "latency_ms": 5}}

    def health_check(self):
        pass


def test_routing_provider_delegates_and_pins_model():
    made = {}

    def factory(name):
        made[name] = made.get(name) or _Fake(name)
        return made[name]

    rp = routing.RoutingProvider(_policy(), factory)
    out = rp.chat_with_tools(system="s", messages=[], tools=None,
                             force_json=True, model_hint="reasoning-strong")
    # Static order -> ollama, model pinned to the candidate's model.
    assert made["ollama"].calls == ["qwen3:14b"]
    assert "anthropic" not in made          # lazily constructed only what's used
    assert out["usage"]["routed_provider"] == "ollama"
    assert out["usage"]["routed_model"] == "qwen3:14b"
    assert len(rp.decisions) == 1


def test_routing_provider_follows_scoreboard():
    made = {}
    rp = routing.RoutingProvider(
        _policy(), lambda n: made.setdefault(n, _Fake(n)),
        scoreboard={("anthropic", "claude-sonnet-4-6"):
                    {"blocker_pass_rate": 1.0, "cost_per_step": 0.02, "latency_per_step": 200},
                    ("ollama", "qwen3:14b"):
                    {"blocker_pass_rate": 0.5, "cost_per_step": 0.0, "latency_per_step": 100}})
    rp.chat_with_tools(system="s", messages=[], tools=None,
                       force_json=True, model_hint="reasoning-strong")
    assert "anthropic" in made and made["anthropic"].calls == ["claude-sonnet-4-6"]


def test_routing_provider_raises_when_no_candidate():
    rp = routing.RoutingProvider(_policy(candidates={}), lambda n: _Fake(n))
    with pytest.raises(RuntimeError, match="no candidates"):
        rp.chat_with_tools(system="s", messages=[], tools=None,
                           force_json=True, model_hint="reasoning-strong")


def test_build_routing_provider_from_config():
    # Offline-safe: constructs the router from the real config, no candidate spun up.
    rp = routing.build_routing_provider(lambda n: _Fake(n))
    assert rp.name == "routing"
    assert "reasoning-strong" in rp.policy["candidates"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
