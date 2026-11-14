"""Terminal-enrolled local identities and revocable browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from http.cookies import SimpleCookie

from .store import canonical


class Auth:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def enroll(self, username, password, role):
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", username) or role not in {
            "operator",
            "reviewer",
            "admin",
        }:
            raise ValueError("invalid username or role")
        if not isinstance(password, str) or not 12 <= len(password) <= 512:
            raise ValueError("password must have 12–512 characters")
        salt = secrets.token_hex(16)
        password_hash = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        record = {
            "id": username,
            "role": role,
            "salt": salt,
            "password_hash": password_hash,
            "version": secrets.token_hex(16),
        }
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO users VALUES(?,?)",
                (username, canonical(record)),
            )
        return {"id": username, "role": role}

    def _actor(self, user):
        return {
            "id": user["id"],
            "role": user["role"],
            "classifications": ["public", "internal"],
        }

    def login(self, username, password):
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or len(username) > 80
            or len(password) > 512
        ):
            raise ValueError("invalid credentials")
        now = self.clock()
        with self.store.transaction() as db:
            key = "login:" + username
            row = db.execute("SELECT body FROM metadata WHERE id=?", (key,)).fetchone()
            rate = json.loads(row["body"]) if row else {"start": now, "count": 0}
            if now - rate["start"] > 300:
                rate = {"start": now, "count": 0}
            if rate["count"] >= 5:
                raise ValueError("login rate limit; wait five minutes")
            rate["count"] += 1
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, canonical(rate))
            )
            row = db.execute(
                "SELECT body FROM users WHERE id=?", (username,)
            ).fetchone()
        user = json.loads(row["body"]) if row else None
        salt = bytes.fromhex(user["salt"]) if user else bytes(16)
        candidate = hashlib.scrypt(
            password.encode(), salt=salt, n=16384, r=8, p=1
        ).hex()
        if not user or not hmac.compare_digest(candidate, user["password_hash"]):
            raise ValueError("invalid credentials")
        token = secrets.token_urlsafe(32)
        record = {
            "user_id": username,
            "version": user["version"],
            "created_at": now,
            "last_seen": now,
            "csrf": secrets.token_urlsafe(32),
            "actor": self._actor(user),
        }
        with self.store.transaction() as db:
            db.execute("DELETE FROM metadata WHERE id=?", (key,))
            db.execute(
                "INSERT INTO sessions VALUES(?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), canonical(record)),
            )
        return {**record, "cookie": "roboport_session=" + token}

    def _session_id(self, cookie):
        jar = SimpleCookie()
        try:
            jar.load(cookie or "")
            token = jar["roboport_session"].value
        except (KeyError, ValueError):
            raise ValueError("authenticated session required") from None
        return hashlib.sha256(token.encode()).hexdigest()

    def authenticate(self, cookie):
        sid = self._session_id(cookie)
        with self.store.transaction() as db:
            row = db.execute("SELECT body FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row:
                raise ValueError("authenticated session required")
            session = json.loads(row["body"])
            row = db.execute(
                "SELECT body FROM users WHERE id=?", (session["user_id"],)
            ).fetchone()
            user = json.loads(row["body"]) if row else None
            if (
                not user
                or user["version"] != session["version"]
                or self.clock() - session["last_seen"] > 900
                or self.clock() - session["created_at"] > 28800
            ):
                raise ValueError("session expired or revoked")
            session["last_seen"] = self.clock()
            db.execute(
                "UPDATE sessions SET body=? WHERE id=?", (canonical(session), sid)
            )
        return session

    def require_csrf(self, session, value):
        if not isinstance(value, str) or not hmac.compare_digest(
            session["csrf"], value
        ):
            raise ValueError("CSRF token required")

    def logout(self, cookie):
        sid = self._session_id(cookie)
        with self.store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
