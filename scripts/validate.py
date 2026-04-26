#!/usr/bin/env python3
"""
ROBOPORT validator.

Validates:
  - evals/evals.json       against resources/schemas/eval.schema.json
  - agents/registry.json   structure (every path exists, every crew edge resolves)
  - any --output FILE      against resources/schemas/output.schema.json#<def>

Usage:
  python scripts/validate.py --evals evals/evals.json
  python scripts/validate.py --registry agents/registry.json
  python scripts/validate.py --output runs/<run_id>/final_output.json --as FinalReport
  python scripts/validate.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    sys.stderr.write("Missing dependency: pip install jsonschema\n")
    sys.exit(2)


REPO = Path(__file__).resolve().parent.parent
SCHEMAS = REPO / "resources" / "schemas"


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def validate_evals(path: Path) -> list[str]:
    schema = load_json(SCHEMAS / "eval.schema.json")
    instance = load_json(path)
    errors: list[str] = []
    try:
        jsonschema.validate(instance, schema)
    except jsonschema.ValidationError as e:
        errors.append(f"[evals] {e.message} (at {'/'.join(str(p) for p in e.path)})")
        return errors

    seen_ids: set[int] = set()
    for ev in instance["evals"]:
        if ev["id"] in seen_ids:
            errors.append(f"[evals] duplicate eval id: {ev['id']}")
        seen_ids.add(ev["id"])
        if "blockers" in ev:
            missing = set(ev["blockers"]) - set(ev["expectations"])
            if missing:
                errors.append(f"[evals] eval {ev['id']} blockers reference unknown expectations: {missing}")
    return errors


def validate_registry(path: Path) -> list[str]:
    reg = load_json(path)
    errors: list[str] = []

    for agent_id, meta in reg.get("agents", {}).items():
        agent_path = REPO / meta["path"]
        if not agent_path.exists():
            errors.append(f"[registry] agent {agent_id}: file not found: {meta['path']}")

    for crew_id, crew in reg.get("crews", {}).items():
        if crew["entry"] not in reg["agents"]:
            errors.append(f"[registry] crew {crew_id}: entry agent not in registry: {crew['entry']}")
        for edge in crew.get("edges", []):
            for end in ("from", "to"):
                if edge[end] not in reg["agents"]:
                    errors.append(f"[registry] crew {crew_id}: edge references unknown agent: {edge[end]}")
    return errors


def validate_output(path: Path, definition: str) -> list[str]:
    schema_full = load_json(SCHEMAS / "output.schema.json")
    if definition not in schema_full.get("definitions", {}):
        return [f"[output] unknown definition '{definition}' (try one of: {list(schema_full['definitions'])})"]
    sub = {
        "$schema": "https://json-schema.org/draft-07/schema#",
        "definitions": schema_full["definitions"],
        "$ref": f"#/definitions/{definition}",
    }
    instance = load_json(path)
    errors: list[str] = []
    try:
        jsonschema.validate(instance, sub)
    except jsonschema.ValidationError as e:
        errors.append(f"[output:{definition}] {e.message} (at {'/'.join(str(p) for p in e.path)})")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="ROBOPORT validator")
    ap.add_argument("--evals", type=Path, help="Path to evals.json")
    ap.add_argument("--registry", type=Path, help="Path to agents/registry.json")
    ap.add_argument("--output", type=Path, help="Path to a run output to validate")
    ap.add_argument("--as", dest="defn", help="Definition name from output.schema.json (e.g., FinalReport)")
    ap.add_argument("--all", action="store_true", help="Validate the standard set: evals, registry, example output")
    args = ap.parse_args()

    targets: list[tuple[str, list[str]]] = []

    if args.all:
        ev = REPO / "evals" / "evals.json"
        if ev.exists():
            targets.append(("evals.json", validate_evals(ev)))
        rg = REPO / "agents" / "registry.json"
        if rg.exists():
            targets.append(("registry.json", validate_registry(rg)))
    else:
        if args.evals:
            targets.append((str(args.evals), validate_evals(args.evals)))
        if args.registry:
            targets.append((str(args.registry), validate_registry(args.registry)))
        if args.output:
            if not args.defn:
                ap.error("--output requires --as <DefinitionName>")
            targets.append((str(args.output), validate_output(args.output, args.defn)))

    if not targets:
        ap.print_help()
        return 1

    failed = 0
    for name, errs in targets:
        if errs:
            failed += 1
            print(f"FAIL  {name}")
            for e in errs:
                print(f"   - {e}")
        else:
            print(f"OK    {name}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
