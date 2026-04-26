#!/usr/bin/env python3
"""
ROBOPORT benchmark aggregator.

Rolls up artifacts under evals/benchmarks/<label>/ into a single report.
Computes per-eval pass rates, per-expectation pass rates across runs,
blocker failure rates, and dedupes Grader meta-critiques.

Also supports baseline-vs-candidate comparison.

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
    }


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
