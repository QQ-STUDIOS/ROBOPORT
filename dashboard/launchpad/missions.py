"""Durable dispatch claim and owned, bounded mission execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path, PureWindowsPath

from .policy import validate_manifest
from .readiness import observe_readiness


def provenance_path_matches(raw, path):
    if PureWindowsPath(str(path)).drive:
        return PureWindowsPath(raw) == PureWindowsPath(path)
    return Path(raw).resolve() == Path(path).resolve()


def terminal_artifact_inventory(directory):
    required = [
        "artifacts/" + name
        for name in (
            "provenance.json",
            "plan.json",
            "budget.json",
            "grading.json",
            "final_output.json",
            "run.log",
        )
    ]
    required.append("telemetry/run.log")
    if (directory / "artifacts/review_context.json").exists():
        required.append("artifacts/review_context.json")
    try:
        plan = json.loads((directory / "artifacts/plan.json").read_text())
        if not plan.get("steps"):
            raise ValueError("terminal plan contains no steps")
        for step in plan["steps"]:
            if not isinstance(step.get("id"), str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]{1,80}", step["id"]
            ):
                raise ValueError("terminal step identifier invalid")
            required.append("artifacts/steps/" + step["id"] + ".json")
        inventory = {}
        for name in required:
            path = directory / name
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(directory.resolve())
            ):
                raise ValueError(
                    "terminal artifact missing or outside mission: " + name
                )
            inventory[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        records = [
            json.loads(line)
            for line in (directory / "telemetry/run.log").read_text().splitlines()
            if line
        ]
        if not any(
            r.get("event") == "run.complete"
            and r.get("run_summary", {}).get("status") == "ok"
            for r in records
        ):
            raise ValueError("terminal telemetry success missing")
        return inventory
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError("terminal artifact inventory incomplete: " + str(exc)) from exc


ACTIVE = {"queued", "running", "cancelling", "unknown"}


class MissionManager:
    def __init__(self, store, repo, data_root, policy, runtime_config=None):
        self.store, self.repo, self.data_root, self.policy = (
            store,
            Path(repo),
            Path(data_root),
            policy,
        )
        sys.path.insert(0, str(self.repo / "scripts"))
        from roboport_runtime.config import load_runtime_config

        self.runtime_config = runtime_config or load_runtime_config(self.repo)
        self.cancellations = {}
        self.inflight = set()  # operation ids executing in this process
        self.lock = threading.RLock()

    def _check_actor(self, actor, mission):
        if actor.get("role") not in {"operator", "admin"} or (
            actor.get("role") != "admin" and actor["id"] != mission["manifest"]["owner"]
        ):
            raise ValueError("mission owner/operator required")

    def _exclusive(self, command, action):
        """Run one operation at a time per operation id. A retry that arrives
        while the first attempt is still executing gets its pending state;
        only an attempt abandoned by an earlier gateway process is resumed."""
        op = command.get("operation_id")
        if not isinstance(op, str):
            return action()
        with self.lock:
            if op in self.inflight:
                return {"operation_id": op, "status": "pending", "mission_id": command.get("mission_id")}
            self.inflight.add(op)
        try:
            return action()
        finally:
            with self.lock:
                self.inflight.discard(op)

    def dispatch(self, actor, command):
        return self._exclusive(command, lambda: self._dispatch(actor, command))

    def _dispatch(self, actor, command):
        if command.get("intent") != "start":
            raise ValueError("Unsupported command")
        claim = self.store.claim_operation(actor["id"], command)
        if not claim["new"]:
            return claim["outcome"]
        accepted = {
            "operation_id": command["operation_id"],
            "status": "accepted",
            "mission_id": command.get("mission_id"),
        }
        if claim.get("resumed"):
            # Queuing records the operation on the mission in one transaction,
            # so a mission that left draft under this operation was dispatched.
            mission = self.store.get_mission(command["mission_id"])
            if mission.get("operation_id") == command["operation_id"] and mission["state"] != "draft":
                return self.store.finish_operation(command["operation_id"], accepted)
        # Register the cancellation handle before the mission becomes queued,
        # so reconcile() never sees a queued mission it doesn't own.
        cancel = threading.Event()
        with self.lock:
            if command.get("mission_id") in self.cancellations:
                self.store.finish_operation(command["operation_id"], {
                    "operation_id": command["operation_id"], "status": "rejected",
                    "error": "mission launch already in progress"})
                raise ValueError("mission launch already in progress")
            self.cancellations[command.get("mission_id")] = cancel
        try:
            with self.store.transaction() as db:
                mission = self.store._get(db, command["mission_id"])
                self._check_actor(actor, mission)
                if mission["revision"] != command.get("expected_revision"):
                    raise ValueError("mission revision conflict")
                if mission["state"] != "draft":
                    raise ValueError("mission is not a dispatchable draft")
                current = validate_manifest(
                    mission["manifest"], self.policy, self.runtime_config, self.repo
                )
                if (
                    current["manifest_hash"] != command.get("manifest_hash")
                    or current["manifest_hash"] != mission["manifest"]["manifest_hash"]
                ):
                    raise ValueError("reviewed manifest changed; review again")
                if current.get("require_review"):
                    reviewer_id = current.get("reviewer_id")
                    row = db.execute(
                        "SELECT body FROM users WHERE id=?", (reviewer_id,)
                    ).fetchone()
                    reviewer = json.loads(row["body"]) if row else {}
                    if (
                        current.get("review_phase") != "proposal"
                        or reviewer.get("role") not in {"reviewer", "admin"}
                        or (
                            current.get("independent_review")
                            and reviewer_id == current["owner"]
                        )
                    ):
                        raise ValueError(
                            "named independent proposal reviewer must be enrolled before dispatch"
                        )
                readiness = observe_readiness(
                    self.repo, self.policy, self.runtime_config
                )
                if not readiness["ready"]:
                    raise ValueError(
                        "readiness blocked: " + "; ".join(readiness["blockers"])
                    )
                if readiness["model_digests"] != current["model_digests"]:
                    raise ValueError("installed model changed; review manifest again")
                for row in db.execute("SELECT body FROM missions"):
                    other = json.loads(row["body"])
                    if other["state"] in ACTIVE:
                        raise ValueError(
                            "an active or unknown mission blocks another launch"
                        )
                directory = self.data_root / "missions" / mission["id"]
                mission = self.store._transition(
                    db,
                    mission["id"],
                    mission["revision"],
                    "queued",
                    {
                        "run_dir": str(directory),
                        "operation_id": command["operation_id"],
                    },
                )
            self._launch(mission, cancel)
            return self.store.finish_operation(command["operation_id"], accepted)
        except (ValueError, KeyError, OSError) as exc:
            with self.lock:
                if self.cancellations.get(command.get("mission_id")) is cancel:
                    self.cancellations.pop(command.get("mission_id"), None)
            self.store.finish_operation(
                command["operation_id"],
                {
                    "operation_id": command["operation_id"],
                    "status": "rejected",
                    "error": str(exc),
                },
            )
            raise

    def _launch(self, mission, cancel):
        from windows_deploy import atomic_json

        directory = Path(mission["run_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / "manifest.json", mission["manifest"])
        threading.Thread(target=self._run, args=(mission, cancel), daemon=True).start()

    def _run(self, mission, cancel):
        try:
            self._run_owned(mission, cancel)
        except Exception as exc:  # noqa: BLE001 — keep uncertain execution auditable
            self.store.lifecycle_transition(
                mission["id"],
                "unknown",
                {"outcome": {"error": str(exc), "accounting_complete": False}},
            )
            self.store.finish_operation(
                mission["operation_id"],
                {
                    "operation_id": mission["operation_id"],
                    "status": "unknown",
                    "mission_id": mission["id"],
                },
            )
        finally:
            with self.lock:
                self.cancellations.pop(mission["id"], None)

    def _run_owned(self, mission, cancel):
        from roboport_runtime.process_runner import run_bounded
        from windows_deploy import _record

        directory = Path(mission["run_dir"])

        def started(pid):
            with self.store.transaction() as db:
                current = self.store._get(db, mission["id"])
                self.store._transition(
                    db,
                    current["id"],
                    current["revision"],
                    "cancelling" if current["state"] == "cancelling" else "running",
                    {"process": _record(pid)},
                )

        env = dict(os.environ)
        env["ROBOPORT_PROVIDER"] = "ollama"
        env.pop("ANTHROPIC_API_KEY", None)
        argv = [
            sys.executable,
            str(self.repo / "scripts/launchpad_worker.py"),
            "--manifest",
            str(directory / "manifest.json"),
            "--mission-dir",
            str(directory),
        ]
        result = run_bounded(
            argv,
            cwd=self.repo,
            env=env,
            timeout_seconds=mission["manifest"]["limits"]["max_wall_seconds"] + 10,
            max_output_bytes=1024 * 1024,
            cancel_event=cancel,
            on_start=started,
        )
        try:
            if result.get("error", {}):
                state = (
                    "cancelled" if result["error"]["code"] == "cancelled" else "failed"
                )
                outcome = {
                    "status": state,
                    "error": result["error"],
                    "accounting_complete": False,
                    "budget_path": "artifacts/budget.json",
                }
            else:
                outcome = self._terminal_evidence(mission)
                state = outcome["status"]
                if state == "completed" and result.get("exit_code") != 0:
                    raise ValueError("worker did not exit successfully")
        except (ValueError, OSError, KeyError) as exc:
            state, outcome = (
                "unknown",
                {
                    "error": "Terminal evidence unavailable: " + str(exc),
                    "accounting_complete": False,
                },
            )
        try:
            self.store.lifecycle_transition(mission["id"], state, {"outcome": outcome})
            self.store.finish_operation(
                mission["operation_id"],
                {
                    "operation_id": mission["operation_id"],
                    "status": state,
                    "mission_id": mission["id"],
                },
            )
        finally:
            with self.lock:
                self.cancellations.pop(mission["id"], None)

    def cancel(self, actor, command):
        return self._exclusive(command, lambda: self._cancel(actor, command))

    def _cancel(self, actor, command):
        if command.get("intent") != "cancel":
            raise ValueError("Unsupported command")
        claim = self.store.claim_operation(actor["id"], command)
        if not claim["new"]:
            return claim["outcome"]
        if claim.get("resumed"):
            mission = self.store.get_mission(command["mission_id"])
            if mission["state"] in {"cancelling", "cancelled"}:
                return self.store.finish_operation(command["operation_id"], {
                    "operation_id": command["operation_id"], "status": "accepted",
                    "mission_id": mission["id"]})
        try:
            with self.lock:
                mission = self.store.get_mission(command["mission_id"])
                self._check_actor(actor, mission)
                if command.get("manifest_hash") != mission["manifest"][
                    "manifest_hash"
                ] or mission["revision"] != command.get("expected_revision"):
                    raise ValueError("mission revision or manifest conflict")
                event = self.cancellations.get(mission["id"])
                if event is None or mission["state"] not in {"queued", "running"}:
                    raise ValueError(
                        "cancellation unsupported for unowned or terminal mission"
                    )
                self.store.transition(
                    mission["id"], mission["revision"], "cancelling", {}
                )
                event.set()
            return self.store.finish_operation(
                command["operation_id"],
                {
                    "operation_id": command["operation_id"],
                    "status": "accepted",
                    "mission_id": mission["id"],
                },
            )
        except ValueError as exc:
            self.store.finish_operation(
                command["operation_id"],
                {
                    "operation_id": command["operation_id"],
                    "status": "rejected",
                    "error": str(exc),
                },
            )
            raise

    def _terminal_evidence(self, mission):
        directory = self.data_root / "missions" / mission["id"]
        outcome = json.loads((directory / "outcome.json").read_text())
        if outcome.get("manifest_hash") != mission["manifest"][
            "manifest_hash"
        ] or outcome.get("status") not in {"completed", "failed"}:
            raise ValueError("terminal outcome does not match mission")
        if outcome["status"] == "completed":
            if terminal_artifact_inventory(directory) != outcome.get("artifact_hashes"):
                raise ValueError(
                    "terminal artifact hash inventory missing or mismatched"
                )
            provenance = json.loads(
                (directory / "artifacts/provenance.json").read_text()
            )
            budget = json.loads((directory / "artifacts/budget.json").read_text())
            grading = json.loads((directory / "artifacts/grading.json").read_text())
            if (
                provenance.get("mode") != "live"
                or provenance.get("status") != "ok"
                or not provenance_path_matches(
                    provenance.get("run_id", ""), directory / "artifacts"
                )
                or grading.get("blocker_failed")
                or grading.get("pass_rate") != 1.0
                or "llm_calls" not in budget
            ):
                raise ValueError(
                    "terminal success lacks matching live grading/accounting evidence"
                )
        return outcome

    def reconcile(self):
        # A lost native Job Object kills owned Windows children. POSIX workers
        # may remain; retain identity and block another run until resolution.
        for mission in self.store.list_missions():
            with self.lock:
                self._reconcile_one(mission)

    def _reconcile_one(self, mission):
        # Called under self.lock, which dispatch holds while registering a
        # launch; the revision check below catches a worker's own update.
        if mission["state"] in ACTIVE and mission["id"] not in self.cancellations:
            from windows_deploy import _owned_process

            try:
                if mission.get("process") and _owned_process(mission["process"]):
                    raise ValueError("owned worker remains active")
                outcome = self._terminal_evidence(mission)
                state, fields = outcome["status"], {"outcome": outcome}
            except (ValueError, OSError, KeyError):
                state, fields = (
                    "unknown",
                    {
                        "error": "Gateway restarted; reconcile process and terminal evidence before another launch"
                    },
                )
            if state != mission["state"]:
                try:
                    self.store.transition(mission["id"], mission["revision"], state, fields)
                except ValueError:
                    pass  # changed since it was read; the next pass looks again
