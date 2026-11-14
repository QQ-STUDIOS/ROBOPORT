"""Timestamped observations; unknown is different from a healthy service."""

import socket
import time
from pathlib import Path

import psutil
import requests


def observe_readiness(repo: Path, policy: dict, runtime_config: dict) -> dict:
    now = time.time()
    blockers, observations, digests = [], [], {}
    enabled = [p for p in policy.get("profiles", {}).values() if p.get("enabled")]
    if not enabled:
        blockers.append("No approved mission profile is enabled")
    expected = policy.get("expected_machine")
    if expected and socket.gethostname().casefold() != expected.casefold():
        blockers.append("Expected workstation identity has not been observed")
    observations.append(
        {
            "id": "machine",
            "state": "observed",
            "value": socket.gethostname(),
            "source": "OS",
            "observed_at": now,
        }
    )
    observations.append(
        {
            "id": "memory",
            "state": "observed",
            "value": psutil.virtual_memory().total,
            "source": "OS",
            "observed_at": now,
        }
    )
    disks = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append(
                {
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "total_bytes": usage.total,
                    "free_bytes": usage.free,
                }
            )
        except OSError:
            continue
    observations.append(
        {
            "id": "volumes",
            "state": "observed" if disks else "unknown",
            "value": disks,
            "source": "OS mounted volumes; physical drive inventory pending",
            "observed_at": now,
        }
    )
    if enabled:
        routes = {
            (r.get("base_url", "http://127.0.0.1:11434").rstrip("/"), r["model"])
            for k, r in runtime_config.get("models", {}).items()
            if k != "none" and r.get("model")
        }
        for base, name in sorted(routes):
            try:
                result = requests.get(base + "/api/tags", timeout=1).json()
                model = next(
                    (m for m in result.get("models", []) if m.get("name") == name), None
                )
                if not model or not model.get("digest"):
                    raise ValueError("configured model not installed")
                digests[name] = model["digest"]
                observations.append(
                    {
                        "id": "model:" + name,
                        "state": "observed",
                        "value": model["digest"],
                        "source": "Ollama tags",
                        "observed_at": now,
                    }
                )
            except (requests.RequestException, ValueError, KeyError):
                blockers.append("Configured local model unavailable: " + name)
                observations.append(
                    {
                        "id": "model:" + name,
                        "state": "unknown",
                        "value": None,
                        "source": "Ollama tags",
                        "observed_at": now,
                    }
                )
    observations.append(
        {
            "id": "compliance",
            "state": "unknown",
            "value": None,
            "source": "Intune prior report; current observation unavailable",
            "observed_at": None,
        }
    )
    observations.append(
        {
            "id": "gpu_placement",
            "state": "unknown",
            "value": None,
            "source": "Verify loaded model placement on target",
            "observed_at": None,
        }
    )
    return {
        "environment_id": policy.get("environment_id") or socket.gethostname(),
        "ready": not blockers,
        "blockers": blockers,
        "observed_at": now,
        "expires_at": now + 30,
        "observations": observations,
        "model_digests": digests,
        "profiles": {
            k: {
                "tools": v.get("tools", []),
                "roots": v.get("roots", []),
                "approval": v.get("approval"),
                "enabled": v.get("enabled", False),
            }
            for k, v in policy.get("profiles", {}).items()
        },
        "defaults": {
            "limits": runtime_config.get("budgets", {}).get("per_run", {}),
            "context_tokens": runtime_config.get("defaults", {}).get(
                "context_tokens", 32768
            ),
            "completion_tokens": runtime_config.get("defaults", {}).get(
                "max_tokens", 2048
            ),
        },
        "expected_storage_bytes": 4000000000000,
        "expected_memory_bytes": 128 * 1024**3,
        "capabilities": {
            "remote_wake": False,
            "openai_sessions": False,
            "corpus": False,
            "container_admin": False,
        },
    }
