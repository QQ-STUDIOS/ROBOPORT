"""Executor: run a single plan step against the owner agent with tool use."""
from __future__ import annotations

import functools
import json
import re
from typing import Any

from .client import REPO, _parse_json, load_agent_spec, provider
from .providers import TransientProviderError
from .tools import dispatch, load_agent_tool_map, schemas_for

MAX_TOOL_ROUNDS = 6
MAX_PROVIDER_RETRIES = 2   # Layer 1: bounded retries on transient (5xx) failures


def _chat_retry(p, **kw) -> tuple[dict, int]:
    """Call the provider, retrying bounded times on TransientProviderError.
    Returns (response, retries_used); re-raises after the budget is exhausted."""
    retries = 0
    while True:
        try:
            return p.chat_with_tools(**kw), retries
        except TransientProviderError:
            if retries >= MAX_PROVIDER_RETRIES:
                raise
            retries += 1


def _repair_schema(p, system: str, history: list, bad_content: str,
                   issues: list[str], output_type: str, model_hint: str):
    """Layer 1: one repair pass — re-prompt with the validation errors and ask
    for corrected JSON. Returns the reparsed result dict, or None if it didn't."""
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
        return _parse_json(out["content"])
    except (TransientProviderError, json.JSONDecodeError, Exception):  # noqa: BLE001
        return None


@functools.lru_cache(maxsize=1)
def _output_schema_doc() -> dict:
    return json.loads((REPO / "resources" / "schemas" / "output.schema.json")
                      .read_text(encoding="utf-8"))


def _resolve_output_schema(output_type: str | None) -> dict | None:
    """Map step.output_type (e.g. 'TechnicalAnalysis', 'list[Job]') to an
    inlined JSON schema fragment from output.schema.json. Returns None for
    unknown / freeform types."""
    if not output_type:
        return None
    doc = _output_schema_doc()
    defs = doc.get("definitions", {})
    # list[X] -> array of X
    m = re.match(r"\s*list\[(\w+)\]\s*", output_type)
    if m and m.group(1) in defs:
        return {"type": "array", "items": {"$ref": f"#/definitions/{m.group(1)}"},
                "definitions": defs}
    if output_type in defs:
        return {"$ref": f"#/definitions/{output_type}", "definitions": defs}
    return None


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


def call_executor(step: dict, accumulated: dict, registry: dict) -> dict[str, Any]:
    owner = step["owner"]
    agent_meta = registry["agents"].get(owner)
    if agent_meta is None:
        return _failed(step, f"unknown agent: {owner}")

    if agent_meta.get("deterministic") and agent_meta.get("model_hint") == "none":
        return _ok(step, owner, {"deterministic_stub": True, "owner": owner,
                                  "input": step["input"]}, llm_calls=0)

    p = provider()
    system_spec = load_agent_spec(agent_meta["path"])
    allowed_tools = _agent_tools_for(owner)
    tool_schemas = schemas_for(allowed_tools)
    model_hint = agent_meta.get("model_hint", "any")
    output_type = step.get("output_type", "object")
    output_schema = _resolve_output_schema(output_type)

    user_prompt = (
        f"STEP\nid: {step['id']}\nwave: {step.get('wave', 0)}\n"
        f"input: {json.dumps(step.get('input', {}))}\n"
        f"output_type: {step.get('output_type', 'object')}\n\n"
        f"SUCCESS CRITERIA\n"
        + "\n".join(f"- {c}" for c in step.get("success_criteria", []))
        + "\n\n"
        f"PRIOR STEP OUTPUTS (by step id)\n{json.dumps(accumulated) if accumulated else '(none)'}\n\n"
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

    for round_idx in range(MAX_TOOL_ROUNDS):
        is_final_round = round_idx == MAX_TOOL_ROUNDS - 1
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
        except TransientProviderError as e:
            # Layer 1 exhausted — fail loudly with the call-level layer named.
            return _failed(step, f"{p.name} transient error after {MAX_PROVIDER_RETRIES} "
                           f"retries (round {round_idx}): {e}",
                           llm_calls=llm_calls, tool_calls=tool_calls_total,
                           layer="provider_5xx", retries=retries_total + MAX_PROVIDER_RETRIES)
        except Exception as e:  # noqa: BLE001
            return _failed(step, f"{p.name} request failed (round {round_idx}): {e!r}",
                           llm_calls=llm_calls, tool_calls=tool_calls_total,
                           layer="provider_error", retries=retries_total)

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
                if tc["name"] not in allowed_tools:
                    result: Any = {"error": f"tool {tc['name']!r} not allowed for agent {owner!r}"}
                else:
                    result = dispatch(tc["name"], tc["arguments"])
                tool_calls_total += 1
                messages.append({
                    "role": "tool",
                    "name": tc["name"],
                    "tool_use_id": tc["id"],  # used by Anthropic provider
                    "content": json.dumps(result, default=str),
                })
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
            issues = _validate_against(output_schema, result.get("output", {}))
            if issues:
                fixed = _repair_schema(p, system_spec, messages, content,
                                       issues, output_type, model_hint)
                if fixed is not None:
                    llm_calls += 1
                    result = fixed
                    repaired = True
                    criteria_results = list(result.get("criteria_results", []) or [])
                    issues = _validate_against(output_schema, result.get("output", {}))
                if issues:
                    criteria_results.append({
                        "criterion": f"output conforms to `{output_type}` schema",
                        "passed": False,
                        "evidence": "; ".join(issues),
                    })

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
            "status": result.get("status", "ok"),
            "output": result.get("output", {}),
            "criteria_results": criteria_results,
            "tool_calls": tool_calls_total,
            "llm_calls": llm_calls,
            "retries": retries_total,
            "repaired": repaired,
            "transcript_path": None,
            "error": result.get("error"),
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
        "transcript_path": None,
        "error": None,
    }


def _failed(step: dict, msg: str, *, llm_calls: int = 0, tool_calls: int = 0,
            layer: str | None = None, retries: int = 0) -> dict[str, Any]:
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
        "transcript_path": None,
        "error": msg,
    }
