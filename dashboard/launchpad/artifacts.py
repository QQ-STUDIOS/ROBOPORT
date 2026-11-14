"""Bounded, classified artifact reads and provenance-checked replay imports."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote

from .store import canonical



def is_reparse_point(path) -> bool:
    """True for NTFS junctions and other reparse points (any Python version).

    ``Path.is_junction`` exists only on Python 3.12+, and the bridge supports 3.11.
    """
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT

def authorize(mission, actor):
    manifest = mission["manifest"]
    if manifest.get("classification") not in actor.get("classifications", []) or (
        actor.get("role") == "operator" and manifest.get("owner") != actor.get("id")
    ):
        raise ValueError("artifact access denied")


def read_evidence(mission, relative_path, data_root, actor):
    authorize(mission, actor)
    name = Path(unquote(relative_path))
    if name.is_absolute() or ".." in name.parts or "\\" in relative_path:
        raise ValueError("artifact path must be relative to this mission")
    original_root = Path(data_root) / "missions" / mission["id"]
    if any(
        p.is_symlink() or is_reparse_point(p)
        for p in [original_root, *original_root.parents]
        if p.exists()
    ):
        raise ValueError("artifact mission root traverses a link")
    root = original_root.resolve()
    if not root.is_relative_to(Path(data_root).resolve()):
        raise ValueError("artifact mission root escape")
    path = root / name
    if not path.resolve().is_relative_to(root):
        raise ValueError("artifact root escape")
    if any(
        p.is_symlink() or is_reparse_point(p)
        for p in [path, *path.parents]
        if p.exists()
    ):
        raise ValueError("artifact link rejected")
    before = path.stat(follow_symlinks=False)
    if not path.is_file() or before.st_size > 1024 * 1024:
        raise ValueError("artifact display size exceeds 1MiB")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        body = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    if len(body) > 1024 * 1024 or (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("artifact changed or exceeds display size")
    hash_ = hashlib.sha256(body).hexdigest()
    expected = mission.get("outcome", {}).get("artifact_hashes", {}).get(
        name.as_posix()
    ) or mission.get("evidence_hashes", {}).get(name.as_posix())
    if expected and hash_ != expected:
        raise ValueError("artifact hash mismatch")
    text = body.decode("utf-8")
    try:
        content = json.loads(text)
    except ValueError:
        content = text
    return {
        "path": name.as_posix(),
        "sha256": hash_,
        "bytes": len(body),
        "classification": mission["manifest"]["classification"],
        "content": content,
        "source": "mission artifact",
    }


def build_review_context(manifest, directory):
    """Freeze bounded, verified supporting inputs alongside a successful result."""
    directory = Path(directory)

    def read(path, expected=None):
        path = Path(path)
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("review evidence outside mission artifacts")
        if any(
            p.is_symlink() or is_reparse_point(p)
            for p in [path, *path.parents]
            if p.exists()
        ):
            raise ValueError("review evidence link rejected")
        if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("review evidence size exceeds limit")
        before = path.stat(follow_symlinks=False)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            body = stream.read(10 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("review evidence changed during read")
        if len(body) > 10 * 1024 * 1024:
            raise ValueError("review evidence size exceeds limit")
        hash_ = hashlib.sha256(body).hexdigest()
        if expected is not None and hash_ != expected:
            raise ValueError("review evidence hash mismatch")
        return body, hash_

    final, final_hash = read(directory / "final_output.json")
    plan, plan_hash = read(directory / "plan.json")
    result = {
        "schema_version": 1,
        "manifest_hash": manifest["manifest_hash"],
        "final_output_sha256": final_hash,
        "plan_sha256": plan_hash,
        "profile": manifest["profile"],
    }
    if manifest["profile"] == "jd_crew":
        inputs = json.loads(plan)["steps"][0]["input"]
        result.update(
            candidate_inputs=inputs["candidate_profile"],
            job_inputs=inputs["jobs"],
            evaluation_context=inputs.get("evaluation_context", {}),
            scope="Synthetic deployment fixture. Supplied candidate facts are inputs, not independently verified qualifications. No live job search or application occurred. Fit score is model judgment, not a hiring probability.",
        )
        strategy = next((step for step in json.loads(plan)["steps"] if step.get("owner") == "application_strategist" and "strategy_catalog" in step.get("input", {})), None)
        if strategy is not None:
            from scripts.roboport_runtime.strategy_catalog import compile_selection
            step_body, step_hash = read(directory / "steps" / "strategy.json")
            saved = json.loads(step_body)
            output, selection_evidence = compile_selection(strategy["input"], saved["grounding_selection"]["selection"])
            matches = json.loads(final)["ranked_matches"]
            if saved["status"] != "ok" or saved["output"] != output or saved["grounding_selection"] != selection_evidence or len(matches) != 1 or matches[0]["match"] != output:
                raise ValueError("compiled strategy evidence does not match final report")
            if any(strategy["input"][key] != inputs[key] for key in ("candidate_profile", "jobs", "evaluation_context")):
                raise ValueError("strategy source facts differ from supporting inputs")
            result.update(strategy_selection=selection_evidence, strategy_step_sha256=step_hash)
    elif manifest["profile"] == "environment_investigation":
        ref = json.loads(final)["evidence"]
        evidence_path = Path(ref["artifact_path"])
        _, hash_ = read(evidence_path, ref["artifact_sha256"])
        inventory, _ = read(evidence_path.parent / "input-manifest.json")
        files = json.loads(inventory)["files"]
        if files != manifest["project_hashes"]:
            raise ValueError("source inventory hash mismatch with reviewed project")
        samples, omitted = [], []
        for name, expected in sorted(files.items()):
            if (
                Path(name).is_absolute()
                or ".." in Path(name).parts
                or "\\" in name
                or ":" in name
            ):
                raise ValueError("source inventory path outside snapshot")
            body, actual = read(evidence_path.parent / "project" / name, expected)
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                omitted.append(name)
                continue
            if len(samples) < 20 and len(body) <= 16384:
                samples.append({"path": name, "sha256": actual, "text": text})
            else:
                omitted.append(name)
        result.update(
            source_inventory=files,
            source_samples=samples,
            samples_omitted=omitted,
            rea_evidence_sha256=hash_,
            scope="Only the inventoried snapshot was inspected. Files absent from this scope may exist elsewhere. Static analysis does not establish runtime correctness, a demonstrated defect, or measured performance improvement.",
        )
    else:
        raise ValueError("unsupported review context profile")
    if len(canonical(result).encode()) > 1024 * 1024:
        raise ValueError("review context exceeds display limit")
    return result


def import_replay(records, provenance):
    if (
        not isinstance(records, list)
        or len(records) > 50000
        or len(canonical(records).encode()) > 10 * 1024 * 1024
    ):
        raise ValueError("replay size limit exceeded")
    if not isinstance(provenance, dict) or not provenance.get("telemetry_run_id"):
        raise ValueError("replay provenance with telemetry_run_id required")
    ids = {r.get("run_id") for r in records if isinstance(r, dict) and r.get("run_id")}
    if ids != {provenance["telemetry_run_id"]}:
        raise ValueError("mixed or mismatched run IDs")
    if any(
        not isinstance(r, dict) or not isinstance(r.get("event"), str) for r in records
    ):
        raise ValueError("invalid replay record")
    actual = hashlib.sha256(
        ("\n".join(canonical(r) for r in records) + "\n").encode()
    ).hexdigest()
    if provenance.get("log_sha256") and provenance["log_sha256"] != actual:
        raise ValueError("replay log hash mismatch")
    return {
        "mode": "replay",
        "read_only": True,
        "records": records,
        "provenance": provenance,
        "normalized_log_sha256": actual,
    }


def replay_snapshot(value):
    """Read-only state is built from imported records, never a live store."""
    from .events import EventStore

    records = value["records"]
    run_id = value["provenance"]["telemetry_run_id"]
    crew = next(
        (r.get("crew", "unknown") for r in records if r["event"] == "run.start"),
        "unknown",
    )
    terminal = next(
        (
            r.get("run_summary", {}).get("status")
            for r in reversed(records)
            if r["event"] == "run.complete"
        ),
        None,
    )
    state = (
        "completed"
        if terminal == "ok"
        else ("failed" if terminal == "failed" else "unknown")
    )
    mission = {
        "id": run_id,
        "revision": 1,
        "state": state,
        "decisions": [],
        "manifest": {
            "owner": "replay",
            "profile": crew,
            "objective": "Imported replay " + run_id,
            "classification": "internal",
            "manifest_hash": value["normalized_log_sha256"],
            "independent_review": False,
        },
    }

    class ImportedStore:
        def list_missions(self):
            return [mission]

    events = EventStore(ImportedStore())
    for record in records:
        events.ingest(run_id, record)
    return events.snapshot("Imported replay", "network")
