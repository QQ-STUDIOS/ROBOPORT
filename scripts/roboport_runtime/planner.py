"""Planner: decompose a goal into an ordered, typed plan."""
from __future__ import annotations

from typing import Any
from copy import deepcopy
import json
import re
from collections import deque
from .client import REPO

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


def _context_paths(context):
    queue = deque([('', context)])
    result = []
    while queue and len(result) < 256:
        pointer, value = queue.popleft()
        result.append(pointer)
        children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else []
        for key, child in children:
            if len(queue) + len(result) >= 256:
                break
            token = str(key).replace('~', '~0').replace('/', '~1')
            queue.append((pointer + '/' + token, child))
    return result


def _pointer(context, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')) or re.search(r'~(?![01])', pointer):
        raise ValueError('Invalid planner input pointer')
    value = context
    try:
        for part in pointer[1:].split('/') if pointer else []:
            key = part.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not key.isdigit() or str(int(key)) != key:
                    raise ValueError('Invalid planner array index')
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('Planner context binding does not exist') from exc
    return deepcopy(value)


def _bind(target, pointer, value):
    if pointer == '':
        if not isinstance(value, dict):
            raise ValueError('Root planner input binding must be an object')
        if target.keys() & value.keys():
            raise ValueError('Planner input binding collision')
        target.update(value)
        return
    if not isinstance(pointer, str) or not pointer.startswith('/') or re.search(r'~(?![01])', pointer):
        raise ValueError('Invalid planner binding target')
    parts = [p.replace('~1', '/').replace('~0', '~') for p in pointer[1:].split('/')]
    current = target
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        if not isinstance(current[part], dict):
            raise ValueError('Planner input binding overlap')
        current = current[part]
    if parts[-1] in current:
        raise ValueError('Planner input binding collision')
    current[parts[-1]] = value


def _draft_schema(available, context):
    from station_packs import load_output_schema
    contracts = list(load_output_schema(REPO)['definitions'])
    contracts += ['list[' + name + ']' for name in contracts]
    pointers = _context_paths(context)
    def context_schema(pointer_schema, target_pattern):
        return {'type': 'object', 'additionalProperties': False,
            'required': ['source', 'pointer', 'target'], 'properties': {
                'source': {'const': 'context'}, 'pointer': pointer_schema,
                'target': {'type': 'string', 'pattern': target_pattern}}}
    context_variants = []
    if len(pointers) < 256:
        objects = [pointer for pointer in pointers if isinstance(_pointer(context, pointer), dict)]
        fields = [pointer for pointer in pointers if pointer not in objects]
        if objects:
            context_variants.append(context_schema({'enum': objects}, '^(/.*)?$'))
        if fields:
            context_variants.append(context_schema({'enum': fields}, '^/.+'))
    else:
        context_variants.append(context_schema({'type': 'string', 'pattern': r'^(?:/(?:[^~/]|~[01])*)*$'}, '^(/.*)?$'))
    step_binding = {'type': 'object', 'additionalProperties': False,
        'required': ['source', 'step_index'], 'properties': {
            'source': {'const': 'step'}, 'step_index': {'type': 'integer', 'minimum': 0, 'maximum': 31}}}
    step = {'type': 'object', 'additionalProperties': False,
        'required': ['owner', 'output_type', 'success_criteria', 'input_bindings', 'input_literals', 'depends_on'],
        'properties': {'owner': {'enum': list(available)}, 'output_type': {'enum': contracts},
            'success_criteria': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
            'input_bindings': {'type': 'array', 'uniqueItems': True, 'items': {'oneOf': [*context_variants, step_binding]}},
            'input_literals': {'type': 'object'},
            'depends_on': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 31}}}}
    plan = {'type': 'object', 'additionalProperties': False, 'required': ['goal', 'deliverable', 'steps'],
        'properties': {'goal': {'type': 'string'}, 'deliverable': {'type': 'string'},
            'steps': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'items': step},
            'estimated_tool_calls': {'type': 'integer', 'minimum': 0}, 'fallback': {'type': 'string'}}}
    refusal = {'type': 'object', 'additionalProperties': False, 'required': ['rejection'],
        'properties': {'rejection': {'type': 'object', 'additionalProperties': False, 'required': ['reason'],
            'properties': {'reason': {'type': 'string', 'minLength': 1}, 'unavailable_owner': {'type': 'string'}}}}}
    return {'oneOf': [plan, refusal]}


def _compile_plan(proposal, context, available):
    if 'rejection' in proposal:
        rejection = proposal['rejection']
        raise ValueError(f"Planner rejected request: {rejection['reason']}; unavailable owner: {rejection.get('unavailable_owner', '(unspecified)')}")
    result = {key: deepcopy(value) for key, value in proposal.items() if key != 'steps'}
    result['steps'] = []
    for index, item in enumerate(proposal['steps']):
        owner = item.get('owner')
        if owner not in available:
            raise ValueError(f'Unknown planner step owner: {owner!r}')
        if not {'input_bindings', 'input_literals', 'depends_on'} <= item.keys():
            raise ValueError('Planner proposal requires structured input bindings')
        bindings = item['input_bindings']
        inputs = deepcopy(item['input_literals'])
        json.dumps(inputs, allow_nan=False)
        if not isinstance(inputs, dict):
            raise ValueError('Planner literal input must be an object')
        def has_placeholder(value):
            if isinstance(value, dict):
                return any(has_placeholder(k) or has_placeholder(v) for k, v in value.items())
            if isinstance(value, list):
                return any(has_placeholder(v) for v in value)
            return isinstance(value, str) and bool(re.fullmatch(r'<[^>]*>\.output|[A-Za-z_]\w*\.output', value))
        if has_placeholder(inputs):
            raise ValueError('Prior-step inputs must use structured bindings, not literal references')
        dependencies = set()
        def earlier(value):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < index:
                raise ValueError('Planner dependency must name an earlier step index')
            dependencies.add(value)
            return result['steps'][value]
        for dependency in item.get('depends_on', []):
            earlier(dependency)
        prior_outputs = {}
        for binding in bindings:
            if binding['source'] == 'context':
                _bind(inputs, binding['target'], _pointer(context, binding['pointer']))
            elif binding['source'] == 'step':
                source = earlier(binding['step_index'])
                if source['id'] in prior_outputs:
                    raise ValueError('Duplicate planner prior-output binding')
                prior_outputs[source['id']] = source['id'] + '.output'
            else:
                raise ValueError('Unknown planner binding source')
        if prior_outputs:
            _bind(inputs, '/prior_outputs', prior_outputs)
        if owner == 'improvement_proposer':
            investigations = [result['steps'][d] for d in dependencies
                              if result['steps'][d]['owner'] == 'environment_investigator'
                              and result['steps'][d]['output_type'] == 'EnvironmentInvestigation']
            if len(investigations) != 1:
                raise ValueError('Improvement proposer requires one unambiguous investigation dependency')
            investigation_id = investigations[0]['id']
            if 'investigation_step' in inputs and inputs['investigation_step'] != investigation_id:
                raise ValueError('Planner investigation control binding contradicts its dependency')
            inputs['investigation_step'] = investigation_id
        wave = max((result['steps'][d]['wave'] + 1 for d in dependencies), default=0)
        result['steps'].append({'id': f's{index + 1}', 'owner': owner, 'wave': wave, 'input': inputs,
                                'output_type': item['output_type'], 'success_criteria': item['success_criteria'],
                                'deterministic': available[owner]['deterministic']})
    result['estimated_llm_calls'] = sum(not step['deterministic'] for step in result['steps'])
    return result


def call_planner(goal: str, context: dict, registry: dict) -> dict[str, Any]:
    if context.get("target") == "environment_investigation":
        from .investigation import build_plan
        return build_plan({**context, "operator_objective": context.get("operator_objective", goal)})
    if context.get("target") == "jd_crew" and context.get("deployment_fixture") is True:
        from deployment_smoke import build_plan
        return build_plan()
    required_owner = context.get('required_owner')
    if required_owner is not None and not isinstance(required_owner, str):
        raise ValueError('Required owner must be a registered string ID')
    if required_owner is not None and required_owner not in registry['agents']:
        raise ValueError(f'Unavailable required owner: {required_owner}')
    planner_meta = registry["agents"]["planner"]
    system_spec = load_agent_spec(planner_meta["path"])

    available = {
        name: {"role": meta.get("role", ""),
               "deterministic": meta.get("deterministic") is True}
        for name, meta in registry["agents"].items()
    }
    crews = registry.get('crews', {})
    structured = context.get('target') != 'dataops_crew'
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


    if structured:
        schema = _draft_schema(available, context)
        system_spec += (
            '\nUse structured input_bindings. Select context values by exact JSON pointer; do not copy their contents into input_literals. '
            'A target of empty string binds the selected whole object as the entire input: this preserves its exact shape. '
            'For one complete object from a context array use pointer /items/0 and target empty string, not a new wrapper. '
            'Arrays and scalar values always require a named target /field, such as /transactions or /untrusted_text; they cannot be whole object inputs. Use empty target only for object values. '
            'Each step binding names only a zero-based earlier step_index, with no target. Runtime groups these references under input.prior_outputs keyed by actual step IDs. Never write <ID>.output or any free-text dependency. '
            'depends_on lists zero-based earlier control dependencies. Runtime owns IDs, references, waves and execution flags. '
            'Use input_literals only for constants not supplied in context. Reject unavailable capabilities with the rejection branch; never invent a rejection output contract.')
    user = (
        f"GOAL\n{goal}\n\n"
        f"CONTEXT\n{json.dumps(context, ensure_ascii=False, allow_nan=False)}\n\n"
        f"AVAILABLE AGENTS (id -> registry metadata)\n{available}\n\n"
        f"AVAILABLE CREWS\n{json.dumps(crews)}\n\n"
        "Produce a plan as JSON conforming to the schema. "
        "Set `owner` on every step to one of the available agent ids. "
        "Execution flags are assigned from registry metadata. "
        "Group independent steps into the same `wave`. Output JSON ONLY."
    )

    plan = call_model_json(
        system_spec=system_spec,
        user_prompt=user,
        model_hint=planner_meta.get("model_hint", "any"),
        schema=schema,
    )
    if structured:
        plan = _compile_plan(plan, context, available)
    if required_owner is not None and required_owner not in [step.get('owner') for step in plan.get('steps', [])]:
        raise ValueError(f'Required owner was not planned: {required_owner}')
    # Execution properties come from the authenticated registry, not model claims.
    for step in plan.get("steps", []):
        owner = step.get("owner")
        if owner not in available:
            raise ValueError(f"Unknown planner step owner: {owner!r}")
        step["deterministic"] = available[owner]["deterministic"]
    if context.get("target") == "dataops_crew":
        steps = plan.get("steps", [])
        contracts = [(step.get("owner"), step.get("output_type")) for step in steps]
        if contracts != [("data_engineering", "CleanedDataset"), ("reporting", "DataReport")]:
            raise ValueError("DataOps requires ordered data_engineering/CleanedDataset and reporting/DataReport steps")
    plan.setdefault("estimated_llm_calls", len(plan.get("steps", [])))
    plan.setdefault("estimated_tool_calls", 0)
    plan.setdefault("fallback", "n/a")
    return plan
