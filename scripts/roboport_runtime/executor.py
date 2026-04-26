"""Executor: run a single plan step against the owner agent."""
from __future__ import annotations

from typing import Any

from .client import call_model_json, load_agent_spec, model_for

STEP_RESULT_SCHEMA = {
    "type": "object",
    "required": ["status", "output", "criteria_results"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "failed"]},
        "output": {"type": "object"},
        "criteria_results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["criterion", "passed"],
                "properties": {
                    "criterion": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "error": {"type": ["string", "null"]},
    },
}


def call_executor(step: dict, accumulated: dict, registry: dict) -> dict[str, Any]:
    owner = step["owner"]
    agent_meta = registry["agents"].get(owner)
    if agent_meta is None:
        return _failed(step, f"unknown agent: {owner}")

    # Deterministic agents (e.g., synthesizer) don't get an LLM call.
    if agent_meta.get("deterministic") and agent_meta.get("model_hint") == "none":
        return {
            "step_id": step["id"],
            "status": "ok",
            "output": {"deterministic_stub": True, "owner": owner, "input": step["input"]},
            "criteria_results": [
                {"criterion": c, "passed": True,
                 "evidence": "deterministic agent — not LLM-evaluated"}
                for c in step.get("success_criteria", [])
            ],
            "tool_calls": 0,
            "llm_calls": 0,
            "transcript_path": None,
            "error": None,
        }

    system_spec = load_agent_spec(agent_meta["path"])
    user = (
        f"STEP\nid: {step['id']}\nwave: {step.get('wave', 0)}\n"
        f"input: {step['input']}\n"
        f"output_type: {step.get('output_type', 'object')}\n\n"
        f"SUCCESS CRITERIA\n"
        + "\n".join(f"- {c}" for c in step.get("success_criteria", []))
        + "\n\n"
        f"PRIOR STEP OUTPUTS (by step id)\n{accumulated or '(none)'}\n\n"
        "Execute this step per your agent spec. Return JSON ONLY conforming "
        "to the schema. Put your typed output (matching `output_type`) in "
        "`output`. Mark each success criterion with passed=true/false and "
        "brief evidence."
    )

    try:
        result = call_model_json(
            system_spec=system_spec,
            user_prompt=user,
            model=model_for(agent_meta),
            schema=STEP_RESULT_SCHEMA,
        )
    except Exception as e:  # noqa: BLE001
        return _failed(step, f"executor exception: {e!r}")

    return {
        "step_id": step["id"],
        "status": result.get("status", "ok"),
        "output": result.get("output", {}),
        "criteria_results": result.get("criteria_results", []),
        "tool_calls": 0,
        "llm_calls": 1,
        "transcript_path": None,
        "error": result.get("error"),
    }


def _failed(step: dict, msg: str) -> dict[str, Any]:
    return {
        "step_id": step["id"],
        "status": "failed",
        "output": {},
        "criteria_results": [],
        "tool_calls": 0,
        "llm_calls": 0,
        "transcript_path": None,
        "error": msg,
    }
