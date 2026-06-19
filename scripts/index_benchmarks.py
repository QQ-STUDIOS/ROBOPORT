#!/usr/bin/env python3
"""Walk evals/benchmarks/ and write evals/benchmarks/_index.json.

The index is a flat list of every graded run with its blocker_failed and
pass_rate, sorted newest-graded first. The skill_daemon uses this to ask
"what's new since last tick" without re-walking the whole tree on every
event.

Usage:

    python scripts/index_benchmarks.py          # rebuild from scratch
    python scripts/index_benchmarks.py --print  # also print summary to stdout

Library use (daemon will call this directly on init + after each new grade):

    from scripts.index_benchmarks import build_index, write_index
    idx = build_index()
    write_index(idx)

The index is written atomically (tmp file + rename) so a reader will never
see a half-written file.

Design notes (see plan: M0 — indexer):
  - Only graded runs are indexed. Ungraded runs (benchmark.py without --grade)
    are out of scope for the optimizer trigger.
  - sqlite remains the source of truth in M2+; this JSON file is the
    human-readable view (and the only state M0 ships).
  - Stdlib only. watchdog is in requirements.txt for M2 but not used here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO / "evals" / "benchmarks"
INDEX_PATH = BENCH_DIR / "_index.json"


def _read_grading(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # Malformed or unreadable — surface as a warning, not a crash.
        print(f"  [warn] skipping {path}: {e}", file=sys.stderr)
        return None


def build_index(bench_dir: Path = BENCH_DIR) -> dict[str, Any]:
    """Walk bench_dir and produce the index dict.

    Structure:
      {
        "indexed_at":   "<ISO 8601 UTC>",
        "bench_dir":    "evals/benchmarks",
        "run_count":    int,
        "blocker_failed_count": int,
        "runs": [
          {
            "run_path":       "evals/benchmarks/<label>/eval_<id>/run_<n>",
            "grading_path":   "<same>/grading.json",
            "blocker_failed": bool,
            "pass_rate":      float | null,
            "graded_at":      "<ISO 8601>" | null,
            "eval_id":        "<id from path>",
            "label":          "<benchmark label dir name>"
          },
          ...
        ]
      }
    """
    runs: list[dict[str, Any]] = []
    if not bench_dir.exists():
        # Empty index is valid — the daemon needs to handle a cold start.
        return _wrap(runs, bench_dir)

    for label_dir in sorted(bench_dir.iterdir()):
        if not label_dir.is_dir():
            # Skip _index.json and any other files at the bench root.
            continue
        for eval_dir in sorted(label_dir.glob("eval_*")):
            if not eval_dir.is_dir():
                continue
            for run_dir in sorted(eval_dir.glob("run_*")):
                if not run_dir.is_dir():
                    continue
                grading = run_dir / "grading.json"
                if not grading.is_file():
                    continue
                data = _read_grading(grading)
                if data is None:
                    continue
                runs.append({
                    "run_path":       str(run_dir.relative_to(REPO)),
                    "grading_path":   str(grading.relative_to(REPO)),
                    "blocker_failed": bool(data.get("blocker_failed", False)),
                    "pass_rate":      data.get("pass_rate"),
                    "graded_at":      data.get("graded_at"),
                    "eval_id":        eval_dir.name.removeprefix("eval_"),
                    "label":          label_dir.name,
                })

    # Newest graded first; ungraded (graded_at=None) sort last.
    runs.sort(key=lambda r: (r["graded_at"] or ""), reverse=True)
    return _wrap(runs, bench_dir)


def _wrap(runs: list[dict[str, Any]], bench_dir: Path) -> dict[str, Any]:
    return {
        "indexed_at":            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bench_dir":             str(bench_dir.relative_to(REPO)),
        "run_count":             len(runs),
        "blocker_failed_count":  sum(1 for r in runs if r["blocker_failed"]),
        "runs":                  runs,
    }


def write_index(idx: dict[str, Any], path: Path = INDEX_PATH) -> None:
    """Atomically write the index. Writes to a sibling .tmp, then os.replace.

    The daemon may be reading _index.json at any moment; os.replace is atomic
    on POSIX and Windows so a reader never sees a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(idx, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--print", action="store_true",
                    help="Print a summary to stdout after writing the index.")
    ap.add_argument("--bench-dir", type=Path, default=BENCH_DIR,
                    help=f"Override the benchmarks dir (default: {BENCH_DIR.relative_to(REPO)})")
    args = ap.parse_args(argv)

    idx = build_index(args.bench_dir)
    write_index(idx, args.bench_dir / "_index.json")

    if args.print:
        print(f"indexed {idx['run_count']} run(s); "
              f"{idx['blocker_failed_count']} with blocker_failed=true")
        for r in idx["runs"][:10]:
            mark = "X" if r["blocker_failed"] else "."
            rate = f"{r['pass_rate']:.2f}" if isinstance(r["pass_rate"], (int, float)) else "  - "
            print(f"  [{mark}] pass={rate}  {r['run_path']}")
        if idx["run_count"] > 10:
            print(f"  ... (+{idx['run_count'] - 10} more)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
