"""Fail-closed profile scope and reviewed manifest fingerprints."""

from __future__ import annotations

import hashlib
import math
import socket
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .store import digest

SUPPORTED_TOOLS = {"rea_analyze_project"}
SUPPORTED_PROFILES = {"environment_investigation", "jd_crew"}


def load_policy(path: Path):
    if not path.exists():
        return {
            "schema_version": 1,
            # No policy yet: the environment is the machine the gateway runs on.
            "environment_id": socket.gethostname(),
            "profiles": {},
        }
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        not isinstance(policy, dict)
        or policy.get("schema_version") != 1
        or not isinstance(policy.get("profiles"), dict)
    ):
        raise ValueError("invalid launch pad policy")
    return policy


def component_inventory(repo: Path):
    paths = []
    for directory in (
        "agents",
        "stations",
        "resources/schemas",
        "scripts/roboport_runtime",
        "dashboard/launchpad",
    ):
        paths.extend(
            p
            for p in (repo / directory).rglob("*")
            if p.is_file() and p.suffix in {".py", ".md", ".json", ".yaml"}
        )
    paths.extend(
        repo / name
        for name in (
            "scripts/benchmark.py",
            "scripts/deployment_smoke.py",
            "tests/fixtures/deployment_jd.json",
            "scripts/synthesize.py",
            "scripts/launchpad_worker.py",
            "integrations/rea/package-lock.json",
            "evals/deployment_jd.json",
            "evals/environment_investigation.json",
        )
    )
    return {
        p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
        if p.exists()
    }


def project_inventory(root):
    values, size = {}, 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("project root contains unsafe link")
        if not path.is_file():
            continue
        size += path.stat().st_size
        if size > 10 * 1024 * 1024 or len(values) >= 1000:
            raise ValueError("project exceeds 10MiB or 1000 files")
        data = path.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("project file too large")
        values[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    return values


def validate_manifest(
    manifest: dict, policy: dict, runtime_config: dict, repo: Path
) -> dict:
    profile_id = manifest.get("profile")
    profile = policy.get("profiles", {}).get(profile_id) or {}
    if profile_id not in SUPPORTED_PROFILES or profile.get("enabled") is not True:
        raise ValueError("profile unavailable or not enabled")
    if profile.get("review_phase", "proposal") != "proposal":
        raise ValueError(
            "unsupported review checkpoint phase; this slice supports proposal review only"
        )
    approval = profile.get("approval") or {}
    expiry = approval.get("expires_at")
    if (
        not approval.get("id")
        or not isinstance(expiry, (int, float))
        or not math.isfinite(expiry)
        or expiry <= time.time()
    ):
        raise ValueError("profile approval missing or expired")
    root = Path(manifest.get("project_root", "")).resolve()
    roots = [Path(p).resolve() for p in profile.get("roots", [])]
    if not root.is_dir() or not any(root == p or root.is_relative_to(p) for p in roots):
        raise ValueError("project root not approved")
    tools = manifest.get("tools")
    allowed = set(profile.get("tools", [])) & SUPPORTED_TOOLS
    if not isinstance(tools, list) or set(tools) - allowed:
        raise ValueError("tool scope not approved")
    if profile_id == "environment_investigation" and "rea_analyze_project" not in tools:
        raise ValueError("REA capability required")
    classification = manifest.get("classification")
    if classification not in {
        "public",
        "internal",
    } or classification not in profile.get("classifications", []):
        raise ValueError("classification not supported or approved")
    limits = manifest.get("limits")
    caps = runtime_config.get("budgets", {}).get("per_run", {})
    if not isinstance(limits, dict) or set(limits) != set(caps):
        raise ValueError("exact run limits required")
    for key, cap in caps.items():
        value = limits[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or value > min(cap, profile.get("limits", {}).get(key, cap))
        ):
            raise ValueError(f"invalid or over-policy limit: {key}")
        if key.endswith("calls") and int(value) != value:
            raise ValueError("call ceiling must be integral")
    for key, ceiling in (
        (
            "context_tokens",
            runtime_config.get("defaults", {}).get("context_tokens", 32768),
        ),
        (
            "completion_tokens",
            runtime_config.get("defaults", {}).get("max_tokens", 2048),
        ),
    ):
        value = manifest.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= ceiling
        ):
            raise ValueError(f"invalid {key}")
    if manifest["completion_tokens"] >= manifest["context_tokens"]:
        raise ValueError("completion exceeds context")
    for hint, route in runtime_config.get("models", {}).items():
        if hint == "none":
            continue
        if route.get("provider") != "ollama" or urlparse(
            route.get("base_url", "")
        ).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("only local Ollama routes supported")
    if (
        not isinstance(manifest.get("objective"), str)
        or not 1 <= len(manifest["objective"].strip()) <= 4000
    ):
        raise ValueError("objective required, maximum 4000 characters")
    criteria = manifest.get("success_criteria")
    if (
        not isinstance(criteria, list)
        or not criteria
        or any(
            not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in criteria
        )
        or len(criteria) > 20
    ):
        raise ValueError("bounded success criteria required")
    if not manifest.get("owner"):
        raise ValueError("authenticated owner required")
    value = {
        key: deepcopy(manifest[key])
        for key in (
            "profile",
            "objective",
            "tools",
            "classification",
            "owner",
            "limits",
            "context_tokens",
            "completion_tokens",
            "success_criteria",
        )
    }
    value.update(
        project_root=str(root),
        project_hashes=project_inventory(root),
        model_digests=deepcopy(manifest.get("model_digests", {})),
        environment_id=policy.get("environment_id") or socket.gethostname(),
        require_review=bool(profile.get("require_review", True)),
        review_phase="proposal",
        reviewer_id=profile.get("reviewer_id"),
        independent_review=bool(profile.get("independent_review", True)),
        approval=deepcopy(approval),
        policy_hash=digest(policy),
        runtime_config_hash=digest(runtime_config),
        runtime_config=deepcopy(runtime_config),
        component_hashes=component_inventory(repo),
    )
    value["manifest_hash"] = digest(value)
    return value
