"""One manifest-pinned live mission. Runs only as the gateway's owned child."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import closing
from copy import deepcopy
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from roboport_runtime.config import runtime_config_scope

from dashboard.launchpad.policy import component_inventory, project_inventory
from dashboard.launchpad.store import digest


def execute_manifest(manifest: dict, mission_dir: Path) -> dict:
    import benchmark
    from roboport_runtime import call_executor, call_grader, call_planner
    from roboport_runtime.feed_log import FeedLog
    from roboport_runtime.run_log import RunLog
    from windows_deploy import atomic_json

    mission_dir.mkdir(parents=True, exist_ok=True)
    (mission_dir / "artifacts").mkdir(exist_ok=True)
    raw = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    if (
        digest(raw) != manifest["manifest_hash"]
        or component_inventory(REPO) != manifest["component_hashes"]
        or project_inventory(Path(manifest["project_root"]))
        != manifest["project_hashes"]
    ):
        raise ValueError("reviewed manifest or source changed before worker execution")
    config = deepcopy(manifest["runtime_config"])
    config["budgets"]["per_run"] = manifest["limits"]
    config["defaults"]["context_tokens"] = manifest["context_tokens"]
    config["defaults"]["max_tokens"] = manifest["completion_tokens"]
    for override in config.get("agent_overrides", {}).values():
        if "max_tokens" in override:
            override["max_tokens"] = min(
                override["max_tokens"], manifest["completion_tokens"]
            )
    config["launchpad_scope"] = {"tools": manifest["tools"]}
    config["rea"] = {
        **config.get("rea", {}),
        "approved_roots": [manifest["project_root"]],
        "workspace": str(mission_dir / "rea"),
    }
    eval_file = (
        "deployment_jd.json"
        if manifest["profile"] == "jd_crew"
        else "environment_investigation.json"
    )
    ev = json.loads((REPO / "evals" / eval_file).read_text())["evals"][0]
    ev.update(
        target=manifest["profile"],
        prompt=ev["prompt"] + "\nOperator objective: " + manifest["objective"],
    )
    ev["expectations"] = ev["expectations"] + manifest["success_criteria"]
    if manifest["profile"] == "environment_investigation":
        from roboport_runtime.investigation import objective_criteria

        ev["context"]["project_path"] = manifest["project_root"]
        ev["context"]["operator_objective"] = manifest["objective"]
        ev["expectations"] += objective_criteria(manifest["objective"])
    from station_packs import load_registry

    registry = load_registry(REPO)
    run_id = mission_dir.name
    old_calls = benchmark.call_planner, benchmark.call_executor, benchmark.call_grader
    benchmark.call_planner, benchmark.call_executor, benchmark.call_grader = (
        call_planner,
        call_executor,
        call_grader,
    )
    previous_provider = os.environ.get("ROBOPORT_PROVIDER")
    os.environ["ROBOPORT_PROVIDER"] = "ollama"
    try:
        with (
            runtime_config_scope(config),
            closing(RunLog(mission_dir / "telemetry", run_id)) as log,
            closing(FeedLog(mission_dir / "feed.jsonl", run_id=run_id)) as feed,
        ):
            outcome, grading = benchmark.execute_run(
                ev,
                mission_dir / "artifacts",
                registry,
                config=config,
                live=True,
                grade=True,
                feed=feed,
                run_log=log,
            )
        result = {
            "status": "completed" if outcome["status"] == "ok" else "failed",
            "manifest_hash": manifest["manifest_hash"],
            "runtime_outcome": outcome,
            "grading": grading,
            "accounting_complete": True,
        }
        if result["status"] == "completed":
            from dashboard.launchpad.artifacts import build_review_context
            from dashboard.launchpad.missions import terminal_artifact_inventory

            atomic_json(
                mission_dir / "artifacts/review_context.json",
                build_review_context(manifest, mission_dir / "artifacts"),
            )
            result["artifact_hashes"] = terminal_artifact_inventory(mission_dir)
        atomic_json(mission_dir / "outcome.json", result)
        return result
    finally:
        benchmark.call_planner, benchmark.call_executor, benchmark.call_grader = (
            old_calls
        )
        if previous_provider is None:
            os.environ.pop("ROBOPORT_PROVIDER", None)
        else:
            os.environ["ROBOPORT_PROVIDER"] = previous_provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mission-dir", type=Path, required=True)
    args = parser.parse_args()
    execute_manifest(json.loads(args.manifest.read_text()), args.mission_dir)


if __name__ == "__main__":
    main()
