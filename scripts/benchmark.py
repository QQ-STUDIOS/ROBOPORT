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
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def step_fingerprint(owner: str, registry: dict, config: dict) -> str:
    """A short, stable hash of everything that decides how a step runs — its
    registry entry, resolved model_hint, and provider/model. diff_runs.py uses it
    as the comparability check: same fingerprint => the two runs are comparable;
    a different one is surfaced as config drift (e.g. a routing change)."""
    reg = (registry.get("agents") or {}).get(owner) or {}
    override = (config.get("agent_overrides") or {}).get(owner) or {}
    hint = override.get("model_hint", reg.get("model_hint"))
    model = (config.get("models") or {}).get(hint) or {}
    payload = {
        "agent": owner,
        "model_hint": hint,
        "registry": {k: reg.get(k) for k in ("path", "role", "deterministic", "model_hint")},
        "model": {k: model.get(k) for k in ("provider", "model")},
        "override": override or None,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def load_agent_config() -> dict:
    """Best-effort load of config/agent_config.yaml (empty dict if unavailable)."""
    path = REPO / "config" / "agent_config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml  # lazy; pyyaml is a runtime dep
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — fingerprint degrades gracefully without config
        return {}


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


def run_one(eval_obj: dict, run_dir: Path, registry: dict, feed=None, run_log=None,
            config: dict | None = None) -> dict:
    """Execute a single (eval × run) pair. Returns a small summary.

    If `feed` (a roboport_runtime.feed_log.FeedLog) is provided, the run also
    emits control-surface lifecycle telemetry — the runtime-native feed that
    lights up control_surface/. The runtime emits only logical state; the
    dashboard owns all motion.

    If `run_log` (a roboport_runtime.run_log.RunLog) is provided, the run emits
    the Ops Console event stream (run.start / plan.created / step.* /
    run.complete) that dashboard/bridge.py tails.
    """
    import time as _time
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "run.log"
    crew = eval_obj.get("target") or "crew"
    if run_log is not None:
        run_log.run_start(crew)
    _t_run = _time.perf_counter()

    plan = call_planner(eval_obj["prompt"], context={}, registry=registry)
    (run_dir / "plan.json").write_text(json.dumps(plan, indent=2))
    jsonl_append(log, {"event": "plan_emitted", "ts": datetime.now(timezone.utc).isoformat()})

    if feed is not None:
        from roboport_runtime.feed_log import stations_from_plan  # lazy
        feed.crew_start(crew, stations_from_plan(plan), input=eval_obj.get("prompt"))
    if run_log is not None:
        run_log.plan_created(plan)

    accumulated: dict = {}
    durations_ms: list[int] = []
    llm_total = tool_total = 0
    for step in plan["steps"]:
        sid = step["id"]
        owner = step["owner"]
        task_id, agent_id = f"t_{sid}", f"drone-{sid}"
        station_id = f"stn.{owner}"
        # deterministic steps run fast; LLM steps get a longer work estimate.
        est = 0.8 if step.get("deterministic") else 2.4
        if feed is not None:
            feed.task_enqueue(task_id, station_id, agent_id,
                              wave=int(step.get("wave", 0)), work_estimate_s=est)
            feed.task_start(task_id, agent_id, station_id, eta_s=1.2)
            feed.task_progress(task_id, agent_id, 0.05, eta_s=est)
        if run_log is not None:
            run_log.step_start(sid, owner, wave=int(step.get("wave", 0)))

        _t_step = _time.perf_counter()
        result = call_executor(step, accumulated, registry)
        dur_ms = int((_time.perf_counter() - _t_step) * 1000)

        jsonl_append(log, {
            "event": "step_done",
            "step_id": sid,
            "status": result["status"],
            "criteria_results": result.get("criteria_results", []),
            "tool_calls": result.get("tool_calls", 0),
            "llm_calls": result.get("llm_calls", 0),
            "duration_ms": dur_ms,
            "config_fp": step_fingerprint(owner, registry, config or {}),
            "error": result.get("error"),
        })
        ok = result["status"] == "ok"
        llm_total += result.get("llm_calls", 0)
        tool_total += result.get("tool_calls", 0)
        durations_ms.append(dur_ms)
        if feed is not None:
            if ok:
                feed.task_progress(task_id, agent_id, 1.0, eta_s=0.0)
            feed.task_end(task_id, agent_id,
                          status="ok" if ok else "error",
                          error=result.get("error"),
                          llm_calls=result.get("llm_calls", 0),
                          tool_calls=result.get("tool_calls", 0),
                          deterministic=bool(step.get("deterministic")))
        if run_log is not None:
            if ok:
                run_log.step_complete(sid, owner, duration_ms=dur_ms,
                                      llm_calls=result.get("llm_calls", 0),
                                      tool_calls=result.get("tool_calls", 0))
            else:
                run_log.step_failed(sid, owner, result.get("error"), layer="criterion_failed")
        if not ok:
            if feed is not None:
                feed.crew_end(status="failed")
            if run_log is not None:
                run_log.run_complete(_run_summary(durations_ms, llm_total, tool_total, _t_run, _time))
            (run_dir / "final_output.json").write_text(json.dumps(
                {"status": "failed", "error": result.get("error")}, indent=2))
            return {"status": "failed", "step_failed": sid}
        accumulated[sid] = result["output"]

    if feed is not None:
        feed.crew_end(status="ok")
    if run_log is not None:
        run_log.run_complete(_run_summary(durations_ms, llm_total, tool_total, _t_run, _time))
    final = list(accumulated.values())[-1] if accumulated else {}
    (run_dir / "final_output.json").write_text(json.dumps(final, indent=2))
    return {"status": "ok"}


def _run_summary(durations_ms, llm_total, tool_total, t_run_start, _time) -> dict:
    """run.complete payload — counts + wall time + p95 step latency."""
    p95 = 0
    if durations_ms:
        ordered = sorted(durations_ms)
        idx = max(0, int(round(0.95 * (len(ordered) - 1))))
        p95 = ordered[idx]
    return {
        "steps": len(durations_ms),
        "llm_calls": llm_total,
        "tool_calls": tool_total,
        "wall_ms": int((_time.perf_counter() - t_run_start) * 1000),
        "p95_ms": p95,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Agent or crew id (e.g., jd_crew)")
    ap.add_argument("--eval-set", default=str(REPO / "evals" / "evals.json"))
    ap.add_argument("--runs", type=int, default=3, help="Runs per eval (>=3 recommended)")
    ap.add_argument("--label", default=None, help="Output folder label; defaults to timestamp")
    ap.add_argument("--out", default=None, help="Override benchmark output dir")
    ap.add_argument("--grade", action="store_true", help="Run grader after each run")
    ap.add_argument("--feed-log", default=None, metavar="PATH",
                    help="Also emit control-surface lifecycle telemetry (JSONL) to "
                         "PATH. Tail it with control_surface/collector (runtime feed) "
                         "to watch the crew run on the dashboard.")
    ap.add_argument("--run-log", nargs="?", const="runs", default=None, metavar="DIR",
                    help="Also emit the Ops Console event stream to "
                         "DIR/<run_id>/run.log (default DIR: runs/). Tail it with "
                         "dashboard/bridge.py to watch the crew on the Ops Console.")
    ap.add_argument("--live", action="store_true",
                    help="Use the model-backed runtime (scripts/roboport_runtime) "
                         "instead of the stubs.")
    ap.add_argument("--provider", choices=["ollama", "anthropic"], default=None,
                    help="Backend for --live. Default: $ROBOPORT_PROVIDER or "
                         "'ollama'. Anthropic requires ANTHROPIC_API_KEY.")
    args = ap.parse_args()

    if args.live:
        if args.provider:
            os.environ["ROBOPORT_PROVIDER"] = args.provider
        try:
            from roboport_runtime import (  # type: ignore
                call_planner as _live_planner,
                call_executor as _live_executor,
                call_grader as _live_grader,
            )
            from roboport_runtime.client import health_check, provider  # type: ignore
        except ImportError as e:
            print(f"--live requires `pip install -r requirements.txt`: {e}", file=sys.stderr)
            return 2
        try:
            health_check()
            print(f"--live: provider={provider().name}", file=sys.stderr)
        except RuntimeError as e:
            print(f"--live preflight failed: {e}", file=sys.stderr)
            return 2
        global call_planner, call_executor, call_grader
        call_planner, call_executor, call_grader = _live_planner, _live_executor, _live_grader

    registry_path = REPO / "agents" / "registry.json"
    if not registry_path.exists():
        print(f"registry not found: {registry_path}", file=sys.stderr)
        return 2
    registry = json.loads(registry_path.read_text())
    config = load_agent_config()

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
            ev.setdefault("target", args.target)
            feed = None
            if args.feed_log:
                from roboport_runtime.feed_log import FeedLog  # lazy; stdlib-only
                feed = FeedLog(args.feed_log, run_id=f"{label}/{ev['id']}/run_{n}")
            run_log = None
            if args.run_log:
                from roboport_runtime.run_log import RunLog  # lazy; stdlib-only
                rl_id = f"{label}_{ev['id']}_run{n}"
                run_log = RunLog(Path(args.run_log) / rl_id, run_id=rl_id)
            try:
                outcome = run_one(ev, run_dir, registry, feed=feed, run_log=run_log, config=config)
            finally:
                if feed is not None:
                    feed.close()
                if run_log is not None:
                    run_log.close()
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
