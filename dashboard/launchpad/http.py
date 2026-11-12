"""Same-origin bounded JSON gateway; runtime authority never comes from the UI."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .policy import validate_manifest
from .readiness import observe_readiness


class HTTPError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Gateway:
    def __init__(self, store, auth, policy, repo, data_root, runtime_config=None):
        self.store, self.auth, self.policy = store, auth, policy
        self.repo, self.data_root = Path(repo), Path(data_root)
        sys.path.insert(0, str(self.repo / "scripts"))
        from roboport_runtime.config import load_runtime_config

        self.runtime_config = runtime_config or load_runtime_config(self.repo)
        from .missions import MissionManager

        self.manager = MissionManager(
            store, self.repo, self.data_root, policy, runtime_config=self.runtime_config
        )
        self.manager.reconcile()
        from .events import EventStore

        self.events = EventStore(store)
        self.events.start(self.data_root)
        self.login_attempts = []

    def reply(self, h, status, body, cookie=None):
        payload = json.dumps(body, allow_nan=False).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Content-Length", str(len(payload)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            h.send_header("Set-Cookie", cookie + "; Path=/; HttpOnly; SameSite=Strict")
        h.end_headers()
        h.wfile.write(payload)

    def check_origin(self, h):
        expected = "127.0.0.1:" + str(h.server.server_port)
        host = h.headers.get("Host", "")
        allowed = {expected, "localhost:" + str(h.server.server_port)}
        if host not in allowed or (
            h.headers.get("Origin") and h.headers["Origin"] != "http://" + host
        ):
            raise HTTPError(403, "Host or Origin rejected")
        if h.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"}:
            raise HTTPError(403, "Cross-site request rejected")

    def body(self, h):
        try:
            length = int(h.headers.get("Content-Length", "0"))
        except ValueError:
            raise HTTPError(400, "Invalid body length") from None
        if length <= 0 or length > 10 * 1024 * 1024:
            raise HTTPError(413, "Body exceeds bounded import size")
        if h.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise HTTPError(415, "JSON content type required")
        try:
            body = json.loads(
                h.rfile.read(length),
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("Non-finite JSON")
                ),
            )
        except (ValueError, UnicodeError):
            raise HTTPError(400, "Invalid JSON body") from None
        if not isinstance(body, dict):
            raise HTTPError(400, "Object body required")
        return body

    def session(self, h):
        try:
            return self.auth.authenticate(h.headers.get("Cookie", ""))
        except ValueError as exc:
            raise HTTPError(401, str(exc)) from None

    def authorize_mission(self, actor, mission):
        if mission["manifest"].get("classification") not in actor[
            "classifications"
        ] or (
            actor["role"] == "operator"
            and mission["manifest"].get("owner") != actor["id"]
        ):
            raise HTTPError(403, "Mission access denied")

    def event_stream(self, h, actor, scope):
        if scope.startswith("mission:"):
            self.authorize_mission(actor, self.store.get_mission(scope[8:]))
        self.events.sync(self.data_root)
        sub = self.events.subscribe(self.policy.get("environment_id"), scope, actor)
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        try:
            while not sub.closed:
                self.auth.authenticate(h.headers.get("Cookie", ""))
                value = sub.next()
                h.wfile.write(
                    ("data: " + json.dumps(value) + "\n\n").encode()
                    if value
                    else b": heartbeat\n\n"
                )
                h.wfile.flush()
                if value and value["type"] == "resync":
                    break
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, ValueError):
            pass
        finally:
            sub.close()

    def handle(self, h):
        path = urlsplit(h.path).path
        if not path.startswith("/api/launchpad/"):
            return False
        try:
            self.check_origin(h)
            if getattr(self, "policy_path", None):
                from roboport_runtime.config import load_runtime_config

                from .policy import load_policy

                self.policy = load_policy(self.policy_path)
                self.runtime_config = load_runtime_config(self.repo)
                self.manager.policy = self.policy
                self.manager.runtime_config = self.runtime_config
            if h.command == "GET" and path == "/api/launchpad/session":
                try:
                    session = self.session(h)
                    value = {"actor": session["actor"], "csrf": session["csrf"]}
                except HTTPError:
                    with self.store.transaction() as db:
                        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    value = {"actor": None, "enrollment_required": count == 0}
                self.reply(h, 200, value)
                return True
            if h.command == "POST" and path == "/api/launchpad/login":
                self.login_attempts = [
                    v for v in self.login_attempts if v > time.time() - 60
                ]
                if len(self.login_attempts) >= 20:
                    raise HTTPError(429, "Login rate limit")
                self.login_attempts.append(time.time())
                body = self.body(h)
                try:
                    session = self.auth.login(
                        body.get("username"), body.get("password")
                    )
                except ValueError as exc:
                    raise HTTPError(401, str(exc)) from None
                self.reply(
                    h,
                    200,
                    {"actor": session["actor"], "csrf": session["csrf"]},
                    session["cookie"],
                )
                return True
            session = self.session(h)
            actor = session["actor"]
            if h.command == "POST":
                try:
                    self.auth.require_csrf(session, h.headers.get("X-CSRF-Token"))
                except ValueError as exc:
                    raise HTTPError(403, str(exc)) from None
                body = self.body(h)
                if path == "/api/launchpad/logout":
                    self.auth.logout(h.headers.get("Cookie", ""))
                    self.reply(
                        h, 200, {"status": "logged-out"}, "roboport_session=; Max-Age=0"
                    )
                elif path == "/api/launchpad/missions":
                    if actor["role"] not in {"operator", "admin"}:
                        raise HTTPError(403, "Operator role required")
                    manifest = validate_manifest(
                        dict(
                            body,
                            owner=actor["id"],
                            model_digests=observe_readiness(
                                self.repo, self.policy, self.runtime_config
                            )["model_digests"],
                        ),
                        self.policy,
                        self.runtime_config,
                        self.repo,
                    )
                    self.reply(h, 201, self.store.create_mission(manifest))
                elif path == "/api/launchpad/decisions":
                    self.authorize_mission(
                        actor, self.store.get_mission(body.get("mission_id"))
                    )
                    self.reply(h, 201, self.store.record_decision(actor, body))
                elif path == "/api/launchpad/replay":
                    from .artifacts import import_replay, replay_snapshot

                    value = import_replay(body.get("records"), body.get("provenance"))
                    value["snapshot"] = replay_snapshot(value)
                    self.reply(h, 200, value)
                elif path == "/api/launchpad/commands" and self.manager:
                    if body.get("intent") not in {"start", "cancel"}:
                        raise ValueError("Unsupported command")
                    mission = self.store.get_mission(body.get("mission_id"))
                    self.authorize_mission(actor, mission)
                    result = (
                        self.manager.dispatch(actor, body)
                        if body["intent"] == "start"
                        else self.manager.cancel(actor, body)
                    )
                    self.reply(h, 202, result)
                else:
                    raise ValueError("Unsupported command or route")
            elif h.command == "GET":
                if path == "/api/launchpad/events":
                    self.event_stream(
                        h,
                        actor,
                        parse_qs(urlsplit(h.path).query).get("scope", ["network"])[0],
                    )
                    return True
                if path.startswith("/api/launchpad/missions/") and path.endswith(
                    "/evidence"
                ):
                    from .artifacts import read_evidence

                    mission = self.store.get_mission(path.split("/")[-2])
                    name = parse_qs(urlsplit(h.path).query).get(
                        "path", ["artifacts/final_output.json"]
                    )[0]
                    value = read_evidence(mission, name, self.data_root, actor)
                elif path == "/api/launchpad/readiness":
                    value = observe_readiness(
                        self.repo, self.policy, self.runtime_config
                    )
                    value.setdefault(
                        "profiles",
                        {
                            k: {
                                "tools": v.get("tools", []),
                                "roots": v.get("roots", []),
                                "enabled": v.get("enabled", False),
                            }
                            for k, v in self.policy.get("profiles", {}).items()
                        },
                    )
                    value.setdefault(
                        "defaults",
                        {
                            "limits": self.runtime_config["budgets"]["per_run"],
                            "context_tokens": self.runtime_config["defaults"].get(
                                "context_tokens", 32768
                            ),
                            "completion_tokens": self.runtime_config["defaults"].get(
                                "max_tokens", 2048
                            ),
                        },
                    )
                    with self.store.transaction() as db:
                        users = {
                            row["id"]: json.loads(row["body"])
                            for row in db.execute("SELECT * FROM users")
                        }
                    value["profile_readiness"] = {}
                    for name, profile in self.policy.get("profiles", {}).items():
                        blockers = []
                        reviewer_id = profile.get("reviewer_id")
                        reviewer = users.get(reviewer_id, {})
                        if profile.get("require_review", True) and (
                            reviewer.get("role") not in {"reviewer", "admin"}
                            or (
                                profile.get("independent_review", True)
                                and reviewer_id == actor["id"]
                            )
                        ):
                            blockers.append(
                                "Named independent proposal reviewer is not enrolled for this profile"
                            )
                        if not profile.get("enabled"):
                            blockers.append("Profile is not enabled")
                        value["profile_readiness"][name] = {
                            "ready": not blockers,
                            "blockers": blockers,
                            "reviewer_id": reviewer_id,
                            "review_phase": "proposal",
                        }
                    value["model_routes"] = {
                        k: {"model": v.get("model"), "provider": v.get("provider")}
                        for k, v in self.runtime_config.get("models", {}).items()
                        if k != "none"
                    }
                elif path == "/api/launchpad/missions":
                    value = [
                        m
                        for m in self.store.list_missions()
                        if actor["role"] != "operator"
                        or m["manifest"]["owner"] == actor["id"]
                    ]
                elif path == "/api/launchpad/snapshot":
                    self.manager.reconcile()
                    self.events.sync(self.data_root)
                    value = self.events.snapshot(
                        self.policy.get("environment_id"), "network", actor
                    )
                else:
                    raise HTTPError(404, "Route not found")
                self.reply(h, 200, value)
            else:
                raise HTTPError(405, "Method not allowed")
        except HTTPError as exc:
            self.reply(h, exc.status, {"error": str(exc)})
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(h, 400, {"error": str(exc)})
        return True
