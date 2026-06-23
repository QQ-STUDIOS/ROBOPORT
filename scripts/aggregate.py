#!/usr/bin/env python3
"""
ROBOPORT benchmark aggregator.

Rolls up artifacts under evals/benchmarks/<label>/ into a single report.
Computes per-eval pass rates, per-expectation pass rates across runs,
blocker failure rates, and dedupes Grader meta-critiques.

Also reports routing telemetry (Phase 4): cost/latency per *passing* run and a
by-provider/model breakdown with blocker pass rate, read from each run's run.log.

Also supports baseline-vs-candidate comparison (including per-agent cost/latency
regression flags from the two summary.json routing blocks).

Usage:
  python scripts/aggregate.py --benchmark evals/benchmarks/<label>
  python scripts/aggregate.py --grade     evals/benchmarks/<label>     # runs grader on already-completed runs
  python scripts/aggregate.py --compare   --baseline <dir> --candidate <dir>
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def collect_runs(bench_dir: Path) -> list[dict]:
    """Walk bench_dir and gather every run's grading + outcome."""
    runs = []
    for ev_dir in sorted(bench_dir.glob("eval_*")):
        ev_id = ev_dir.name.replace("eval_", "")
        for run_dir in sorted(ev_dir.glob("run_*")):
            grading = run_dir / "grading.json"
            final = run_dir / "final_output.json"
            entry = {
                "eval_id": ev_id,
                "run": run_dir.name,
                "path": str(run_dir),
                "grading": load_json(grading) if grading.exists() else None,
                "completed": final.exists(),
            }
            runs.append(entry)
    return runs


# --- Phase 4 task 3: routing / cost-latency reporting ------------------------

def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, int(round(0.95 * (len(ordered) - 1))))]


def _read_run_log_telemetry(run_dir: Path) -> list[dict]:
    """Pull the per-step routing telemetry (Phase 4) off a run's run.log."""
    log = run_dir / "run.log"
    if not log.exists():
        return []
    steps = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "step_done":
            steps.append(ev)
    return steps


def _passing(entry: dict) -> bool:
    """A run is *passing* if it completed and (when graded) failed no blocker."""
    if not entry.get("completed"):
        return False
    g = entry.get("grading")
    if g and g.get("blocker_failed"):
        return False
    return True


def _sum_steps(steps: list[dict]) -> dict:
    agg = {"llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0,
           "completion_tokens": 0, "latency_ms": 0, "cost_usd": 0.0,
           "cost_unknown": False, "providers": set(), "models": set()}
    for s in steps:
        for k in ("llm_calls", "tool_calls", "prompt_tokens", "completion_tokens", "latency_ms"):
            agg[k] += int(s.get(k) or 0)
        cost = s.get("cost_usd")
        if cost is None:
            agg["cost_unknown"] = True
        else:
            agg["cost_usd"] += float(cost)
        if s.get("provider"):
            agg["providers"].add(s["provider"])
        if s.get("model"):
            agg["models"].add(s["model"])
    return agg


def routing_rollup(runs: list[dict]) -> dict:
    """Phase 4 task 3: cost/latency per *passing* run, and a by-provider/model
    breakdown with blocker pass rate. `runs` is a list of
    {eval_id, run, completed, grading, steps:[step_done telemetry]}.

    Cost stays honest: any unknown-cost step makes that scope's cost None
    (a passing-run total, a per-run total, or a provider/model total)."""
    per_run, pm = [], {}
    pass_costs, pass_cost_unknown, pass_latencies = [], False, []

    for r in runs:
        agg = _sum_steps(r["steps"])
        passing = _passing(r)
        blocker_failed = (bool((r.get("grading") or {}).get("blocker_failed"))
                          if r.get("grading") else None)
        per_run.append({
            "eval_id": r["eval_id"], "run": r["run"], "passing": passing,
            "completed": bool(r.get("completed")), "blocker_failed": blocker_failed,
            "llm_calls": agg["llm_calls"], "tool_calls": agg["tool_calls"],
            "prompt_tokens": agg["prompt_tokens"], "completion_tokens": agg["completion_tokens"],
            "latency_ms": agg["latency_ms"],
            "cost_usd": None if agg["cost_unknown"] else round(agg["cost_usd"], 6),
            "providers": sorted(agg["providers"]), "models": sorted(agg["models"]),
        })
        if passing:
            pass_latencies.append(agg["latency_ms"])
            if agg["cost_unknown"]:
                pass_cost_unknown = True
            else:
                pass_costs.append(agg["cost_usd"])

        seen = set()
        for s in r["steps"]:
            key = (s.get("provider"), s.get("model"))
            if key == (None, None):
                continue
            d = pm.setdefault(key, {"steps": 0, "llm_calls": 0, "latency_ms": 0,
                                    "prompt_tokens": 0, "completion_tokens": 0,
                                    "cost_usd": 0.0, "cost_unknown": False,
                                    "runs": set(), "graded_runs": 0, "blocker_fails": 0})
            d["steps"] += 1
            for k in ("llm_calls", "latency_ms", "prompt_tokens", "completion_tokens"):
                d[k] += int(s.get(k) or 0)
            cost = s.get("cost_usd")
            if cost is None:
                d["cost_unknown"] = True
            else:
                d["cost_usd"] += float(cost)
            seen.add(key)
        for key in seen:
            d = pm[key]
            d["runs"].add((r["eval_id"], r["run"]))
            if blocker_failed is not None:
                d["graded_runs"] += 1
                d["blocker_fails"] += int(blocker_failed)

    by_pm = []
    for (prov, model), d in sorted(pm.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        gr = d["graded_runs"]
        by_pm.append({
            "provider": prov, "model": model, "steps": d["steps"], "runs": len(d["runs"]),
            "llm_calls": d["llm_calls"], "latency_ms": d["latency_ms"],
            "prompt_tokens": d["prompt_tokens"], "completion_tokens": d["completion_tokens"],
            "cost_usd": None if d["cost_unknown"] else round(d["cost_usd"], 6),
            "blocker_pass_rate": round((gr - d["blocker_fails"]) / gr, 3) if gr else None,
        })

    n = len(pass_latencies)
    return {
        "per_run": per_run,
        "passing_runs": {
            "n": n,
            "cost_usd": {
                "total": None if pass_cost_unknown else round(sum(pass_costs), 6),
                "mean": (None if pass_cost_unknown or not pass_costs
                         else round(sum(pass_costs) / len(pass_costs), 6)),
            },
            "latency_ms": {
                "total": sum(pass_latencies),
                "mean": round(sum(pass_latencies) / n, 1) if n else 0,
                "p95": _p95(pass_latencies),
            },
        },
        "by_provider_model": by_pm,
    }


def collect_routing_runs(bench_dir: Path) -> list[dict]:
    """Build the `routing_rollup` input from a benchmark dir's run.log artifacts."""
    runs = []
    for ev_dir in sorted(bench_dir.glob("eval_*")):
        ev_id = ev_dir.name.replace("eval_", "")
        for run_dir in sorted(ev_dir.glob("run_*")):
            grading = run_dir / "grading.json"
            runs.append({
                "eval_id": ev_id, "run": run_dir.name,
                "completed": (run_dir / "final_output.json").exists(),
                "grading": load_json(grading) if grading.exists() else None,
                "steps": _read_run_log_telemetry(run_dir),
            })
    return runs


# Conservative defaults: a per-step mean must rise by both a fraction and an
# absolute floor before it is flagged, so noise on tiny numbers doesn't trip it.
ROUTING_REGRESSION_POLICY = {"latency_pct": 0.20, "latency_min_ms": 50.0,
                             "cost_pct": 0.20, "cost_min_usd": 1e-4}


def routing_deltas(baseline_summary: dict, candidate_summary: dict,
                   policy: dict | None = None) -> list[dict]:
    """Phase 4 task 3: per-agent cost/latency deltas between two benchmarks'
    `summary.json` routing blocks, flagging material regressions."""
    pol = policy or ROUTING_REGRESSION_POLICY
    a = {r["agent"]: r for r in ((baseline_summary.get("routing") or {}).get("by_agent") or [])}
    b = {r["agent"]: r for r in ((candidate_summary.get("routing") or {}).get("by_agent") or [])}

    def _mean(row: dict, key: str):
        steps, val = row.get("steps") or 0, row.get(key)
        if not steps or val is None:
            return None
        return val / steps

    out = []
    for agent in sorted(set(a) | set(b)):
        ar, br = a.get(agent, {}), b.get(agent, {})
        bl_lat, cd_lat = _mean(ar, "latency_ms"), _mean(br, "latency_ms")
        bl_cost, cd_cost = _mean(ar, "cost_usd"), _mean(br, "cost_usd")
        flags, lat_pct, cost_pct = [], None, None
        if bl_lat and cd_lat is not None:
            lat_pct = round((cd_lat - bl_lat) / bl_lat, 3)
            if lat_pct > pol["latency_pct"] and (cd_lat - bl_lat) > pol["latency_min_ms"]:
                flags.append("latency_regression")
        if bl_cost and cd_cost is not None:
            cost_pct = round((cd_cost - bl_cost) / bl_cost, 3)
            if cost_pct > pol["cost_pct"] and (cd_cost - bl_cost) > pol["cost_min_usd"]:
                flags.append("cost_regression")
        out.append({
            "agent": agent,
            "baseline_latency_ms_mean": round(bl_lat, 1) if bl_lat is not None else None,
            "candidate_latency_ms_mean": round(cd_lat, 1) if cd_lat is not None else None,
            "latency_delta_pct": lat_pct,
            "baseline_cost_mean": round(bl_cost, 6) if bl_cost is not None else None,
            "candidate_cost_mean": round(cd_cost, 6) if cd_cost is not None else None,
            "cost_delta_pct": cost_pct,
            "flags": flags,
        })
    return out


def report_benchmark(bench_dir: Path) -> dict:
    runs = collect_runs(bench_dir)

    by_eval: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_eval[r["eval_id"]].append(r)

    per_eval = []
    expectation_outcomes: dict[str, list[bool]] = defaultdict(list)
    meta_critiques = Counter()

    for ev_id, ev_runs in by_eval.items():
        pass_rates = []
        blocker_fails = 0
        for r in ev_runs:
            g = r["grading"]
            if not g:
                continue
            pass_rates.append(g["pass_rate"])
            if g.get("blocker_failed"):
                blocker_fails += 1
            for res in g.get("results", []):
                expectation_outcomes[res["expectation"]].append(res["verdict"] == "PASS")
            for note in g.get("meta_critique", []):
                meta_critiques[note] += 1

        per_eval.append({
            "eval_id": ev_id,
            "n_runs": len(ev_runs),
            "mean_pass_rate": round(statistics.mean(pass_rates), 3) if pass_rates else None,
            "stdev_pass_rate": round(statistics.stdev(pass_rates), 3) if len(pass_rates) > 1 else 0.0,
            "blocker_failure_rate": round(blocker_fails / len(ev_runs), 3) if ev_runs else None,
        })

    per_expectation = sorted(
        [
            {
                "expectation": exp,
                "n": len(outs),
                "pass_rate": round(sum(outs) / len(outs), 3),
            }
            for exp, outs in expectation_outcomes.items()
        ],
        key=lambda x: x["pass_rate"],
    )

    return {
        "benchmark": str(bench_dir.relative_to(REPO)) if bench_dir.is_relative_to(REPO) else str(bench_dir),
        "n_runs_total": len(runs),
        "per_eval": per_eval,
        "weakest_expectations": per_expectation[:10],
        "strongest_expectations": list(reversed(per_expectation))[:10],
        "meta_critiques_top": meta_critiques.most_common(10),
        "routing": routing_rollup(collect_routing_runs(bench_dir)),
    }


def _load_summary(bench_dir: Path) -> dict:
    p = bench_dir / "summary.json"
    return load_json(p) if p.exists() else {}


def compare(baseline: Path, candidate: Path) -> dict:
    a = report_benchmark(baseline)
    b = report_benchmark(candidate)
    a_by_eval = {e["eval_id"]: e for e in a["per_eval"]}
    b_by_eval = {e["eval_id"]: e for e in b["per_eval"]}

    deltas = []
    for eid in sorted(set(a_by_eval) | set(b_by_eval)):
        ae = a_by_eval.get(eid, {})
        be = b_by_eval.get(eid, {})
        deltas.append({
            "eval_id": eid,
            "baseline_pass_rate":  ae.get("mean_pass_rate"),
            "candidate_pass_rate": be.get("mean_pass_rate"),
            "delta": (be.get("mean_pass_rate") or 0) - (ae.get("mean_pass_rate") or 0)
                if (ae.get("mean_pass_rate") is not None and be.get("mean_pass_rate") is not None) else None,
            "baseline_blocker_fail":  ae.get("blocker_failure_rate"),
            "candidate_blocker_fail": be.get("blocker_failure_rate"),
        })

    wins = sum(1 for d in deltas if d["delta"] is not None and d["delta"] > 0)
    losses = sum(1 for d in deltas if d["delta"] is not None and d["delta"] < 0)
    return {
        "baseline":  a["benchmark"],
        "candidate": b["benchmark"],
        "wins": wins,
        "losses": losses,
        "ties":   len(deltas) - wins - losses,
        "deltas": deltas,
        "routing_deltas": routing_deltas(_load_summary(baseline), _load_summary(candidate)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", type=Path, help="Roll up a single benchmark dir")
    ap.add_argument("--grade",     type=Path, help="(stub) trigger grading pass over a benchmark dir")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--baseline",  type=Path)
    ap.add_argument("--candidate", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.compare:
        if not (args.baseline and args.candidate):
            ap.error("--compare requires --baseline and --candidate")
        out = compare(args.baseline, args.candidate)
    elif args.benchmark:
        out = report_benchmark(args.benchmark)
    elif args.grade:
        # Grading is performed inside benchmark.py via --grade.
        # Re-grading already-completed runs requires the model-call layer; stub here.
        print("grading already-completed runs requires a wired model layer; "
              "use scripts/benchmark.py --grade on a fresh run for now.", file=sys.stderr)
        return 2
    else:
        ap.print_help()
        return 1

    text = json.dumps(out, indent=2)
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
