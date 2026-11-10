#!/usr/bin/env python3
"""Phase 5 — crew-expansion governance gate.

Prevents breadth from outrunning proof by enforcing the mechanically-checkable
expansion rules from docs/ROADMAP.md against the repo's own artifacts, with a
diff_runs-style exit code so CI can gate on it:

  R1  every eval carries at least one blocker (no new agent lands unproven).
  R2  each crew's eval set covers all four proof kinds — happy_path, empty,
      schema_failure, evidence_grounding.
  R4  every crew station maps to a registered agent and every edge declares its
      output contract (`type`).

R3 (run artifacts compatible with diff_runs) is enforced at runtime by
`diff_runs.py`; R5 (generic-agent growth needs a justifying eval) is a review-time
judgment — both are reported as advisories here, not gated.

Exit codes: 0 clean · 1 governance violation · 2 usage/load error.

    python scripts/governance.py                      # gate the repo
    python scripts/governance.py --json               # machine-readable report
    python scripts/governance.py --rule R1 --rule R2  # a subset of rules
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

REQUIRED_EVAL_KINDS = ("happy_path", "empty", "schema_failure", "evidence_grounding")
ALL_RULES = ("R1", "R2", "R4")


def _violation(rule: str, subject: str, message: str) -> dict:
    return {"rule": rule, "severity": "violation", "subject": subject, "message": message}


# --- R1: blocker discipline --------------------------------------------------

def check_blockers(eval_set: dict) -> list[dict]:
    """R1: every eval must carry at least one blocker."""
    target = eval_set.get("target", "?")
    out = []
    for e in eval_set.get("evals", []):
        if not e.get("blockers"):
            out.append(_violation(
                "R1", f"{target}#eval-{e.get('id')}",
                "eval has no blocker — a failure here would not fail the eval"))
    return out


# --- R2: proof-kind coverage -------------------------------------------------

def check_kind_coverage(eval_set: dict) -> list[dict]:
    """R2: the crew's eval set must cover all four required proof kinds."""
    target = eval_set.get("target", "?")
    covered = {e.get("kind") for e in eval_set.get("evals", []) if e.get("kind")}
    out = []
    for kind in REQUIRED_EVAL_KINDS:
        if kind not in covered:
            out.append(_violation(
                "R2", f"{target}:{kind}",
                f"no eval of kind '{kind}' — crew proof set is incomplete"))
    return out


def check_crews_have_evals(registry: dict, eval_sets: list[dict]) -> list[dict]:
    """R2: every crew in the registry must have an eval set (matched by target) —
    no crew lands without its proof set."""
    targets = {es.get("target") for es in eval_sets}
    out = []
    for cname in sorted(registry.get("crews") or {}):
        if cname not in targets:
            out.append(_violation(
                "R2", cname, "crew has no eval set — cannot prove the four kinds"))
    return out


# --- R4: station / contract integrity ----------------------------------------

def check_stations(registry: dict) -> list[dict]:
    """R4: every crew station maps to a registered agent, and every edge declares
    its output contract (`type`). A station may be a runnable agent OR an offline
    Theragen catalog record (`theragen_agents`) — a modeled crew can wire a real
    external agent as a station, which the catalog makes a first-class registry
    citizen; it is still never executed."""
    agents = set(registry.get("agents", {})) | set(registry.get("theragen_agents", {}))
    out = []
    for cname, crew in (registry.get("crews") or {}).items():
        stations = {crew.get("entry")} if crew.get("entry") else set()
        for edge in crew.get("edges", []):
            stations |= {edge.get("from"), edge.get("to")}
            if not edge.get("type"):
                out.append(_violation(
                    "R4", f"{cname}:{edge.get('from')}->{edge.get('to')}",
                    "crew edge has no output contract (`type`)"))
        for st in sorted(s for s in stations if s):
            if st not in agents:
                out.append(_violation(
                    "R4", f"{cname}:{st}",
                    "crew station is not a registered agent"))
    return out


# --- orchestration -----------------------------------------------------------

def discover_eval_sets(evals_dir: Path) -> list[dict]:
    """Every *.json under evals/ that is an eval set (has a top-level `evals` list).
    Skips non-eval-set files such as fault_injection.json (which has `scenarios`)."""
    out = []
    for p in sorted(evals_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(d, dict) and isinstance(d.get("evals"), list):
            out.append(d)
    return out


def run_governance(eval_sets: list[dict], registry: dict,
                   rules: tuple[str, ...] = ALL_RULES) -> dict:
    """Run the selected rules; return a report with every violation found."""
    violations: list[dict] = []
    if "R1" in rules:
        for es in eval_sets:
            violations.extend(check_blockers(es))
    if "R2" in rules:
        for es in eval_sets:
            violations.extend(check_kind_coverage(es))
        violations.extend(check_crews_have_evals(registry, eval_sets))
    if "R4" in rules:
        violations.extend(check_stations(registry))
    by_rule = {r: sum(1 for v in violations if v["rule"] == r) for r in rules}
    return {"ok": not violations, "rules": list(rules),
            "violations": violations, "counts_by_rule": by_rule}


def _render(report: dict) -> str:
    lines = ["ROBOPORT governance gate (Phase 5)", ""]
    if report["ok"]:
        lines.append(f"  PASS — no violations across rules {', '.join(report['rules'])}")
    else:
        lines.append(f"  FAIL — {len(report['violations'])} violation(s)")
        for v in report["violations"]:
            lines.append(f"    [{v['rule']}] {v['subject']}: {v['message']}")
    lines.append("")
    lines.append("  advisory: R3 (diff_runs artifact compat) is enforced at runtime; "
                 "R5 (generic-agent growth) is a review-time judgment.")
    return "\n".join(lines)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Enforce ROBOPORT crew-expansion governance.")
    ap.add_argument("--eval-set", type=Path, action="append", default=None,
                    help="Eval set JSON (repeatable). Default: every eval set under evals/.")
    ap.add_argument("--registry", type=Path, default=REPO / "agents" / "registry.json",
                    help="Agent/crew registry JSON.")
    ap.add_argument("--rule", action="append", choices=list(ALL_RULES), default=None,
                    help="Run only these rules (repeatable). Default: all.")
    ap.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    ap.add_argument("--quiet", action="store_true", help="Suppress stdout on pass.")
    args = ap.parse_args()

    try:
        eval_sets = ([_load_json(p) for p in args.eval_set] if args.eval_set
                     else discover_eval_sets(REPO / "evals"))
        registry = _load_json(args.registry)
    except (OSError, json.JSONDecodeError) as e:
        print(f"governance: could not load inputs: {e}", file=sys.stderr)
        return 2

    rules = tuple(args.rule) if args.rule else ALL_RULES
    report = run_governance(eval_sets, registry, rules)

    if args.json:
        print(json.dumps(report, indent=2))
    elif not (report["ok"] and args.quiet):
        print(_render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
