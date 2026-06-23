#!/usr/bin/env python3
"""
ROBOPORT — agent scaffolder.

Automates the documented "Adding an agent" flow (README §"Adding an agent") so a
new agent lands consistent with the repo's opinions: the spec is the prompt,
typed contracts at every boundary, one agent one job, and no agent without an
eval blocker. It then runs the validator.

What it does (all transparent; use --dry-run to preview):
  1. agents/<role>/<name>.md   — frontmatter + the seven required sections
  2. agents/registry.json      — the {path, role, deterministic, model_hint} entry
                                 (+ a crew edge with --crew/--after)
  3. resources/schemas/output.schema.json — a stub definition for a NEW typed
                                 output (skipped for object/list[Existing])
  4. config/agent_config.yaml  — tool whitelist + an agent_override (comments kept)
  5. evals/<name>.json         — a target-<name> eval set with one blocker
  6. python scripts/validate.py --all + --evals evals/<name>.json

Examples
--------
  # a tool-using domain agent
  python scripts/new_agent.py --name market_scanner --role domain \\
      --title "Market Scanner" --output-type MarketScan \\
      --model-hint tool-use-capable --tools fetch_url,parse_html

  # a deterministic crew step wired after the synthesizer
  python scripts/new_agent.py --name salary_explainer --role domain.crew_builder \\
      --title "Salary Explainer" --output-type SalaryNote --deterministic \\
      --crew jd_crew --after synthesizer --edge-type FinalReport

  python scripts/new_agent.py --name foo --role domain --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "agents" / "registry.json"
OUTPUT_SCHEMA = REPO / "resources" / "schemas" / "output.schema.json"
CONFIG = REPO / "config" / "agent_config.yaml"
EVALS_DIR = REPO / "evals"

VALID_HINTS = ("reasoning-strong", "tool-use-capable", "writing-strong", "none")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TYPE_RE = re.compile(r"^[A-Z]\w+$")


class Change:
    """A planned file write — applied only outside --dry-run."""
    def __init__(self, path: Path, content: str, kind: str) -> None:
        self.path, self.content, self.kind = path, content, kind

    def apply(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.content, encoding="utf-8")


def agent_md_path(role: str, name: str) -> Path:
    return REPO / "agents" / Path(*role.split(".")) / f"{name}.md"


# ── 1. agent spec (the prompt) ────────────────────────────────────────────────
def render_agent_md(args, output_ref: str) -> str:
    det = args.deterministic
    title = args.title or args.name.replace("_", " ").title()
    tools = [t.strip() for t in (args.tools or "").split(",") if t.strip()]
    tools_block = (
        "\n".join(f"- `{t}` — TODO: what this tool does and why this agent needs it"
                  for t in tools)
        if tools else "_None — this agent answers from reasoning over its typed input._"
    )
    exec_block = (
        "1. TODO: pure-Python transform of the input (deterministic — no model call).\n"
        "2. TODO: validate the result against the output contract before handoff."
        if det else
        "1. TODO: first step (call a tool / reason over the input).\n"
        "2. TODO: gather evidence.\n"
        "3. TODO: produce ONE typed JSON answer matching the output contract."
    )
    return f"""---
id: {args.name}
role: {args.role}
title: {title}
inputs: {args.inputs}
outputs: {args.output_type}
model_hint: {args.model_hint}
temperature: {args.temperature}
deterministic_share: {1.0 if det else 0.0}
---

# {title}

TODO: one or two sentences — what single job this agent does, and where it sits
in the pipeline. One agent, one job.

## Role

TODO: the agent's responsibility in prose. This body IS the prompt sent to the
model (the spec is the prompt) — write it as instructions to the agent. State
what it must NOT do, so responsibility stays crisp.

## Inputs

```json
{{
  "{args.inputs.split(',')[0].strip() or 'input'}": "TODO: shape of the input this agent receives"
}}
```

## Outputs

`{args.output_type}` per `{output_ref}`:

```json
{{ "TODO": "an example instance of {args.output_type}" }}
```

## Execution order

{exec_block}

## Success criteria

- TODO: a verifiable assertion about the output (becomes an eval expectation)
- TODO: a second assertion — at least one of these must be a hard blocker
- Empty/!ok results fail loudly — an empty result means the work broke, not "no results"

## Tools used

{tools_block}

## Hand-off

TODO: which agent(s) consume this output, and the typed contract on that edge.
"""


# ── text-insertion helpers (preserve the files' hand-aligned formatting) ──────
def _insert_after(text: str, line_re: str, payload: str) -> str:
    lines = text.splitlines(keepends=True)
    pat = re.compile(line_re)
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines.insert(i + 1, payload)
            return "".join(lines)
    raise SystemExit(f"anchor not found: {line_re!r}")


def _guard_json(text: str, label: str) -> str:
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"scaffold produced invalid JSON for {label}: {e}")
    return text


# ── 2. registry entry (insert in place — minimal diff) ────────────────────────
def patch_registry(args) -> str:
    text = REGISTRY.read_text(encoding="utf-8")
    reg = json.loads(text)
    if args.name in reg.get("agents", {}):
        raise SystemExit(f"agent '{args.name}' already in registry — pick another name")
    if args.crew:
        if args.crew not in reg.get("crews", {}):
            raise SystemExit(f"crew '{args.crew}' not in registry (existing: {list(reg.get('crews', {}))})")
        if args.after and args.after not in reg["agents"]:
            raise SystemExit(f"--after agent '{args.after}' not in registry")

    rel = agent_md_path(args.role, args.name).relative_to(REPO).as_posix()
    det = "true" if args.deterministic else "false"
    entry = (f'    "{args.name}": {{ "path": "{rel}", "role": "{args.role}", '
             f'"deterministic": {det}, "model_hint": "{args.model_hint}" }},\n')
    out = _insert_after(text, r'^\s*"agents":\s*\{\s*$', entry)

    if args.crew:
        src = args.after or reg["crews"][args.crew]["entry"]
        etype = args.edge_type or args.output_type
        opt = ', "optional": true' if args.optional else ""
        edge = f'        {{ "from": "{src}", "to": "{args.name}", "type": "{etype}"{opt} }},\n'
        # scope to the chosen crew, then its edges array
        lines = out.splitlines(keepends=True)
        ci = next((i for i, ln in enumerate(lines)
                   if re.match(rf'^\s*"{re.escape(args.crew)}":\s*\{{', ln)), None)
        ei = next((i for i in range(ci or 0, len(lines))
                   if re.match(r'^\s*"edges":\s*\[\s*$', lines[i])), None) if ci is not None else None
        if ei is None:
            raise SystemExit(f"could not find a multi-line edges array for crew '{args.crew}'")
        lines.insert(ei + 1, edge)
        out = "".join(lines)
    return _guard_json(out, "registry.json")


# ── 3. output schema stub (only for a new typed output; insert in place) ──────
def patch_output_schema(args) -> tuple[str, bool]:
    if not TYPE_RE.match(args.output_type):           # object / list[X] / primitive
        return "", False
    text = OUTPUT_SCHEMA.read_text(encoding="utf-8")
    if args.output_type in json.loads(text).get("definitions", {}):
        return "", False
    block = (
        f'    "{args.output_type}": {{\n'
        f'      "type": "object",\n'
        f'      "description": "TODO: define {args.output_type} (scaffolded by new_agent.py).",\n'
        f'      "properties": {{}},\n'
        f'      "additionalProperties": true\n'
        f'    }},\n'
    )
    out = _insert_after(text, r'^\s*"definitions":\s*\{\s*$', block)
    return _guard_json(out, "output.schema.json"), True


# ── 4. config: tool whitelist + override (preserve comments via text insert) ──
def patch_config(args) -> str | None:
    text = CONFIG.read_text(encoding="utf-8")
    tools = [t.strip() for t in (args.tools or "").split(",") if t.strip()]
    lines = text.splitlines(keepends=True)

    def insert_after_header(src: list[str], header: str, payload: list[str]) -> list[str]:
        for i, ln in enumerate(src):
            if re.match(rf"^{re.escape(header)}\s*$", ln):
                return src[: i + 1] + payload + src[i + 1:]
        raise SystemExit(f"config: '{header}' block not found")

    if re.search(rf"(?m)^\s+{re.escape(args.name)}:", text):
        print(f"  · config already has '{args.name}' — skipping config edits")
        return None
    tools_line = [f"  {args.name}: [{', '.join(tools)}]\n"]
    override = [f"  {args.name}:\n",
                f"    temperature: {args.temperature}\n",
                f"    max_tokens: 2048\n"]
    lines = insert_after_header(lines, "tools:", tools_line)
    lines = insert_after_header(lines, "agent_overrides:", override)
    return "".join(lines)


# ── 5. eval set with a blocker ────────────────────────────────────────────────
def render_eval_set(args) -> str:
    assertion = f"TODO: a verifiable assertion about {args.name}'s {args.output_type} output"
    obj = {
        "$schema": "https://roboport.dev/schemas/eval.schema.json",
        "target": args.name,
        "version": "0.1.0",
        "evals": [{
            "id": 1,
            "prompt": f"TODO: a concrete input that exercises {args.name}.",
            "expected_output": f"TODO: what a correct {args.output_type} looks like.",
            "expectations": [assertion,
                             "TODO: a second assertion (non-empty / well-formed output)"],
            "blockers": [assertion],
            "tags": [args.name],
        }],
    }
    return json.dumps(obj, indent=2) + "\n"


# ── orchestration ─────────────────────────────────────────────────────────────
def build_changes(args) -> list[Change]:
    md_path = agent_md_path(args.role, args.name)
    if md_path.exists():
        raise SystemExit(f"{md_path.relative_to(REPO)} already exists — pick another name")

    output_ref = "object (freeform)"
    if TYPE_RE.match(args.output_type) or args.output_type.startswith("list["):
        inner = args.output_type[5:-1] if args.output_type.startswith("list[") else args.output_type
        output_ref = f"resources/schemas/output.schema.json#/definitions/{inner}"

    changes = [Change(md_path, render_agent_md(args, output_ref), "create agent spec")]

    reg_text = patch_registry(args)
    changes.append(Change(REGISTRY, reg_text, "registry entry" + (f" + {args.crew} edge" if args.crew else "")))

    schema_text, added = patch_output_schema(args)
    if added:
        changes.append(Change(OUTPUT_SCHEMA, schema_text, f"output schema stub: {args.output_type}"))

    cfg_text = patch_config(args)
    if cfg_text is not None:
        changes.append(Change(CONFIG, cfg_text, "config: tools + override"))

    changes.append(Change(EVALS_DIR / f"{args.name}.json", render_eval_set(args), "eval set (with blocker)"))
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a new ROBOPORT agent.")
    ap.add_argument("--name", required=True, help="agent id (snake_case)")
    ap.add_argument("--role", required=True,
                    help="role / dir, e.g. core | evaluation | domain | domain.crew_builder")
    ap.add_argument("--title", default=None, help="human title (default: Title Case of name)")
    ap.add_argument("--output-type", default="object",
                    help="typed output, e.g. MarketScan | list[Job] | object")
    ap.add_argument("--inputs", default="input", help="frontmatter inputs descriptor")
    ap.add_argument("--model-hint", default=None, choices=VALID_HINTS,
                    help="default: 'none' if --deterministic else 'reasoning-strong'")
    ap.add_argument("--deterministic", action="store_true", help="pure-Python agent (no LLM)")
    ap.add_argument("--tools", default="", help="comma-separated tool whitelist")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--crew", default=None, help="add as a node of this crew")
    ap.add_argument("--after", default=None, help="crew edge source (default: crew entry)")
    ap.add_argument("--edge-type", default=None, help="crew edge contract type (default: output-type)")
    ap.add_argument("--optional", action="store_true", help="mark the crew edge optional")
    ap.add_argument("--dry-run", action="store_true", help="print planned changes; write nothing")
    args = ap.parse_args()

    if not NAME_RE.match(args.name):
        ap.error(f"--name must be snake_case (got {args.name!r})")
    if args.model_hint is None:
        args.model_hint = "none" if args.deterministic else "reasoning-strong"

    changes = build_changes(args)

    verb = "Would write" if args.dry_run else "Writing"
    print(f"{verb} {len(changes)} change(s) for agent '{args.name}' (role={args.role}):")
    for c in changes:
        print(f"  · {c.kind:34} {c.path.relative_to(REPO)}")

    if args.dry_run:
        print("\n--dry-run: nothing written. Drop --dry-run to apply.")
        return 0

    for c in changes:
        c.apply()

    print("\nRunning validator…")
    rc = subprocess.call([sys.executable, str(REPO / "scripts" / "validate.py"), "--all"])
    rc |= subprocess.call([sys.executable, str(REPO / "scripts" / "validate.py"),
                           "--evals", str(EVALS_DIR / f"{args.name}.json")])
    print("\nNext: fill the TODOs in the spec/eval, then "
          f"`python scripts/benchmark.py --target {args.name} --eval-set evals/{args.name}.json`.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
