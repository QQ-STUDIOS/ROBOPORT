"""Grader: evaluate a run's expectations against the produced artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .budget import fits_local_context
from .client import call_model_json, load_agent_spec

GRADING_SCHEMA = {
    "type": "object",
    "required": ["results", "pass_rate", "blocker_failed"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["expectation", "verdict", "evidence"],
                "properties": {
                    "expectation": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL", "INCONCLUSIVE"]},
                    "evidence": {"type": "string"},
                    "blocker": {"type": "boolean"},
                },
            },
        },
        "pass_rate": {"type": "number"},
        "blocker_failed": {"type": "boolean"},
        "meta_critique": {"type": "array", "items": {"type": "string"}},
    },
}


def _read(p: Path | None) -> str:
    if p is None or not p.exists():
        return "(not produced)"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"(read error: {e})"


def _read_json(p: Path) -> str:
    """Lossless whitespace compaction; never truncate evidence to fit a budget."""
    text = _read(p)
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        # Keep malformed evidence visible; the normal context limit still applies.
        return text


def _step_outputs(outputs_dir: Path) -> str:
    """Each step's typed output, so expectations about intermediate agents
    (for example the investigator's findings) have evidence to grade."""
    rows = {}
    for path in sorted((outputs_dir / "steps").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            rows[path.stem] = {"status": data.get("status"), "output": data.get("output")}
    return json.dumps(rows, ensure_ascii=False, separators=(',', ':')) if rows else "(none)"


def call_grader(
    expectations: list[str],
    transcript_path: Path | None,
    outputs_dir: Path,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    final_output_path = outputs_dir / "final_output.json"
    plan_path = outputs_dir / "plan.json"

    system_spec = load_agent_spec("agents/evaluation/grader.md")

    user = (
        "EXPECTATIONS\n"
        + "\n".join(f"- {e}" for e in expectations)
        + "\n\nPLAN\n" + _read_json(plan_path)
        + "\n\nFINAL OUTPUT\n" + _read_json(final_output_path)
        + "\n\nRUN LOG (JSONL)\n" + _read(transcript_path)
        + "\n\nGrade each expectation. PASS only with concrete evidence from "
          "the artifacts. FAIL on contradiction. INCONCLUSIVE when artifacts "
          "are insufficient. pass_rate = PASS / total. blocker_failed=true "
          "if any FAIL is on a blocker-class expectation. JSON ONLY."
    )

    try:
        try:
            final = json.loads(_read(final_output_path))
        except ValueError:
            final = None
        if isinstance(final, dict) and isinstance(final.get('evidence'), dict) and final['evidence'].get('rea_version'):
            from .investigation import source_handoff
            source = source_handoff(final['evidence'], outputs_dir)
            user += '\n\nINSPECTED SOURCE DATA\n' + json.dumps(source, ensure_ascii=False) + '\n\n'
            user += ('Verify factual claims against these static samples, not upstream interpretations. '
                     'Check loop-dependent counts, operation complexity and return values, behavior preservation, '
                     'and, for performance proposals, at least three matched before/after runs in the proposed validation. '
                     'FAIL on contradiction or omitted requirements; INCONCLUSIVE if needed source is missing. '
                     'Do not treat a proposed benchmark as an executed measurement.')
        # Step outputs are supporting evidence for expectations about
        # intermediate agents. Add them only when the prompt still fits the
        # context ceiling, so they never make an otherwise gradable run fail;
        # without them, such an expectation is INCONCLUSIVE, never a PASS.
        with_steps = user + "\n\nSTEP OUTPUTS (by step id)\n" + _step_outputs(outputs_dir)
        if fits_local_context(system=system_spec, messages=[{"role": "user", "content": with_steps}],
                              tools=None, force_json=True, model_hint="reasoning-strong"):
            user = with_steps
        else:
            user += "\n\nSTEP OUTPUTS\n(omitted: they would exceed the context ceiling)"
        result = call_model_json(
            system_spec=system_spec,
            user_prompt=user,
            model_hint="reasoning-strong",
            schema=GRADING_SCHEMA,
        )
    except Exception as e:  # noqa: BLE001
        result = {
            "results": [
                {"expectation": ex, "verdict": "INCONCLUSIVE",
                 "evidence": f"grader error: {e!r}"}
                for ex in expectations
            ],
            "pass_rate": 0.0,
            "blocker_failed": True,  # ungraded: fail closed
            "meta_critique": [f"grader exception: {e!r}"],
        }

    rows = result.get('results') or []
    matched = len(rows) == len(expectations) and sorted(r.get('expectation', '') for r in rows) == sorted(expectations)
    passed = sum(r.get('verdict') == 'PASS' and bool(r.get('evidence')) for r in rows)
    result['pass_rate'] = passed / len(expectations) if expectations and matched else 0.0
    # Acceptance (every expectation PASS) is pass_rate == 1.0; blocker_failed
    # reports only blocker-class misses, which aggregate and diff_runs key on.
    declared = set(blockers or [])
    blocker_miss = any((r.get('blocker') or r.get('expectation') in declared)
                       and not (r.get('verdict') == 'PASS' and r.get('evidence')) for r in rows)
    result['blocker_failed'] = not matched or blocker_miss or bool(result.get('blocker_failed'))
    return {
        "run_id": outputs_dir.name,
        "results": result["results"],
        "pass_rate": result["pass_rate"],
        "blocker_failed": result.get("blocker_failed", False),
        "meta_critique": result.get("meta_critique", []),
        "graded_at": datetime.now(timezone.utc).isoformat(),
    }
