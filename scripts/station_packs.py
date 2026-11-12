"""Station packs: one site's agents, crews, eval sets and deployment settings.

The core registry (agents/registry.json) holds the runnable framework and names
no particular site. Each directory under stations/ that has a station.json adds
its own catalog agents, crews and eval sets on top. A repository without
stations/ is the plain framework. Packs may also add output contracts
(schemas/output.schema.json definitions).
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE_REGISTRY = Path("agents") / "registry.json"
CORE_OUTPUT_SCHEMA = Path("resources") / "schemas" / "output.schema.json"
# Sections a station pack may add to; a pack can never replace a core entry.
MERGED_SECTIONS = ("agents", "catalog_agents", "crews")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def station_dirs(repo: Path = REPO) -> list[Path]:
    root = repo / "stations"
    if not root.is_dir():
        return []
    return sorted(p.parent for p in root.glob("*/station.json"))


def load_station(station: Path) -> dict:
    meta = _load(station / "station.json")
    if meta.get("schema_version") != 1 or meta.get("id") != station.name:
        raise ValueError(f"station {station.name}: station.json must have schema_version 1 and id {station.name!r}")
    return meta


def merge_registry(registry: dict, repo: Path = REPO) -> dict:
    """Add every station pack's sections to a copy of the core registry."""
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in registry.items()}
    for station in station_dirs(repo):
        load_station(station)
        extra = station / "registry.json"
        if not extra.exists():
            continue
        overlay = _load(extra)
        for section in MERGED_SECTIONS:
            target = merged.setdefault(section, {})
            for key, value in overlay.get(section, {}).items():
                if key in target:
                    raise ValueError(f"station {station.name}: {section}.{key} is already defined")
                target[key] = value
    return merged


def load_registry(repo: Path = REPO, path: Path | str | None = None) -> dict:
    """The core registry plus station packs. An explicit other path is taken as-is."""
    core = repo / CORE_REGISTRY
    path = Path(path) if path else core
    registry = _load(path)
    if path.resolve() != core.resolve():
        return registry
    return merge_registry(registry, repo)


def load_output_schema(repo: Path = REPO) -> dict:
    """The core output contracts plus each pack's schemas/output.schema.json definitions."""
    doc = _load(repo / CORE_OUTPUT_SCHEMA)
    definitions = dict(doc.get("definitions", {}))
    for station in station_dirs(repo):
        extra = station / "schemas" / "output.schema.json"
        if not extra.exists():
            continue
        for name, schema in _load(extra).get("definitions", {}).items():
            if name in definitions:
                raise ValueError(f"station {station.name}: output definition {name} is already defined")
            definitions[name] = schema
    return {**doc, "definitions": definitions}


def eval_dirs(repo: Path = REPO) -> list[Path]:
    """evals/ plus each station pack's evals/."""
    dirs = [repo / "evals"]
    dirs += [s / "evals" for s in station_dirs(repo) if (s / "evals").is_dir()]
    return dirs
