"""Grader: evaluate a run's expectations against the produced artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def call_grader(
    expectations: list[str],
    transcript_path: Path | None,
    outputs_dir: Path,
) -> dict[str, Any]:
    final_output_path = outputs_dir / "final_output.json"
    plan_path = outputs_dir / "plan.json"

    system_spec = load_agent_spec("agents/evaluation/grader.md")

    user = (
        "EXPECTATIONS\n"
        + "\n".join(f"- {e}" for e in expectations)
        + "\n\nPLAN\n" + _read(plan_path)
        + "\n\nFINAL OUTPUT\n" + _read(final_output_path)
        + "\n\nRUN LOG (JSONL)\n" + _read(transcript_path)
        + "\n\nGrade each expectation. PASS only with concrete evidence from "
          "the artifacts. FAIL on contradiction. INCONCLUSIVE when artifacts "
          "are insufficient. pass_rate = PASS / total. blocker_failed=true "
          "if any FAIL is on a blocker-class expectation. JSON ONLY."
    )

    try:
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
