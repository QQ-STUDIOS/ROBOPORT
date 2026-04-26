"""Executor: run a single plan step against the owner agent with tool use."""
from __future__ import annotations

import json
from typing import Any

import requests

from .client import (
    OLLAMA_HOST,
    _parse_json,
    load_agent_spec,
    model_for,
)
from .tools import dispatch, load_agent_tool_map, schemas_for

# Cap on tool-call rounds per step. Prevents a confused model from looping
# forever over the same tool. The final round forces a JSON answer.
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

    # Deterministic agents (e.g., synthesizer) skip the LLM.
    if agent_meta.get("deterministic") and agent_meta.get("model_hint") == "none":
        return _ok(step, owner, {"deterministic_stub": True, "owner": owner,
                                  "input": step["input"]}, llm_calls=0)

    system_spec = load_agent_spec(agent_meta["path"])
    allowed_tools = _agent_tools_for(owner)
    tool_schemas = schemas_for(allowed_tools)

    user = (
        f"STEP\nid: {step['id']}\nwave: {step.get('wave', 0)}\n"
        f"input: {json.dumps(step['input'])}\n"
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

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_spec},
        {"role": "user", "content": user},
    ]

    llm_calls = 0
    tool_calls_total = 0
    last_content = ""

    for round_idx in range(MAX_TOOL_ROUNDS):
        is_final_round = round_idx == MAX_TOOL_ROUNDS - 1
        payload: dict[str, Any] = {
            "model": model_for(agent_meta),
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "think": False,
            "options": {"temperature": 0.2, "num_ctx": 16384},
        }
        # Offer tools until the last round, where we force a final JSON.
        if tool_schemas and not is_final_round:
            payload["tools"] = tool_schemas
        else:
            payload["format"] = "json"

        try:
            r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=900)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return _failed(step, f"ollama request failed (round {round_idx}): {e!r}",
                           llm_calls=llm_calls, tool_calls=tool_calls_total)

        llm_calls += 1
        body = r.json()
        msg = body.get("message") or {}
        last_content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if tool_calls:
            # Echo assistant turn (with tool_calls) into history, then dispatch.
            messages.append({
                "role": "assistant",
                "content": last_content,
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                name = fn.get("name", "")
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if name not in allowed_tools:
                    result: Any = {"error": f"tool {name!r} not allowed for agent {owner!r}"}
                else:
                    result = dispatch(name, args or {})
                tool_calls_total += 1
                messages.append({
                    "role": "tool",
                    "name": name,
                    "content": json.dumps(result, default=str),
                })
            continue

        # No tool calls — try to parse the final structured answer.
        if not last_content.strip():
            # Model bailed without progress. Coach it toward a final JSON and
            # let the loop run another round (now in JSON-only mode since
            # last_content stays blank but we've already echoed nothing).
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
            result = _parse_json(last_content)
        except json.JSONDecodeError:
            return _failed(step, f"could not parse final JSON: {last_content[:300]!r}",
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
