#!/usr/bin/env python3
"""
ROBOPORT benchmark runner.

Runs an eval set against a target agent or crew, N times per eval, and
writes per-run artifacts under evals/benchmarks/<label>/.

This is the orchestration skeleton. Integrate your model-call layer
(`call_planner`, `call_executor`, `call_grader`) by replacing the stubs
in `roboport_runtime`. The CLI and bookkeeping are production-ready;
the model-call layer is intentionally pluggable so you can wire it to
the API of your choice.

Usage:
  python scripts/benchmark.py --target jd_crew --eval-set evals/evals.json --runs 3
  python scripts/benchmark.py --target job_scout --eval-set evals/evals.json --runs 5 \\
                              --label 2026-04-25-prompt-v2

Output layout:
  evals/benchmarks/<label>/
    summary.json
    eval_<id>/
      run_<n>/
        plan.json
        final_output.json
        run.log         (JSONL)
        grading.json    (after --grade)
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# --- Pluggable runtime stubs --------------------------------------------------
# Replace these with real model calls (Anthropic SDK, OpenAI, etc.).
# Each returns a dict matching the shapes documented in the agent specs.

def call_planner(goal: str, context: dict, registry: dict) -> dict:
    """Stub. Returns a trivial single-step plan that echoes the goal."""
    return {
        "goal": goal,
        "deliverable": "final_output.json",
        "steps": [
            {
                "id": "s1",
                "owner": "stub",
                "wave": 0,
                "input": {"goal": goal},
                "output_type": "object",
                "success_criteria": ["output is non-empty"],
                "deterministic": True,
            }
        ],
        "estimated_llm_calls": 0,
        "estimated_tool_calls": 0,
        "fallback": "n/a",
    }


def call_executor(step: dict, accumulated: dict, registry: dict) -> dict:
    """Stub. Echoes back a deterministic 'output'."""
    return {
        "step_id": step["id"],
        "status": "ok",
        "output": {"echo": step["input"], "stub": True},
        "criteria_results": [{"criterion": c, "passed": True} for c in step["success_criteria"]],
        "tool_calls": 0,
        "llm_calls": 0,
        "transcript_path": None,
        "error": None,
    }


def call_grader(expectations: list[str], transcript_path: Path | None, outputs_dir: Path) -> dict:
    """Stub. Marks all expectations PASS with placeholder evidence."""
    return {
        "run_id": outputs_dir.name,
        "results": [
            {"expectation": e, "verdict": "PASS", "evidence": "[stub grader]"}
            for e in expectations
        ],
        "pass_rate": 1.0,
        "blocker_failed": False,
        "meta_critique": ["[stub grader] replace with real model call"],
        "graded_at": datetime.now(timezone.utc).isoformat(),
    }
# -----------------------------------------------------------------------------


def now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")


def jsonl_append(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def run_one(eval_obj: dict, run_dir: Path, registry: dict) -> dict:
    """Execute a single (eval × run) pair. Returns a small summary."""
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "run.log"
    plan = call_planner(eval_obj["prompt"], context={}, registry=registry)
    (run_dir / "plan.json").write_text(json.dumps(plan, indent=2))
    jsonl_append(log, {"event": "plan_emitted", "ts": datetime.now(timezone.utc).isoformat()})

    accumulated: dict = {}
    for step in plan["steps"]:
        result = call_executor(step, accumulated, registry)
        jsonl_append(log, {"event": "step_done", "step_id": step["id"], "status": result["status"]})
        if result["status"] != "ok":
            (run_dir / "final_output.json").write_text(json.dumps(
                {"status": "failed", "error": result.get("error")}, indent=2))
            return {"status": "failed", "step_failed": step["id"]}
        accumulated[step["id"]] = result["output"]

    final = list(accumulated.values())[-1] if accumulated else {}
    (run_dir / "final_output.json").write_text(json.dumps(final, indent=2))
    return {"status": "ok"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Agent or crew id (e.g., jd_crew)")
    ap.add_argument("--eval-set", default=str(REPO / "evals" / "evals.json"))
    ap.add_argument("--runs", type=int, default=3, help="Runs per eval (>=3 recommended)")
    ap.add_argument("--label", default=None, help="Output folder label; defaults to timestamp")
    ap.add_argument("--out", default=None, help="Override benchmark output dir")
    ap.add_argument("--grade", action="store_true", help="Run grader after each run")
    ap.add_argument("--live", action="store_true",
                    help="Use the Anthropic-SDK runtime instead of the stubs. "
                         "Requires ANTHROPIC_API_KEY.")
    args = ap.parse_args()

    if args.live:
        try:
            from roboport_runtime import (  # type: ignore
                call_planner as _live_planner,
                call_executor as _live_executor,
                call_grader as _live_grader,
            )
        except ImportError as e:
            print(f"--live requires `pip install anthropic`: {e}", file=sys.stderr)
            return 2
        global call_planner, call_executor, call_grader
        call_planner, call_executor, call_grader = _live_planner, _live_executor, _live_grader

    registry_path = REPO / "agents" / "registry.json"
    if not registry_path.exists():
        print(f"registry not found: {registry_path}", file=sys.stderr)
        return 2
    registry = json.loads(registry_path.read_text())

    eval_set = json.loads(Path(args.eval_set).read_text())
    evals = [e for e in eval_set["evals"] if eval_set.get("target") == args.target or args.target == "*"]
    if not evals:
        print(f"no evals found for target='{args.target}' in {args.eval_set}", file=sys.stderr)
        return 2

    label = args.label or now_label()
    out_dir = Path(args.out) if args.out else (REPO / "evals" / "benchmarks" / label)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "target": args.target,
        "label": label,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "runs_per_eval": args.runs,
        "evals": [],
    }

    for ev in evals:
        ev_dir = out_dir / f"eval_{ev['id']}"
        ev_summary = {"id": ev["id"], "runs": []}
        for n in range(1, args.runs + 1):
            run_dir = ev_dir / f"run_{n}"
            outcome = run_one(ev, run_dir, registry)
            grading = None
            if args.grade:
                grading = call_grader(ev["expectations"], run_dir / "run.log", run_dir)
                (run_dir / "grading.json").write_text(json.dumps(grading, indent=2))
            ev_summary["runs"].append({
                "n": n,
                "status": outcome["status"],
                "pass_rate": grading["pass_rate"] if grading else None,
                "blocker_failed": grading["blocker_failed"] if grading else None,
            })
        summary["evals"].append(ev_summary)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    failed = sum(1 for ev in summary["evals"] for r in ev["runs"] if r["status"] != "ok")
    total = sum(len(ev["runs"]) for ev in summary["evals"])
    print(f"benchmark: {total - failed}/{total} runs ok  -> {out_dir}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
