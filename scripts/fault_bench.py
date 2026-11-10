#!/usr/bin/env python3
"""Offline fault-injection benchmark.

Runs the error-stack scenarios in evals/fault_injection.json through the *real*
executor, driven by the FaultProvider instead of a model — so Phase 3's error
stack (call-level retry/repair/quiet-200, step-level budget, user-level
unsafe-action escalation) is exercised end to end, offline, and gated. Each
scenario scripts the provider's responses and declares the outcome to expect;
the runner grades the actual result against it and writes diff_runs-compatible
run artifacts (run.log step_done + final_output.json + grading.json).

Exit codes: 0 all scenarios met expectations · 1 a scenario diverged · 2 load error.

    python scripts/fault_bench.py                 # run the suite, write artifacts
    python scripts/fault_bench.py --json           # machine-readable summary
    python scripts/fault_bench.py --out runs/fi    # choose the output dir
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from roboport_runtime import executor  # noqa: E402
from roboport_runtime.fault_provider import FaultProvider  # noqa: E402

# Executor seams we swap per scenario (restored afterward) — the same ones the
# fault-injection unit tests monkeypatch.
_SEAMS = ("provider", "load_agent_spec", "_agent_tools_for", "_agent_config", "dispatch")


def grade(result: dict, calls: int, expect: dict) -> tuple[bool, list[str]]:
    """Deterministically check a step result against a scenario's `expect`."""
    mism: list[str] = []
    for key, actual in (("status", result.get("status")), ("layer", result.get("layer")),
                        ("retries", result.get("retries")), ("repaired", result.get("repaired")),
                        ("escalated_action", result.get("escalated_action")), ("calls", calls)):
        if key in expect and actual != expect[key]:
            mism.append(f"{key}: expected {expect[key]!r}, got {actual!r}")
    if "layer_not" in expect and result.get("layer") == expect["layer_not"]:
        mism.append(f"layer_not: layer must not be {expect['layer_not']!r}")
    if "error_contains" in expect and expect["error_contains"] not in (result.get("error") or ""):
        mism.append(f"error_contains: {expect['error_contains']!r} not in error "
                    f"{result.get('error')!r}")
    if "failed_criterion_contains" in expect:
        sub = expect["failed_criterion_contains"]
        failed = [c["criterion"] for c in result.get("criteria_results", []) if not c.get("passed")]
        if not any(sub in c for c in failed):
            mism.append(f"failed_criterion_contains: no failed criterion contains {sub!r} "
                        f"(failed={failed})")
    return (not mism, mism)


def run_scenario(scenario: dict, originals: dict) -> tuple[dict, int]:
    """Run one scenario through the real executor with the FaultProvider injected."""
    fp = FaultProvider(scenario["fault_script"])
    executor.provider = lambda: fp
    executor.load_agent_spec = lambda *_: "spec"
    executor._agent_tools_for = lambda *_: list(scenario.get("allowed_tools", []))
    executor._agent_config = (lambda: scenario["config"]) if "config" in scenario \
        else originals["_agent_config"]
    executor.dispatch = lambda name, args: None  # no-op: no scenario reaches a real dispatch

    st = scenario["step"]
    owner = st.get("owner", "tester")
    registry = {"agents": {owner: {"path": "x", "model_hint": "any", "deterministic": False}}}
    step = {"id": scenario["id"], "owner": owner, "wave": 0, "input": {},
            "output_type": st.get("output_type", "FinalReport"),
            "success_criteria": st.get("success_criteria", ["produce output"])}
    return executor.call_executor(step, {}, registry), fp.calls


def _config_fp(step: dict, config: dict | None) -> str:
    raw = json.dumps({"owner": step["owner"], "output_type": step["output_type"],
                      "config": config or {}}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def write_artifacts(run_dir: Path, scenario: dict, result: dict, verdict: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    step = {"owner": scenario["step"].get("owner", "tester"),
            "output_type": scenario["step"].get("output_type", "FinalReport")}
    step_done = {
        "event": "step_done", "step_id": scenario["id"], "status": result["status"],
        "criteria_results": result.get("criteria_results", []),
        "tool_calls": result.get("tool_calls", 0), "llm_calls": result.get("llm_calls", 0),
        "duration_ms": 0, "config_fp": _config_fp(step, scenario.get("config")),
        "provider": result.get("provider"), "model": result.get("model"),
        "prompt_tokens": result.get("prompt_tokens", 0),
        "completion_tokens": result.get("completion_tokens", 0),
        "cost_usd": result.get("cost_usd"), "latency_ms": result.get("latency_ms", 0),
        "layer": result.get("layer"), "error": result.get("error"),
    }
    (run_dir / "run.log").write_text(json.dumps(step_done) + "\n", encoding="utf-8")
    (run_dir / "final_output.json").write_text(
        json.dumps(result.get("output", {}), indent=2), encoding="utf-8")
    # Deterministic verdict — named so it does NOT collide with the LLM Grader's
    # grading.json, which aggregate.py parses with a different schema.
    (run_dir / "fault_verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")


def run_suite(scenarios: list[dict], out_dir: Path) -> dict:
    originals = {s: getattr(executor, s) for s in _SEAMS}
    results = []
    try:
        for sc in scenarios:
            result, calls = run_scenario(sc, originals)
            passed, mism = grade(result, calls, sc["expect"])
            verdict = {"scenario": sc["id"], "layer": sc.get("layer"), "passed": passed,
                       "expected": sc["expect"], "actual_status": result.get("status"),
                       "actual_layer": result.get("layer"), "mismatches": mism}
            write_artifacts(out_dir / f"eval_{sc['id']}" / "run_1", sc, result, verdict)
            results.append(verdict)
    finally:
        for s, v in originals.items():
            setattr(executor, s, v)
    passed_n = sum(1 for r in results if r["passed"])
    return {"target": "fault_injection", "out": str(out_dir), "total": len(results),
            "passed": passed_n, "ok": passed_n == len(results), "scenarios": results}


def _render(summary: dict) -> str:
    lines = [f"fault-injection benchmark: {summary['passed']}/{summary['total']} scenarios met "
             f"expectations  -> {summary['out']}", ""]
    for r in summary["scenarios"]:
        mark = "ok  " if r["passed"] else "FAIL"
        lines.append(f"  [{mark}] {r['scenario']:32} ({r['layer']})")
        for m in r["mismatches"]:
            lines.append(f"         - {m}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the offline fault-injection suite.")
    ap.add_argument("--set", type=Path, default=REPO / "evals" / "fault_injection.json",
                    help="Fault-injection scenario set.")
    ap.add_argument("--out", type=Path, default=None, help="Output dir for run artifacts.")
    ap.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    ap.add_argument("--quiet", action="store_true", help="Suppress stdout on all-pass.")
    args = ap.parse_args()

    try:
        data = json.loads(args.set.read_text(encoding="utf-8"))
        scenarios = data["scenarios"]
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"fault_bench: could not load scenario set: {e}", file=sys.stderr)
        return 2

    label = datetime.now(timezone.utc).strftime("fault_%Y%m%dT%H%M%SZ")
    out_dir = args.out or (REPO / "evals" / "benchmarks" / label)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = run_suite(scenarios, out_dir)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(summary, indent=2))
    elif not (summary["ok"] and args.quiet):
        print(_render(summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
