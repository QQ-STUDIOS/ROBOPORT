"""Phase 4 task 4 — observability-aware routing.

A `RoutingProvider` sits on the Provider seam and, per `model_hint`, picks a
concrete `{provider, model}` candidate from the routing policy — *acting on the
telemetry* the rest of Phase 4 produces. The decision engine (`select_candidate`)
is pure and offline-testable; the provider is a thin delegator around it.

Policy (in `config/agent_config.yaml`, inert unless `--provider routing`):

    routing:
      objective: blocker_pass_then_cost      # or "static"
      constraints: { max_latency_ms_per_step: 10000, max_usd_per_run: 5.00 }
      candidates:
        reasoning-strong:
          - { provider: ollama,    model: qwen3:14b }
          - { provider: anthropic, model: claude-sonnet-4-6 }

Without a scoreboard, selection is the policy's **static priority** (first
candidate). Given a scoreboard — the `by_provider_model` block an
`aggregate.py` routing report produces — `blocker_pass_then_cost` ranks the
candidates by highest blocker pass rate, then lowest per-step cost, after
dropping any whose known per-step latency exceeds `max_latency_ms_per_step`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .providers import Provider

REPO = Path(__file__).resolve().parent.parent.parent

DEFAULT_OBJECTIVE = "blocker_pass_then_cost"


def load_routing_policy(config: dict | None) -> dict:
    """Parse the `routing` block of an agent_config dict into a normalized policy."""
    r = (config or {}).get("routing") or {}
    candidates = {}
    for hint, lst in (r.get("candidates") or {}).items():
        candidates[hint] = [
            {"provider": str(c.get("provider")), "model": c.get("model")}
            for c in (lst or []) if c.get("provider")
        ]
    return {
        "objective": r.get("objective", DEFAULT_OBJECTIVE),
        "constraints": dict(r.get("constraints") or {}),
        "candidates": candidates,
    }


def scoreboard_from_report(by_provider_model: list[dict]) -> dict:
    """Turn an aggregate routing report's `by_provider_model` rows into a
    scoreboard keyed by (provider, model): blocker pass rate + per-step cost/latency.
    Closes the loop telemetry → decision."""
    sb: dict[tuple, dict] = {}
    for row in by_provider_model or []:
        steps = row.get("steps") or 0
        cost = row.get("cost_usd")
        lat = row.get("latency_ms") or 0
        sb[(row.get("provider"), row.get("model"))] = {
            "blocker_pass_rate": row.get("blocker_pass_rate"),
            "cost_per_step": None if (cost is None or not steps) else round(cost / steps, 6),
            "latency_per_step": round(lat / steps, 1) if steps else None,
        }
    return sb


def _rank_key(entry: dict):
    """blocker_pass_then_cost: highest pass rate first (unknown last), then
    cheapest per step (unknown last). Stable sort keeps policy order as the final
    tiebreak."""
    bpr = entry["blocker_pass_rate"]
    cost = entry["cost_per_step"]
    return (-(bpr if bpr is not None else -1.0),
            cost if cost is not None else float("inf"))


def select_candidate(hint: str, policy: dict, scoreboard: dict | None = None) -> dict:
    """Pure decision: choose a candidate for `hint`. Returns a decision record
    {hint, objective, chosen: {provider, model}|None, reason, considered:[...]}"""
    candidates = policy.get("candidates", {}).get(hint) or []
    objective = policy.get("objective", DEFAULT_OBJECTIVE)
    constraints = policy.get("constraints", {})
    sb = scoreboard or {}
    max_lat = constraints.get("max_latency_ms_per_step")

    if not candidates:
        return {"hint": hint, "objective": objective, "chosen": None,
                "reason": f"no candidates configured for hint {hint!r}", "considered": []}

    considered = []
    for c in candidates:
        stats = sb.get((c["provider"], c.get("model")), {})
        lat = stats.get("latency_per_step")
        eligible = not (max_lat is not None and lat is not None and lat > max_lat)
        considered.append({
            "provider": c["provider"], "model": c.get("model"),
            "blocker_pass_rate": stats.get("blocker_pass_rate"),
            "cost_per_step": stats.get("cost_per_step"),
            "latency_per_step": lat, "eligible": eligible,
        })

    have_stats = any((e["provider"], e["model"]) in sb for e in considered)
    if objective == "static" or not have_stats:
        chosen = considered[0]
        reason = ("static priority (objective=static)" if objective == "static"
                  else "static priority — no scoreboard telemetry yet")
    else:
        pool = [e for e in considered if e["eligible"]]
        constrained_out = len(considered) - len(pool)
        if not pool:  # none meet the latency constraint — pick least-bad, note it
            pool = considered
        ranked = sorted(pool, key=_rank_key)
        chosen = ranked[0]
        reason = (f"objective={objective}: highest blocker pass rate then lowest "
                  f"per-step cost" + (f" ({constrained_out} dropped on latency)"
                                      if constrained_out else ""))

    return {"hint": hint, "objective": objective,
            "chosen": {"provider": chosen["provider"], "model": chosen["model"]},
            "reason": reason, "considered": considered}


class RoutingProvider(Provider):
    """Delegating provider: per call, select a candidate for the hint and forward
    to that concrete provider, pinning the chosen model. Records every decision."""

    name = "routing"

    def __init__(self, policy: dict, provider_factory: Callable[[str], Provider],
                 scoreboard: dict | None = None):
        self.policy = policy
        self._factory = provider_factory
        self.scoreboard = dict(scoreboard or {})
        self._instances: dict[str, Provider] = {}
        self.decisions: list[dict] = []

    def _provider(self, name: str) -> Provider:
        if name not in self._instances:            # lazily construct only what we use
            self._instances[name] = self._factory(name)
        return self._instances[name]

    def chat_with_tools(self, *, system, messages, tools, force_json, model_hint,
                        model: str | None = None) -> dict[str, Any]:
        decision = select_candidate(model_hint, self.policy, self.scoreboard)
        self.decisions.append(decision)
        chosen = decision["chosen"]
        if not chosen:
            raise RuntimeError(f"routing: {decision['reason']}")
        out = self._provider(chosen["provider"]).chat_with_tools(
            system=system, messages=messages, tools=tools, force_json=force_json,
            model_hint=model_hint, model=chosen["model"])
        usage = out.get("usage")
        if isinstance(usage, dict):
            usage["routed_hint"] = model_hint
            usage["routed_provider"] = chosen["provider"]
            usage["routed_model"] = chosen["model"]
        return out

    def health_check(self) -> None:  # pragma: no cover - construction is offline-safe
        pass


def _load_agent_config() -> dict:
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(
            (REPO / "config" / "agent_config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — degrade to an empty policy
        return {}


def build_routing_provider(provider_factory: Callable[[str], Provider]) -> RoutingProvider:
    """Construct a RoutingProvider from config/agent_config.yaml. Starts with no
    scoreboard (static priority); attach one via `.scoreboard` once a benchmark
    routing report exists."""
    return RoutingProvider(load_routing_policy(_load_agent_config()), provider_factory)
