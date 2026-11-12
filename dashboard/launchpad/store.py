"""Transactional mission, operation, identity, and decision persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data_root = (
            self.path.parent.parent
            if self.path.parent.name == "launchpad"
            else self.path.parent
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, actor TEXT, fingerprint TEXT, outcome TEXT);
                CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, mission_id TEXT, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass  # Windows ACL inheritance is verified by deployment setup.

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_mission(self, manifest: dict) -> dict:
        mission = {
            "id": uuid.uuid4().hex,
            "revision": 1,
            "state": "draft",
            "created_at": time.time(),
            "manifest": manifest,
            "decisions": [],
        }
        with self.transaction() as db:
            db.execute(
                "INSERT INTO missions VALUES(?, ?)", (mission["id"], canonical(mission))
            )
        return mission

    def _get(self, db, mission_id):
        row = db.execute(
            "SELECT body FROM missions WHERE id=?", (mission_id,)
        ).fetchone()
        if not row:
            raise ValueError("mission not found")
        mission = json.loads(row["body"])
        mission["decisions"] = [
            json.loads(r["body"])
            for r in db.execute(
                "SELECT body FROM decisions WHERE mission_id=? ORDER BY rowid",
                (mission_id,),
            )
        ]
        return mission

    def get_mission(self, mission_id: str) -> dict:
        with self.transaction() as db:
            return self._get(db, mission_id)

    def list_missions(self) -> list[dict]:
        with self.transaction() as db:
            return [
                self._get(db, row["id"])
                for row in db.execute("SELECT id FROM missions ORDER BY rowid DESC")
            ]

    def _transition(self, db, mission_id, expected_revision, state, fields):
        mission = self._get(db, mission_id)
        if mission["revision"] != expected_revision:
            raise ValueError("mission revision conflict; refresh required")
        if state not in {
            "draft",
            "waiting-review",
            "queued",
            "running",
            "cancelling",
            "cancelled",
            "failed",
            "completed",
            "rejected",
            "unknown",
        }:
            raise ValueError("invalid mission state")
        if set(fields) - {"run_dir", "process", "outcome", "error", "operation_id"}:
            raise ValueError("protected mission fields")
        mission.update(
            fields, state=state, revision=expected_revision + 1, updated_at=time.time()
        )
        db.execute(
            "UPDATE missions SET body=? WHERE id=?", (canonical(mission), mission_id)
        )
        return mission

    def transition(self, mission_id, expected_revision, state, fields):
        with self.transaction() as db:
            return self._transition(db, mission_id, expected_revision, state, fields)

    def lifecycle_transition(self, mission_id, state, fields):
        """Worker updates share a transaction with reading the current revision."""
        with self.transaction() as db:
            current = self._get(db, mission_id)
            return self._transition(db, mission_id, current["revision"], state, fields)

    def claim_operation(self, actor_id, command):
        op_id = command.get("operation_id")
        if not isinstance(op_id, str) or not 1 <= len(op_id) <= 128:
            raise ValueError("operation_id required")
        fingerprint = digest(command)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
            if row:
                if row["actor"] != actor_id or row["fingerprint"] != fingerprint:
                    raise ValueError(
                        "operation ID reuse with different actor or payload"
                    )
                return {
                    "new": False,
                    "operation_id": op_id,
                    "outcome": json.loads(row["outcome"]),
                }
            outcome = {"operation_id": op_id, "status": "accepted"}
            db.execute(
                "INSERT INTO operations VALUES(?,?,?,?)",
                (op_id, actor_id, fingerprint, canonical(outcome)),
            )
            return {"new": True, "operation_id": op_id, "outcome": outcome}

    def finish_operation(self, operation_id, outcome):
        with self.transaction() as db:
            row = db.execute(
                "SELECT outcome FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            previous = json.loads(row["outcome"]) if row else None
            if (
                previous
                and previous.get("status") != "accepted"
                and outcome.get("status") == "accepted"
            ):
                return previous
            db.execute(
                "UPDATE operations SET outcome=? WHERE id=?",
                (canonical(outcome), operation_id),
            )
        return outcome

    def record_decision(self, actor, decision):
        if actor.get("role") not in {"reviewer", "admin"}:
            raise ValueError("reviewer role required")
        with self.transaction() as db:
            mission = self._get(db, decision["mission_id"])
            manifest = mission["manifest"]
            if decision.get("manifest_hash") != manifest["manifest_hash"]:
                raise ValueError("decision manifest mismatch")
            if manifest.get("independent_review") and actor["id"] == manifest["owner"]:
                raise ValueError("independent reviewer required")
            if (
                decision.get("verdict") not in {"approved", "rejected"}
                or not str(decision.get("rationale", "")).strip()
            ):
                raise ValueError("decision verdict and rationale required")
            from .artifacts import read_evidence

            try:
                evidence = read_evidence(
                    mission, "artifacts/final_output.json", self.data_root, actor
                )
            except (ValueError, OSError) as exc:
                raise ValueError("review evidence unavailable: " + str(exc)) from exc
            if decision.get("evidence_hash") != evidence["sha256"]:
                raise ValueError("review evidence hash mismatch")
            if manifest.get("reviewer_id") and actor["id"] != manifest["reviewer_id"]:
                raise ValueError("named checkpoint reviewer required")
            expires = decision.get("expires_at")
            if expires is not None and (
                not isinstance(expires, (int, float)) or expires <= time.time()
            ):
                raise ValueError("decision expiry must be future")
            value = {
                **decision,
                "id": uuid.uuid4().hex,
                "actor_id": actor["id"],
                "timestamp": time.time(),
            }
            db.execute(
                "INSERT INTO decisions VALUES(?,?,?)",
                (value["id"], mission["id"], canonical(value)),
            )
            self._transition(
                db, mission["id"], mission["revision"], mission["state"], {}
            )
            return value
