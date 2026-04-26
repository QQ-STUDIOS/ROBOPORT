"""Planner: decompose a goal into an ordered, typed plan."""
from __future__ import annotations

from typing import Any

from .client import call_model_json, load_agent_spec

PLAN_SCHEMA = {
    "type": "object",
    "required": ["goal", "deliverable", "steps"],
    "properties": {
        "goal": {"type": "string"},
        "deliverable": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "owner", "wave", "input", "output_type",
                             "success_criteria", "deterministic"],
                "properties": {
                    "id": {"type": "string"},
                    "owner": {"type": "string"},
                    "wave": {"type": "integer"},
                    "input": {"type": "object"},
                    "output_type": {"type": "string"},
                    "success_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "deterministic": {"type": "boolean"},
                },
            },
        },
        "estimated_llm_calls": {"type": "integer"},
        "estimated_tool_calls": {"type": "integer"},
        "fallback": {"type": "string"},
    },
}


def call_planner(goal: str, context: dict, registry: dict) -> dict[str, Any]:
    planner_meta = registry["agents"]["planner"]
    system_spec = load_agent_spec(planner_meta["path"])

    available = {name: meta.get("role", "") for name, meta in registry["agents"].items()}
    crews = list(registry.get("crews", {}).keys())

    user = (
        f"GOAL\n{goal}\n\n"
        f"CONTEXT\n{context or '(none)'}\n\n"
        f"AVAILABLE AGENTS (id -> role)\n{available}\n\n"
        f"AVAILABLE CREWS\n{crews}\n\n"
        "Produce a plan as JSON conforming to the schema. "
        "Set `owner` on every step to one of the available agent ids. "
        "Group independent steps into the same `wave`. Output JSON ONLY."
    )

    plan = call_model_json(
        system_spec=system_spec,
        user_prompt=user,
        model_hint=planner_meta.get("model_hint", "any"),
        schema=PLAN_SCHEMA,
    )
    plan.setdefault("estimated_llm_calls", len(plan.get("steps", [])))
    plan.setdefault("estimated_tool_calls", 0)
    plan.setdefault("fallback", "n/a")
    return plan
