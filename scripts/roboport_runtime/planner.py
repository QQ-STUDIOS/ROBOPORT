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
    if len(parts) != 1:
        raise ValueError('Nested planner destinations are unsupported; copy an intact context object')
    if parts[0] in target:
        raise ValueError('Planner input binding collision')
    target[parts[0]] = value


def _field_name(context, pointer):
    _pointer(context, pointer)
    if not pointer or not isinstance(_pointer(context, pointer.rsplit('/', 1)[0]), dict):
        raise ValueError('Field binding must select an existing named context property')
    return pointer.rsplit('/', 1)[1].replace('~1', '/').replace('~0', '~')


def _draft_schema(available, context):
    from station_packs import load_output_schema
    contracts = list(load_output_schema(REPO)['definitions'])
    contracts += ['list[' + name + ']' for name in contracts]
    pointers = _context_paths(context)
    def context_schema(pointer_schema, placement):
        return {'type': 'object', 'additionalProperties': False,
            'required': ['source', 'pointer', 'placement'], 'properties': {
                'source': {'const': 'context'}, 'pointer': pointer_schema,
                'placement': {'const': placement}}}
    if len(pointers) < 256:
        roots = [pointer for pointer in pointers if isinstance(_pointer(context, pointer), dict)]
        fields = [pointer for pointer in pointers if pointer and isinstance(_pointer(context, pointer.rsplit('/', 1)[0]), dict)]
        root_binding = context_schema({'enum': roots}, 'root')
        field_variants = [context_schema({'enum': fields}, 'field')] if fields else []
    else:
        pointer_schema = {'type': 'string', 'pattern': r'^(?:/(?:[^~/]|~[01])*)*$'}
        root_binding = context_schema(pointer_schema, 'root')
        field_variants = [context_schema(pointer_schema, 'field')]
    step_binding = {'type': 'object', 'additionalProperties': False,
        'required': ['source', 'step_index'], 'properties': {
            'source': {'const': 'step'}, 'step_index': {'type': 'integer', 'minimum': 0, 'maximum': 31}}}
    binding_array = {'oneOf': [
        {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': root_binding},
        {'type': 'array', 'uniqueItems': True, 'items': {'oneOf': [*field_variants, step_binding]}}]}
    step = {'type': 'object', 'additionalProperties': False,
        'required': ['owner', 'output_type', 'success_criteria', 'input_bindings', 'input_literals', 'depends_on'],
        'properties': {'owner': {'enum': list(available)}, 'output_type': {'enum': contracts},
            'success_criteria': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
            'input_bindings': binding_array,
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
        roots = [b for b in bindings if b.get('source') == 'context' and b.get('placement') == 'root']
        if roots and (len(bindings) != 1 or inputs or owner == 'improvement_proposer'):
            raise ValueError('Root planner input binding collision with other input bindings or control fields')
        prior_outputs = {}
        for binding in bindings:
            if binding['source'] == 'context':
                value = _pointer(context, binding['pointer'])
                if binding['placement'] == 'root':
                    _bind(inputs, '', value)
                elif binding['placement'] == 'field':
                    key = _field_name(context, binding['pointer'])
                    _bind(inputs, '/' + key.replace('~', '~0').replace('/', '~1'), value)
                else:
                    raise ValueError('Unknown planner context placement')
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
            '\nUse structured input_bindings. Select context values by JSON pointer and placement; never invent destination strings. '
            'placement root copies ONE complete OBJECT as the entire input. It must be the only binding and input_literals must be empty. '
            'For a complete object from a context array, select /items/0 with placement root to preserve its exact input shape. '
            'placement field copies a named context property under its ORIGINAL field name. Select /transactions or /untrusted_text with placement field. '
            'For multiple objects such as /job and /candidate, use placement field for each so both names and values are preserved. '
            'Each step binding names only a zero-based earlier step_index. Runtime groups output references under input.prior_outputs keyed by actual step IDs. '
            'Never emit <ID>.output or free-text dependencies. depends_on lists earlier control dependencies. '
            'Runtime owns IDs, references, waves and flags. input_literals holds only constants absent from context. '
            'An improvement_proposer must depend on exactly one environment_investigator and use field bindings or output references; runtime supplies its investigation_step control ID. '
            'Reject unavailable capabilities using the rejection branch.')
    scheduling = ('Declare depends_on for control dependencies; runtime assigns waves. ' if structured else
                  'Group independent steps into the same `wave`. ')
    user = (
        f"GOAL\n{goal}\n\n"
        f"CONTEXT\n{json.dumps(context, ensure_ascii=False, allow_nan=False)}\n\n"
        f"AVAILABLE AGENTS (id -> registry metadata)\n{available}\n\n"
        f"AVAILABLE CREWS\n{json.dumps(crews)}\n\n"
        "Produce a plan as JSON conforming to the schema. "
        "Set `owner` on every step to one of the available agent ids. "
        "Execution flags are assigned from registry metadata. "
        + scheduling + "Output JSON ONLY."
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
