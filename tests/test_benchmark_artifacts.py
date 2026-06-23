"""Phase-1.x: benchmark.py step_done carries duration_ms + config_fp.

These are the fields diff_runs.py reads for the latency and comparability
dimensions, so they must actually be written to the run-dir artifact.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import benchmark  # noqa: E402


def test_step_fingerprint_is_stable_and_sensitive():
    registry = {"agents": {"synthesizer": {"path": "p", "role": "domain",
                                           "deterministic": True, "model_hint": "reasoning-strong"}}}
    config = {"models": {"reasoning-strong": {"provider": "ollama", "model": "qwen3:14b"}}}
    a = benchmark.step_fingerprint("synthesizer", registry, config)
    b = benchmark.step_fingerprint("synthesizer", registry, config)
    assert a == b and isinstance(a, str) and len(a) == 12
    # a different resolved model => a different fingerprint
    config2 = {"models": {"reasoning-strong": {"provider": "anthropic", "model": "claude-sonnet-4-6"}}}
    assert benchmark.step_fingerprint("synthesizer", registry, config2) != a


def test_benchmark_run_log_has_latency_and_fingerprint(tmp_path):
    out = tmp_path / "bench"
    subprocess.run(
        [sys.executable, "scripts/benchmark.py", "--target", "jd_crew",
         "--runs", "1", "--out", str(out), "--eval-set", "evals/evals.json"],
        cwd=REPO, check=True, capture_output=True, text=True,
    )
    logs = list(out.glob("eval_*/run_*/run.log"))
    assert logs, "benchmark produced no run.log"
    step_dones = [
        json.loads(line)
        for line in logs[0].read_text().splitlines()
        if line.strip() and json.loads(line).get("event") == "step_done"
    ]
    assert step_dones, "no step_done events written"
    for sd in step_dones:
        assert isinstance(sd.get("duration_ms"), int)
        assert isinstance(sd.get("config_fp"), str) and len(sd["config_fp"]) >= 8


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
