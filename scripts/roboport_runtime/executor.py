"""Executor: run a single plan step against the owner agent with tool use."""
from __future__ import annotations

import json
from typing import Any

from .client import _parse_json, load_agent_spec, provider
from .tools import dispatch, load_agent_tool_map, schemas_for

MAX_TOOL_ROUNDS = 6


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
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    llm_calls = 0
    tool_calls_total = 0

    for round_idx in range(MAX_TOOL_ROUNDS):
        is_final_round = round_idx == MAX_TOOL_ROUNDS - 1
        try:
            out = p.chat_with_tools(
                system=system_spec,
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
                force_json=is_final_round or not tool_schemas,
                model_hint=model_hint,
            )
        except Exception as e:  # noqa: BLE001
            return _failed(step, f"{p.name} request failed (round {round_idx}): {e!r}",
                           llm_calls=llm_calls, tool_calls=tool_calls_total)

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

        return {
            "step_id": step["id"],
            "status": result.get("status", "ok"),
            "output": result.get("output", {}),
            "criteria_results": result.get("criteria_results", []),
            "tool_calls": tool_calls_total,
            "llm_calls": llm_calls,
            "transcript_path": None,
            "error": result.get("error"),
        }

    return _failed(step, f"exhausted {MAX_TOOL_ROUNDS} tool rounds without a final answer",
                   llm_calls=llm_calls, tool_calls=tool_calls_total)


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
        "transcript_path": None,
        "error": None,
    }


def _failed(step: dict, msg: str, *, llm_calls: int = 0,
            tool_calls: int = 0) -> dict[str, Any]:
    return {
        "step_id": step["id"],
        "status": "failed",
        "output": {},
        "criteria_results": [],
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
        "transcript_path": None,
        "error": msg,
    }
