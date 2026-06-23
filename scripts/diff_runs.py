#!/usr/bin/env python3
"""ROBOPORT — cross-run regression diff (Roadmap Phase 1).

Compare two benchmark run directories and attribute drift to a specific agent
boundary. This is the keystone of ROBOPORT's operability thesis: *which agent,
contract, criterion, or cost changed between a baseline run and a candidate?*

Design (see docs/ROADMAP.md §"benign drift vs real regression"):

  Regression is anchored to CRITERIA, not to prose. Two passing runs of the same
  agent produce different free text; a naive content diff would flag every run.
  So the hard signals are deterministic ones — step status, success criteria,
  grading verdicts (esp. blockers), and schema validity. Cost/tool-use deltas are
  soft signals (warnings). Free-text content changes are informational only.

Reads the benchmark run-dir artifacts written by scripts/benchmark.py:

  <run>/plan.json          steps: id, owner (agent), output_type, wave
  <run>/final_output.json  the crew's final typed output (last step)
  <run>/run.log            JSONL; step_done events carry status, criteria_results,
                           tool_calls, llm_calls, error
  <run>/grading.json       (optional, after --grade) per-expectation PASS/FAIL +
                           blocker_failed

Offline and stdlib-only, except optional `jsonschema` for the schema dimension
(degrades to "unavailable" if not installed). Deterministic: same inputs produce
byte-stable JSON (no timestamps unless --include-ts).

Usage:
  python scripts/diff_runs.py \\
    --baseline evals/benchmarks/<label>/eval_1/run_1 \\
    --candidate evals/benchmarks/<label>/eval_1/run_2 \\
    --out diff.json --markdown diff.md

Exit-code contract (wires Phase 1 -> CI gating in Phases 3 & 5):
  0  pass / warning  (unless raised with --fail-on)
  1  regression
  2  inconclusive
Use --fail-on {regression,warning,inconclusive} to set the gating threshold.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
OUTPUT_SCHEMA = REPO / "resources" / "schemas" / "output.schema.json"

# Severity ordering — higher wins when rolling up a step / the whole run.
SEV_ORDER = {"info": 0, "warning": 1, "regression": 2, "inconclusive": 3}


# --- loading -----------------------------------------------------------------

class Run:
    """A parsed benchmark run directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.plan = _load_json(path / "plan.json") or {}
        self.final_output = _load_json(path / "final_output.json")
        self.grading = _load_json(path / "grading.json")
        self.steps = _parse_run_log(path / "run.log")
        # step_id -> {owner, output_type, wave} from the plan
        self.plan_steps = {
            s.get("id"): {
                "owner": s.get("owner"),
                "output_type": s.get("output_type"),
                "wave": s.get("wave", 0),
            }
            for s in self.plan.get("steps", [])
        }

    @property
    def goal(self) -> Optional[str]:
        return self.plan.get("goal")

    @property
    def final_output_type(self) -> Optional[str]:
        steps = self.plan.get("steps") or []
        return steps[-1].get("output_type") if steps else None

    @property
    def run_failed(self) -> bool:
        fo = self.final_output
        return isinstance(fo, dict) and fo.get("status") == "failed"

    def totals(self) -> dict[str, int]:
        llm = sum(s.get("llm_calls", 0) for s in self.steps.values())
        tool = sum(s.get("tool_calls", 0) for s in self.steps.values())
        dur = [s["duration_ms"] for s in self.steps.values() if s.get("duration_ms") is not None]
        return {
            "llm_calls": llm,
            "tool_calls": tool,
            "duration_ms": sum(dur) if dur else None,
        }


def _load_json(p: Path) -> Any:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _parse_run_log(p: Path) -> dict[str, dict]:
    """step_id -> {status, criteria{name:passed}, tool_calls, llm_calls, error, duration_ms}."""
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "step_done":
            continue
        sid = rec.get("step_id")
        if sid is None:
            continue
        out[sid] = {
            "status": rec.get("status"),
            "criteria": {
                c.get("criterion"): bool(c.get("passed"))
                for c in rec.get("criteria_results", [])
                if c.get("criterion") is not None
            },
            "tool_calls": rec.get("tool_calls", 0),
            "llm_calls": rec.get("llm_calls", 0),
            "error": rec.get("error"),
            # forward-compatible: present only once benchmark.py logs per-step latency
            "duration_ms": rec.get("duration_ms"),
        }
    return out


# --- schema dimension --------------------------------------------------------

def _schema_validity(instance: Any, output_type: Optional[str]) -> Optional[bool]:
    """True/False if validatable against output.schema.json; None if unavailable."""
    if instance is None or not output_type:
        return None
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return None
    full = _load_json(OUTPUT_SCHEMA) or {}
    defs = full.get("definitions") or {}
    if output_type not in defs:
        return None
    schema = {"$ref": f"#/definitions/{output_type}", "definitions": defs}
    try:
        jsonschema.validate(instance, schema)
        return True
    except jsonschema.ValidationError:
        return False
    except jsonschema.SchemaError:
        return None


# --- content diff (informational only in Phase 1) ----------------------------

def _content_changes(base: Any, cand: Any, prefix: str = "") -> list[dict]:
    """Scalar/structural changes by JSON path. Free text is expected to vary, so
    these are emitted at severity=info — never a regression on their own."""
    changes: list[dict] = []
    if isinstance(base, dict) and isinstance(cand, dict):
        for key in sorted(set(base) | set(cand)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in cand:
                changes.append({"path": path, "change": "removed"})
            elif key not in base:
                changes.append({"path": path, "change": "added"})
            else:
                changes.extend(_content_changes(base[key], cand[key], path))
    elif isinstance(base, list) and isinstance(cand, list):
        if len(base) != len(cand):
            changes.append({"path": prefix or "$", "change": "length",
                            "baseline": len(base), "candidate": len(cand)})
        for i in range(min(len(base), len(cand))):
            changes.extend(_content_changes(base[i], cand[i], f"{prefix}[{i}]"))
    elif base != cand:
        changes.append({"path": prefix or "$", "change": "value"})
    return changes


# --- the diff ----------------------------------------------------------------

def _sev_max(*sevs: str) -> str:
    return max(sevs, key=lambda s: SEV_ORDER.get(s, 0)) if sevs else "info"


def _attribute_expectation(text: str, type_to_agent: dict[str, str]) -> Optional[str]:
    """Best-effort: an expectation like 'FinalReport.summary...' -> its agent."""
    head = text.split(".", 1)[0].split()[0] if text else ""
    return type_to_agent.get(head)


def diff_runs(baseline: Run, candidate: Run) -> dict:
    # Comparability gate (Roadmap §comparability): different goal => not comparable.
    if baseline.goal and candidate.goal and baseline.goal != candidate.goal:
        return _envelope(baseline, candidate, "inconclusive", [],
                         note="baseline and candidate ran different goals/inputs")

    type_to_agent: dict[str, str] = {}
    for run in (baseline, candidate):
        for info in run.plan_steps.values():
            if info.get("output_type") and info.get("owner"):
                type_to_agent[info["output_type"]] = info["owner"]

    agent_diffs: list[dict] = []
    new_blocker_failures = 0
    schema_regressions = 0

    # 1) Per-step (per-agent) signals from plan + run.log.
    step_ids = sorted(set(baseline.steps) | set(candidate.steps)
                      | set(baseline.plan_steps) | set(candidate.plan_steps))
    for sid in step_ids:
        meta = candidate.plan_steps.get(sid) or baseline.plan_steps.get(sid) or {}
        agent = meta.get("owner") or sid
        contract = meta.get("output_type")
        b = baseline.steps.get(sid)
        c = candidate.steps.get(sid)
        signals: list[dict] = []

        # status: ok -> failed is a hard regression
        if b and c and b.get("status") == "ok" and c.get("status") != "ok":
            signals.append({"kind": "step_failed", "severity": "regression",
                            "message": f"step '{sid}' status {b['status']} -> {c.get('status')}",
                            "baseline": b.get("status"), "candidate": c.get("status")})

        # criteria: PASS -> FAIL is a regression; FAIL -> PASS is an improvement (info)
        if b and c:
            for crit in sorted(set(b["criteria"]) | set(c["criteria"])):
                bp, cp = b["criteria"].get(crit), c["criteria"].get(crit)
                if bp is True and cp is False:
                    signals.append({"kind": "criterion_failed", "severity": "regression",
                                    "message": f"criterion no longer passes: {crit}",
                                    "baseline": "PASS", "candidate": "FAIL"})
                elif bp is False and cp is True:
                    signals.append({"kind": "criterion_fixed", "severity": "info",
                                    "message": f"criterion now passes: {crit}",
                                    "baseline": "FAIL", "candidate": "PASS"})

            # cost: more calls is a soft signal
            for kind, key in (("llm_calls", "llm_calls"), ("tool_calls", "tool_calls")):
                delta = c.get(key, 0) - b.get(key, 0)
                if delta > 0:
                    signals.append({"kind": f"{kind}_increase", "severity": "warning",
                                    "message": f"{kind} {b.get(key, 0)} -> {c.get(key, 0)} (+{delta})",
                                    "baseline": b.get(key, 0), "candidate": c.get(key, 0)})

        if signals:
            severity = _sev_max(*(s["severity"] for s in signals))
            diff = {"agent": agent, "step_id": sid, "contract": contract,
                    "severity": severity, "signals": signals}
            if severity == "regression":
                diff["recommended_next_action"] = (
                    f"run analyzer on {agent} with baseline/candidate context")
            agent_diffs.append(diff)

    # 2) Schema dimension on the final typed output.
    b_valid = _schema_validity(baseline.final_output, baseline.final_output_type)
    c_valid = _schema_validity(candidate.final_output, candidate.final_output_type)
    if b_valid is True and c_valid is False:
        schema_regressions += 1
        agent = type_to_agent.get(candidate.final_output_type or "", "(final_output)")
        agent_diffs.append({
            "agent": agent, "step_id": "(final_output)",
            "contract": candidate.final_output_type, "severity": "regression",
            "signals": [{"kind": "schema_invalid", "severity": "regression",
                         "message": f"final_output no longer validates as "
                                    f"{candidate.final_output_type}",
                         "baseline": "VALID", "candidate": "INVALID"}],
            "recommended_next_action": f"run analyzer on {agent} with baseline/candidate context",
        })

    # 2b) the run itself going from ok -> failed
    if not baseline.run_failed and candidate.run_failed:
        agent_diffs.append({
            "agent": "(run)", "step_id": "(run)", "contract": None,
            "severity": "regression",
            "signals": [{"kind": "run_failed", "severity": "regression",
                         "message": "candidate run failed; baseline succeeded",
                         "baseline": "ok", "candidate": "failed"}],
        })

    # 3) Grading dimension — per-expectation verdict drift (blockers emphasized).
    if baseline.grading and candidate.grading:
        b_blocker = baseline.grading.get("blocker_failed")
        c_blocker = candidate.grading.get("blocker_failed")
        if b_blocker is False and c_blocker is True:
            new_blocker_failures += 1
        b_verdicts = {r["expectation"]: r for r in baseline.grading.get("results", [])}
        c_verdicts = {r["expectation"]: r for r in candidate.grading.get("results", [])}
        for exp in sorted(set(b_verdicts) | set(c_verdicts)):
            bv = (b_verdicts.get(exp) or {}).get("verdict")
            cr = c_verdicts.get(exp) or {}
            cv = cr.get("verdict")
            if bv == "PASS" and cv == "FAIL":
                is_blocker = bool(cr.get("blocker"))
                if is_blocker:
                    new_blocker_failures += 1
                agent = _attribute_expectation(exp, type_to_agent) or "(grading)"
                agent_diffs.append({
                    "agent": agent, "step_id": "(grading)", "contract": None,
                    "severity": "regression",
                    "signals": [{
                        "kind": "blocker_failed" if is_blocker else "expectation_failed",
                        "severity": "regression",
                        "message": ("blocker" if is_blocker else "expectation")
                                   + f" no longer passes: {exp}",
                        "baseline": "PASS", "candidate": "FAIL"}],
                    "recommended_next_action": f"run analyzer on {agent} with baseline/candidate context",
                })
    elif bool(baseline.grading) != bool(candidate.grading):
        agent_diffs.append({
            "agent": "(grading)", "step_id": "(grading)", "contract": None,
            "severity": "inconclusive",
            "signals": [{"kind": "grading_missing", "severity": "inconclusive",
                         "message": "grading.json present on only one side; "
                                    "re-run with --grade for blocker comparison",
                         "baseline": bool(baseline.grading),
                         "candidate": bool(candidate.grading)}]})

    # 4) Roll up the verdict.
    severities = [d["severity"] for d in agent_diffs]
    if "regression" in severities:
        verdict = "regression"
    elif "warning" in severities:
        verdict = "warning"
    elif "inconclusive" in severities:
        verdict = "inconclusive"
    else:
        verdict = "pass"

    env = _envelope(baseline, candidate, verdict, agent_diffs)
    bt, ct = baseline.totals(), candidate.totals()
    lat = (None if bt["duration_ms"] is None or ct["duration_ms"] is None
           else ct["duration_ms"] - bt["duration_ms"])
    env["summary"] = {
        "changed_agents": sorted({d["agent"] for d in agent_diffs
                                  if d["severity"] in ("regression", "warning")}),
        "new_blocker_failures": new_blocker_failures,
        "schema_regressions": schema_regressions,
        "cost_delta_llm_calls": ct["llm_calls"] - bt["llm_calls"],
        "cost_delta_tool_calls": ct["tool_calls"] - bt["tool_calls"],
        "latency_delta_ms": lat,
    }
    return env


def _envelope(baseline: Run, candidate: Run, verdict: str,
              agent_diffs: list[dict], note: Optional[str] = None) -> dict:
    env = {
        "baseline": str(baseline.path),
        "candidate": str(candidate.path),
        "verdict": verdict,
        "agent_diffs": agent_diffs,
    }
    if note:
        env["note"] = note
    return env


# --- rendering ---------------------------------------------------------------

_VERDICT_BADGE = {"pass": "✅ PASS", "warning": "⚠️ WARNING",
                  "regression": "❌ REGRESSION", "inconclusive": "❔ INCONCLUSIVE"}


def to_markdown(env: dict) -> str:
    lines = [f"# Run diff — {_VERDICT_BADGE.get(env['verdict'], env['verdict'])}", ""]
    lines += [f"- **baseline:** `{env['baseline']}`",
              f"- **candidate:** `{env['candidate']}`"]
    if env.get("note"):
        lines.append(f"- **note:** {env['note']}")
    s = env.get("summary")
    if s:
        lines += ["", "## Summary", ""]
        lines.append(f"- changed agents: {', '.join(s['changed_agents']) or '(none)'}")
        lines.append(f"- new blocker failures: {s['new_blocker_failures']}")
        lines.append(f"- schema regressions: {s['schema_regressions']}")
        lines.append(f"- Δ llm_calls: {s['cost_delta_llm_calls']:+d}   "
                     f"Δ tool_calls: {s['cost_delta_tool_calls']:+d}")
        if s["latency_delta_ms"] is not None:
            lines.append(f"- Δ latency: {s['latency_delta_ms']:+d} ms")
    if env["agent_diffs"]:
        lines += ["", "## Agent diffs", ""]
        for d in env["agent_diffs"]:
            head = f"### `{d['agent']}`"
            if d.get("contract"):
                head += f" → `{d['contract']}`"
            head += f" — {_VERDICT_BADGE.get(d['severity'], d['severity'])}"
            lines.append(head)
            for sig in d["signals"]:
                lines.append(f"- **{sig['kind']}**: {sig['message']}")
            if d.get("recommended_next_action"):
                lines.append(f"- ↳ _next:_ {d['recommended_next_action']}")
            lines.append("")
    else:
        lines += ["", "_No differences detected._"]
    return "\n".join(lines).rstrip() + "\n"


# --- cli ---------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compare two ROBOPORT run directories.")
    ap.add_argument("--baseline", required=True, type=Path, help="Baseline run dir")
    ap.add_argument("--candidate", required=True, type=Path, help="Candidate run dir")
    ap.add_argument("--out", type=Path, default=None, help="Write the diff JSON here")
    ap.add_argument("--markdown", type=Path, default=None, help="Write a Markdown summary here")
    ap.add_argument("--fail-on", choices=["regression", "warning", "inconclusive"],
                    default="regression",
                    help="Verdict severity at which to exit nonzero (default: regression)")
    ap.add_argument("--quiet", action="store_true", help="Suppress stdout JSON")
    args = ap.parse_args(argv)

    for label, p in (("baseline", args.baseline), ("candidate", args.candidate)):
        if not p.is_dir():
            print(f"{label} run dir not found: {p}", file=sys.stderr)
            return 2

    env = diff_runs(Run(args.baseline), Run(args.candidate))
    blob = json.dumps(env, indent=2, sort_keys=True)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(blob + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(to_markdown(env), encoding="utf-8")
    if not args.quiet:
        print(blob)

    # Gating: --fail-on names the least-severe verdict that should fail the build.
    fail_sets = {
        "regression": {"regression"},
        "warning": {"regression", "warning"},
        "inconclusive": {"regression", "warning", "inconclusive"},
    }
    exit_codes = {"regression": 1, "warning": 1, "inconclusive": 2}
    verdict = env["verdict"]
    if verdict in fail_sets[args.fail_on]:
        return exit_codes.get(verdict, 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
