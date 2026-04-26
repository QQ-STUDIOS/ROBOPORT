"""Grader: evaluate a run's expectations against the produced artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import REPO, call_model_json, load_agent_spec, model_for

GRADING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results", "pass_rate", "blocker_failed"],
    "properties": {
        "results": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["expectation", "verdict", "evidence"],
                "properties": {
                    "expectation": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL", "INCONCLUSIVE"]},
                    "evidence": {"type": "string"},
                    "blocker": {"type": "boolean"},
                },
            },
        },
        "pass_rate": {"type": "number", "minimum": 0, "maximum": 1},
        "blocker_failed": {"type": "boolean"},
        "meta_critique": {"type": "array", "items": {"type": "string"}},
    },
}


def _load_optional(path: Path | None) -> str:
    if path is None or not path.exists():
        return "(not produced)"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"(read error: {e})"


def call_grader(
    expectations: list[str],
    transcript_path: Path | None,
    outputs_dir: Path,
) -> dict[str, Any]:
    # The benchmark harness passes the run_dir as outputs_dir; transcript is
    # the JSONL run.log inside it. final_output.json sits alongside.
    final_output_path = outputs_dir / "final_output.json"
    plan_path = outputs_dir / "plan.json"

    grader_meta_path = "agents/evaluation/grader.md"
    # The registry is the source of truth, but we don't have it here. Hard-code
    # the spec path; the grader is always reasoning-strong.
    system_spec = load_agent_spec(grader_meta_path)

    user = (
        "EXPECTATIONS\n"
        + "\n".join(f"- {e}" for e in expectations)
        + "\n\nPLAN\n"
        + _load_optional(plan_path)
        + "\n\nFINAL OUTPUT\n"
        + _load_optional(final_output_path)
        + "\n\nRUN LOG (JSONL)\n"
        + _load_optional(transcript_path)
        + "\n\n"
        "Grade each expectation against the artifacts above. PASS only when "
        "you can cite specific evidence from the artifacts. FAIL when the "
        "evidence contradicts the expectation. INCONCLUSIVE when the artifacts "
        "are insufficient to judge. Compute pass_rate as PASS / total. Set "
        "blocker_failed=true if any FAIL is on a blocker-class expectation."
    )

    try:
        result = call_model_json(
            system_spec=system_spec,
            user_prompt=user,
            model="claude-opus-4-7",
            max_tokens=6000,
            schema=GRADING_SCHEMA,
        )
    except Exception as e:  # noqa: BLE001
        result = {
            "results": [
                {"expectation": ex, "verdict": "INCONCLUSIVE", "evidence": f"grader error: {e!r}"}
                for ex in expectations
            ],
            "pass_rate": 0.0,
            "blocker_failed": False,
            "meta_critique": [f"grader exception: {e!r}"],
        }

    return {
        "run_id": outputs_dir.name,
        "results": result["results"],
        "pass_rate": result["pass_rate"],
        "blocker_failed": result.get("blocker_failed", False),
        "meta_critique": result.get("meta_critique", []),
        "graded_at": datetime.now(timezone.utc).isoformat(),
    }
