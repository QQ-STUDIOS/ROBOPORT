"""Executor: run a single plan step against the owner agent with tool use."""
from __future__ import annotations

import fnmatch
import functools
import json
import re
from typing import Any

from .client import REPO, _parse_json, load_agent_spec, provider
from .providers import TransientProviderError
from .budget import BudgetExceeded, current_budget, governed_chat, model_call_scope
from .tools import dispatch, load_agent_tool_map, schemas_for

MAX_TOOL_ROUNDS = 6
MAX_PROVIDER_RETRIES = 2   # Layer 1: bounded retries on transient (5xx) failures
DEFAULT_BUDGET = {"max_llm_calls": 8, "max_tool_calls": 20}

# Layer 3: categorically unsafe actions — irreversible, outward-facing, or
# money/code-execution. A requested action matching one of these is escalated
# to the operator and never executed, regardless of any agent whitelist. These
# are fnmatch patterns (case-insensitive); override via `policy.unsafe_actions`
# in config/agent_config.yaml. The default set deliberately matches none of the
# repo's read-only tools, so today's crews are unaffected.
DEFAULT_UNSAFE_ACTIONS = [
    "delete_*", "drop_*", "purge_*", "remove_*", "rm_*", "destroy_*",  # destructive
    "write_*", "put_*", "post_*", "patch_*", "update_*", "upsert_*",   # mutating writes
    "send_*", "email_*", "sms_*", "message_*", "notify_*",            # outward comms
    "pay_*", "payment_*", "charge_*", "transfer_*", "wire_*", "refund_*",  # money
    "exec_*", "execute_*", "eval_*", "shell*", "run_command*", "spawn_*",  # code exec
    "deploy_*", "merge_*", "push_*", "publish_*", "release_*",        # ship/deploy
]


@functools.lru_cache(maxsize=1)
def _agent_config() -> dict:
    from .config import load_runtime_config
    return load_runtime_config(REPO)


def _budget_for(owner: str, config: dict) -> dict:
    """Layer 2: the per-step call budget for an agent — its override, else the
    config's per_agent budget, else a safe default."""
    over = ((config.get("agent_overrides") or {}).get(owner) or {}).get("budget") or {}
    base = (config.get("budgets") or {}).get("per_agent") or {}
    return {
        "max_llm_calls": int(over.get("max_llm_calls",
                             base.get("max_llm_calls", DEFAULT_BUDGET["max_llm_calls"]))),
        "max_tool_calls": int(over.get("max_tool_calls",
                              base.get("max_tool_calls", DEFAULT_BUDGET["max_tool_calls"]))),
    }


def _unsafe_actions(config: dict) -> list[str]:
    """Layer 3: the unsafe-action denylist — the config's `policy.unsafe_actions`
    if set (an explicit empty list disables the policy), else a safe default."""
    actions = (config.get("policy") or {}).get("unsafe_actions")
    if actions is None:
        return list(DEFAULT_UNSAFE_ACTIONS)
    return [str(a).lower() for a in actions]


def _is_unsafe(name: str, patterns: list[str]) -> bool:
    """True if a tool/action name matches the unsafe denylist (case-insensitive)."""
    n = (name or "").lower()
    return any(fnmatch.fnmatch(n, p) for p in patterns)


def _chat_retry(p, **kw) -> tuple[dict, int]:
    """Call the provider, retrying bounded times on TransientProviderError.
    Returns (response, retries_used); re-raises after the budget is exhausted."""
    retries = 0
    while True:
        try:
            return governed_chat(p, **kw), retries
        except TransientProviderError:
            if retries >= MAX_PROVIDER_RETRIES:
                raise
            retries += 1


def _new_telemetry() -> dict:
    """Phase 4: per-step routing telemetry, accumulated across the step's calls."""
    return {"provider": None, "model": None, "prompt_tokens": 0,
            "completion_tokens": 0, "cost_usd": 0.0, "latency_ms": 0,
            "cost_unknown": False}


def _accumulate(tele: dict, usage: dict | None) -> None:
    """Fold one call's `usage` block into the step telemetry. Cost is summed only
    while every call's cost is known; an unknown cost makes the step total None."""
    if not usage:
        return
    tele["provider"] = usage.get("provider") or tele["provider"]
    tele["model"] = usage.get("model") or tele["model"]
    tele["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    tele["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    tele["latency_ms"] += int(usage.get("latency_ms") or 0)
    cost = usage.get("cost_usd")
    if cost is None:
        tele["cost_unknown"] = True
    else:
        tele["cost_usd"] += float(cost)


def _telemetry_fields(tele: dict) -> dict:
    """The telemetry subset added to a step result."""
    return {
        "provider": tele["provider"],
        "model": tele["model"],
        "prompt_tokens": tele["prompt_tokens"],
        "completion_tokens": tele["completion_tokens"],
        "cost_usd": None if tele["cost_unknown"] else round(tele["cost_usd"], 6),
        "latency_ms": tele["latency_ms"],
    }


def _repair_schema(p, system: str, history: list, bad_content: str,
                   issues: list[str], output_type: str, model_hint: str):
    """Layer 1: one repair pass — re-prompt with the validation errors and ask
    for corrected JSON. Returns (reparsed result dict | None, usage | None)."""
    repair_messages = history + [
        {"role": "assistant", "content": bad_content},
        {"role": "user", "content":
            f"Your previous JSON's `output` failed `{output_type}` schema validation:\n- "
            + "\n- ".join(issues)
            + "\nReturn the corrected final JSON ONLY, in the same top-level format."},
    ]
    try:
        out, _ = _chat_retry(p, system=system, messages=repair_messages,
                             tools=None, force_json=True, model_hint=model_hint)
        return _parse_json(out["content"]), out.get("usage")
    except (TransientProviderError, json.JSONDecodeError, Exception):  # noqa: BLE001
        return None, None


@functools.lru_cache(maxsize=1)
def _output_schema_doc() -> dict:
    # Core contracts plus any station pack's (scripts/station_packs.py).
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    from station_packs import load_output_schema

    return load_output_schema(REPO)


def _referenced_definitions(name: str, definitions: dict) -> dict:
    """Keep the complete transitive local-ref closure; never prune constraints."""
    pending = [name]
    needed = set()
    while pending:
        current = pending.pop()
        if current in needed:
            continue
        if current not in definitions:
            # Keep the old resolver behavior for malformed/unsupported refs.
            return definitions
        needed.add(current)
        nodes = [definitions[current]]
        while nodes:
            node = nodes.pop()
            if isinstance(node, dict):
                reference = node.get('$ref')
                if reference is not None:
                    if not isinstance(reference, str) or not reference.startswith('#/definitions/'):
                        return definitions
                    target = reference[len('#/definitions/'):].split('/', 1)[0]
                    pending.append(target.replace('~1', '/').replace('~0', '~'))
                nodes.extend(node.values())
            elif isinstance(node, list):
                nodes.extend(node)
    return {key: value for key, value in definitions.items() if key in needed}


def _resolve_output_schema(output_type: str | None) -> dict | None:
    """Resolve the requested output contract with all referenced definitions."""
    if not output_type:
        return None
    definitions = _output_schema_doc().get('definitions', {})
    match = re.match(r"\s*list\[(\w+)\]\s*", output_type)
    name = match.group(1) if match else output_type
    if name not in definitions:
        return None
    selected = _referenced_definitions(name, definitions)
    if match:
        return {'type': 'array', 'items': {'$ref': f'#/definitions/{name}'},
                'definitions': selected}
    return {'$ref': f'#/definitions/{name}', 'definitions': selected}


def _validate_against(schema: dict, value: Any) -> list[str]:
    """Return a list of validation error messages; [] if valid."""
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return []
    try:
        jsonschema.validate(value, schema)
        return []
    except jsonschema.ValidationError as e:
        # Surface up to 3 issues for context.
        return [f"{'/'.join(str(p) for p in e.absolute_path) or '.'}: {e.message}"]
    except Exception as e:  # noqa: BLE001
        return [f"validator error: {e!r}"]


def _agent_tools_for(owner: str) -> list[str]:
    try:
        return load_agent_tool_map().get(owner, [])
    except Exception:  # noqa: BLE001
        return []


def _call_executor(step: dict, accumulated: dict, registry: dict) -> dict[str, Any]:
    owner = step["owner"]
    agent_meta = registry["agents"].get(owner)
    if agent_meta is None:
        return _failed(step, f"unknown agent: {owner}")

    if agent_meta.get("deterministic") and agent_meta.get("model_hint") == "none":
        if owner != 'synthesizer':
            return _failed(step, f'deterministic implementation missing: {owner}')
        try:
            from synthesize import from_accumulated
            output = from_accumulated(accumulated)
            issues = _validate_against(_resolve_output_schema('FinalReport'), output)
            if issues:
                return _failed(step, '; '.join(issues), layer='schema_invalid')
            return _ok(step, owner, output, llm_calls=0)
        except (ValueError, KeyError, TypeError) as exc:
            return _failed(step, str(exc), layer='synthesis_failed')

    p = provider()
    system_spec = load_agent_spec(agent_meta["path"])
    allowed_tools = _agent_tools_for(owner)
    if "allowed_tools" in step:
        allowed_tools = [name for name in allowed_tools if name in step["allowed_tools"]]
    scope = _agent_config().get('launchpad_scope')
    if scope is not None:
        allowed_tools = [name for name in allowed_tools if name in scope.get('tools', [])]
    tool_schemas = schemas_for(allowed_tools)
    model_hint = agent_meta.get("model_hint", "any")
    output_type = step.get("output_type", "object")
    output_schema = _resolve_output_schema(output_type)
    catalog = None
    if output_type == 'CandidateMatch' and 'strategy_catalog' in step.get('input', {}):
        from .strategy_catalog import bound_catalog, selection_issues, selection_schema
        try:
            catalog = bound_catalog(step['input'])
            output_schema = selection_schema(catalog)
        except (ValueError, KeyError, TypeError) as exc:
            return _failed(step, str(exc), layer='grounding_invalid')
    if owner == 'improvement_proposer' and output_schema:
        upstream = accumulated.get(step.get('input', {}).get('investigation_step', 'investigate'), {})
        reference = upstream.get('evidence') if isinstance(upstream, dict) else None
        if isinstance(reference, dict) and all(reference.get(k) for k in ('artifact_path', 'artifact_sha256', 'evidence_id', 'rea_version')):
            # Keep the authority in the trusted handoff. The existing bounded
            # schema repair can correct a copied reference; the semantic guard
            # below still rejects anything not matching genuine upstream data.
            output_schema = {'definitions': output_schema['definitions'], 'allOf': [
                {'$ref': output_schema['$ref']},
                {'type': 'object', 'properties': {'evidence': {'const': reference}}}]}
    config = _agent_config()
    budget = _budget_for(owner, config)
    unsafe_actions = _unsafe_actions(config)
    inspected_source = ''
    if owner == 'improvement_proposer' and getattr(current_budget(), 'artifact_dir', None) is not None:
        from .investigation import source_handoff
        try:
            data = source_handoff(reference, current_budget().artifact_dir)
            inspected_source = 'INSPECTED SOURCE DATA\n' + json.dumps(data, ensure_ascii=False) + '\n\n'
        except ValueError as exc:
            return _failed(step, str(exc), layer='evidence_invalid')

    user_prompt = (
        f"STEP\nid: {step['id']}\nwave: {step.get('wave', 0)}\n"
        f"input: {json.dumps(step.get('input', {}))}\n"
        f"output_type: {step.get('output_type', 'object')}\n\n"
        f"SUCCESS CRITERIA\n"
        + "\n".join(f"- {c}" for c in step.get("success_criteria", []))
        + "\n\n"
        f"PRIOR STEP OUTPUTS (by step id)\n{json.dumps(accumulated) if accumulated else '(none)'}\n\n"
        + inspected_source
        + (
            "AVAILABLE TOOLS\n"
            + ", ".join(allowed_tools)
            + "\n\n"
            + "Call tools to gather evidence, then return ONE final JSON message.\n"
            if allowed_tools
            else "(this agent has no tools — answer from reasoning over prior outputs)\n\n"
        )
        + "FINAL ANSWER FORMAT\n"
          'Return JSON with these top-level keys: '
          '{"status":"ok"|"failed","output":{...your typed output matching `output_type`...},'
          '"criteria_results":[{"criterion":str,"passed":bool,"evidence":str},...],'
          '"error":str|null}'
        + (
            f"\n\nOUTPUT SCHEMA (the `output` field must conform to this "
            f"definition of `{output_type}`)\n"
            + json.dumps(output_schema, indent=2)
            if output_schema
            else ""
        )
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    llm_calls = 0
    tool_calls_total = 0
    retries_total = 0
    tele = _new_telemetry()
    rea_results = []

    for round_idx in range(MAX_TOOL_ROUNDS):
        is_final_round = round_idx == MAX_TOOL_ROUNDS - 1
        # Layer 2: abort loudly before exceeding the per-agent call budget.
        if llm_calls >= budget["max_llm_calls"]:
            return _failed(step, f"per-agent budget exceeded: {llm_calls} llm calls "
                           f">= {budget['max_llm_calls']}", llm_calls=llm_calls,
                           tool_calls=tool_calls_total, layer="budget_exceeded",
                           retries=retries_total)
        try:
            out, r = _chat_retry(
                p,
                system=system_spec,
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
                force_json=is_final_round or not tool_schemas,
                model_hint=model_hint,
            )
            retries_total += r
            _accumulate(tele, out.get("usage"))
        except TransientProviderError as e:
            # Layer 1 exhausted — fail loudly with the call-level layer named.
            return _failed(step, f"{p.name} transient error after {MAX_PROVIDER_RETRIES} "
                           f"retries (round {round_idx}): {e}",
                           llm_calls=llm_calls, tool_calls=tool_calls_total,
                           layer="provider_5xx", retries=retries_total + MAX_PROVIDER_RETRIES)
        except Exception as e:  # noqa: BLE001
            return _failed(step, f"{p.name} request failed (round {round_idx}): {e!r}",
                           llm_calls=llm_calls, tool_calls=tool_calls_total,
                           layer="budget_exceeded" if isinstance(e, BudgetExceeded) else "provider_error", retries=retries_total)

        llm_calls += 1
        content = out["content"]
        tcs = out["tool_calls"]

        if tcs:
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {"id": tc["id"],
                     "function": {"name": tc["name"],
                                   "arguments": tc["arguments"]}}
                    for tc in tcs
                ],
            })
            for tc in tcs:
                if tool_calls_total >= budget['max_tool_calls']:
                    return _failed(step, 'per-agent tool calls budget exhausted before dispatch', llm_calls=llm_calls, tool_calls=tool_calls_total, layer='budget_exceeded')
                try:
                    if current_budget() is not None:
                        current_budget().consume_tool_call()
                except BudgetExceeded as exc:
                    return _failed(step, str(exc), llm_calls=llm_calls, tool_calls=tool_calls_total, layer='budget_exceeded')
                # Layer 3: an unsafe/irreversible action is escalated to the
                # operator and never executed — deny overrides any whitelist,
                # and we return BEFORE dispatch so there is no side effect.
                if _is_unsafe(tc["name"], unsafe_actions):
                    return _failed(
                        step,
                        f"unsafe action {tc['name']!r} blocked and escalated: this "
                        f"action is irreversible or outward-facing and requires human "
                        f"authorization before it can run. No tool was executed.",
                        llm_calls=llm_calls, tool_calls=tool_calls_total,
                        layer="unsafe_action", retries=retries_total,
                        escalated_action=tc["name"])
                if tc["name"] not in allowed_tools:
                    result: Any = {"error": f"tool {tc['name']!r} not allowed for agent {owner!r}"}
                else:
                    result = dispatch(tc["name"], tc["arguments"])
                if tc["name"] == "rea_analyze_project" and isinstance(result, dict):
                    rea_results.append(result)
                tool_calls_total += 1
                active_budget = current_budget()
                artifact_dir = getattr(active_budget, 'artifact_dir', None)
                if artifact_dir:
                    with (artifact_dir / 'tool-results.jsonl').open('a', encoding='utf-8') as audit:
                        audit.write(json.dumps({'step_id': step['id'], 'agent': owner, 'tool': tc['name'], 'result': result}, default=str) + '\n')
                messages.append({
                    "role": "tool",
                    "name": tc["name"],
                    "tool_use_id": tc["id"],  # used by Anthropic provider
                    "content": json.dumps(result, default=str),
                })
            if tool_calls_total > budget["max_tool_calls"]:
                return _failed(step, f"per-agent budget exceeded: {tool_calls_total} tool "
                               f"calls > {budget['max_tool_calls']}", llm_calls=llm_calls,
                               tool_calls=tool_calls_total, layer="budget_exceeded",
                               retries=retries_total)
            continue

        if not content.strip():
            if not is_final_round:
                messages.append({
                    "role": "user",
                    "content": (
                        "Your last response was empty. Either call one of the "
                        "available tools to make progress, or return your "
                        "final JSON answer in the format described above. "
                        "Do not return an empty message."
                    ),
                })
                continue
            return _failed(step, "model returned empty content with no tool_calls",
                           llm_calls=llm_calls, tool_calls=tool_calls_total)

        try:
            result = _parse_json(content)
        except json.JSONDecodeError:
            return _failed(step, f"could not parse final JSON: {content[:300]!r}",
                           llm_calls=llm_calls, tool_calls=tool_calls_total)

        # Layer 1 (repair): validate against the typed output schema, and on
        # failure attempt ONE repair pass before surfacing it as a failed
        # criterion. A small model that mostly gets the shape right gets a
        # second chance; a persistent mismatch is recorded, not silently passed.
        criteria_results = list(result.get("criteria_results", []) or [])
        repaired = False
        if output_schema is not None:
            issues = selection_issues(catalog, result.get("output", {})) if catalog is not None else _validate_against(output_schema, result.get("output", {}))
            candidate = step.get('input', {}).get('candidate_profile')
            if output_type == 'CandidateMatch' and candidate is not None and catalog is None:
                from .candidate_grounding import validate_recommendations
                issues += validate_recommendations(result.get('output') or {}, candidate, accumulated, step.get('input', {}))
            if issues:
                # one repair pass — but only if the budget still allows a call
                fixed, repair_usage = (
                    _repair_schema(p, system_spec, messages, content,
                                   issues, output_type, model_hint)
                    if llm_calls < budget["max_llm_calls"] else (None, None))
                if fixed is not None:
                    llm_calls += 1
                    _accumulate(tele, repair_usage)
                    result = fixed
                    repaired = True
                    criteria_results = list(result.get("criteria_results", []) or [])
                    issues = selection_issues(catalog, result.get("output", {})) if catalog is not None else _validate_against(output_schema, result.get("output", {}))
                    if output_type == 'CandidateMatch' and candidate is not None and catalog is None:
                        issues += validate_recommendations(result.get('output') or {}, candidate, accumulated, step.get('input', {}))
                if issues:
                    criteria_results.append({
                        "criterion": f"output conforms to `{output_type}` schema",
                        "passed": False,
                        "evidence": "; ".join(issues),
                    })

        grounding = {}
        if catalog is not None:
            if issues:
                failed = _failed(step, '; '.join(issues), llm_calls=llm_calls, tool_calls=tool_calls_total, layer='grounding_invalid')
                return {**failed, **_telemetry_fields(tele), 'repaired': repaired, 'grounding_selection': {'selection': result.get('output'), 'source_sha256': catalog['source_sha256'], 'catalog_sha256': catalog['catalog_sha256']}}
            from .strategy_catalog import compile_selection
            result['output'], evidence = compile_selection(step['input'], result['output'])
            grounding = {'grounding_selection': evidence}

        if owner in ('environment_investigator', 'improvement_proposer'):
            from .investigation import validate_reference
            sources = rea_results if owner == 'environment_investigator' else [dict(status='ok', **v.get('evidence', {})) for v in accumulated.values() if isinstance(v, dict)]
            issues = validate_reference((result.get('output') or {}).get('evidence'), sources)
            if issues:
                return _failed(step, '; '.join(issues), llm_calls=llm_calls, tool_calls=tool_calls_total, layer='evidence_invalid')
        if output_type == 'CandidateMatch' and catalog is None and step.get('input', {}).get('candidate_profile') is not None:
            from .candidate_grounding import validate_recommendations
            issues = validate_recommendations(result.get('output') or {}, step['input']['candidate_profile'], accumulated, step['input'])
            if issues:
                return _failed(step, '; '.join(issues), llm_calls=llm_calls, tool_calls=tool_calls_total, layer='grounding_invalid')
        # Quiet-200 guard: an empty array out of a search-shaped step means the
        # search broke, not "zero results". Fail loudly rather than pass it on.
        out_val = result.get("output", {})
        if (output_type or "").startswith("list[") and isinstance(out_val, list) and not out_val:
            criteria_results.append({
                "criterion": "non-empty results (quiet-200 guard)",
                "passed": False,
                "evidence": "output is an empty list; empty arrays mean the search "
                            "broke, not zero results",
            })

        return {
            "step_id": step["id"],
            "status": "failed" if any(not c.get("passed") for c in criteria_results) else result.get("status", "failed"),
            "output": result.get("output", {}),
            "criteria_results": criteria_results,
            "tool_calls": tool_calls_total,
            "llm_calls": llm_calls,
            "retries": retries_total,
            "repaired": repaired,
            **_telemetry_fields(tele),
            **grounding,
            "transcript_path": None,
            "error": result.get("error") or ("criterion or output contract failed" if any(not c.get("passed") for c in criteria_results) else None),
        }

    return _failed(step, f"exhausted {MAX_TOOL_ROUNDS} tool rounds without a final answer",
                   llm_calls=llm_calls, tool_calls=tool_calls_total,
                   layer="budget_exceeded", retries=retries_total)


def _ok(step: dict, owner: str, output: dict, llm_calls: int) -> dict[str, Any]:
    return {
        "step_id": step["id"],
        "status": "ok",
        "output": output,
        "criteria_results": [
            {"criterion": c, "passed": True,
             "evidence": "deterministic agent — not LLM-evaluated"}
            for c in step.get("success_criteria", [])
        ],
        "tool_calls": 0,
        "llm_calls": llm_calls,
        "retries": 0,
        "repaired": False,
        **_telemetry_fields(_new_telemetry()),
        "transcript_path": None,
        "error": None,
    }


def _failed(step: dict, msg: str, *, llm_calls: int = 0, tool_calls: int = 0,
            layer: str | None = None, retries: int = 0,
            escalated_action: str | None = None) -> dict[str, Any]:
    return {
        "step_id": step["id"],
        "status": "failed",
        "output": {},
        "criteria_results": [],
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
        "retries": retries,
        "repaired": False,
        "layer": layer,
        "escalated_action": escalated_action,
        "transcript_path": None,
        "error": msg,
    }


def call_executor(step: dict, accumulated: dict, registry: dict) -> dict:
    limits = _budget_for(step['owner'], _agent_config())
    with model_call_scope(limits['max_llm_calls']):
        return _call_executor(step, accumulated, registry)
