"""Planner: decompose a goal into an ordered, typed plan."""
from __future__ import annotations

from typing import Any
from copy import deepcopy
import json

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
    if context.get("target") == "environment_investigation":
        from .investigation import build_plan
        return build_plan({**context, "operator_objective": context.get("operator_objective", goal)})
    if context.get("target") == "jd_crew" and context.get("deployment_fixture") is True:
        from deployment_smoke import build_plan
        return build_plan()
    planner_meta = registry["agents"]["planner"]
    system_spec = load_agent_spec(planner_meta["path"])

    available = {name: meta.get("role", "") for name, meta in registry["agents"].items()}
    crews = registry.get('crews', {})
    schema = deepcopy(PLAN_SCHEMA)
    schema['properties']['steps']['minItems'] = 1
    props = schema['properties']['steps']['items']['properties']
    props['owner'] = {'enum': list(available)}
    props['wave']['minimum'] = 0
    if context.get('target') == 'dataops_crew':
        schema['properties']['steps'].update(minItems=2, maxItems=2)
        props['owner'] = {'enum': ['data_engineering', 'reporting']}
        props['output_type'] = {'enum': ['CleanedDataset', 'DataReport']}
        props['input'] = {'const': {}}
        props['deterministic'] = {'const': False}
        props['success_criteria'] = {'const': ['Output matches the authenticated artifact contract']}
        system_spec += '\nFor DataOps, plan exactly two ordered steps: data_engineering produces CleanedDataset, then reporting produces DataReport. Each input must be {}; runtime injects authenticated artifact evidence after planning. Both are model-assisted, not deterministic agents.'


    user = (
        f"GOAL\n{goal}\n\n"
        f"CONTEXT\n{context or '(none)'}\n\n"
        f"AVAILABLE AGENTS (id -> role)\n{available}\n\n"
        f"AVAILABLE CREWS\n{json.dumps(crews)}\n\n"
        "Produce a plan as JSON conforming to the schema. "
        "Set `owner` on every step to one of the available agent ids. "
        "Group independent steps into the same `wave`. Output JSON ONLY."
    )

    plan = call_model_json(
        system_spec=system_spec,
        user_prompt=user,
        model_hint=planner_meta.get("model_hint", "any"),
        schema=schema,
    )
    plan.setdefault("estimated_llm_calls", len(plan.get("steps", [])))
    plan.setdefault("estimated_tool_calls", 0)
    plan.setdefault("fallback", "n/a")
    return plan
