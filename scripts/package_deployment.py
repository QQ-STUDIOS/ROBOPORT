"""Build a reproducible source deployment from tracked, approved files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOTS = {
    "agents",
    "config",
    "resources",
    "workflows",
    "evals",
    "scripts",
    "dashboard",
    "control_surface",
    "tests",
    "integrations",
    "deploy",
    "docs",
    "stations",
}


def build_package(repo: Path, output: Path) -> dict:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True
    )
    files = {}
    for value in sorted(result.stdout.decode().split("\0")):
        if not value:
            continue
        relative = Path(value)
        parts = relative.parts
        if parts[0] not in ROOTS and value not in {
            "README.md",
            "LICENSE",
            "requirements.txt",
        }:
            continue
        if parts[0] == "tests" and (len(parts) < 2 or parts[1] != "fixtures"):
            continue
        if (
            any(
                p
                in {
                    "node_modules",
                    ".venv",
                    "__pycache__",
                    ".git",
                    "benchmarks",
                    "implemented",
                }
                for p in parts
            )
            or ".local." in value
            or relative.name.startswith(".env")
            or relative.suffix in {".key", ".pem", ".sqlite3", ".db", ".pyc"}
        ):
            continue
        path = repo / relative
        if path.is_symlink() or not path.resolve().is_relative_to(repo.resolve()):
            raise ValueError("unsafe package source path")
        if not path.is_file():
            raise ValueError("tracked package source missing: " + value)
        files[value] = path.read_bytes()
    inventory = {
        name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in files.items()
    }
    files["PACKAGE-MANIFEST.json"] = json.dumps(
        {"schema_version": 1, "files": inventory}, sort_keys=True, indent=2
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo("ROBOPORT/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, body)
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        sha + "  " + output.name + "\n"
    )
    return {
        "path": str(output),
        "sha256": sha,
        "files": len(files),
        "bytes": output.stat().st_size,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build_package(Path(__file__).resolve().parents[1], args.output), indent=2
        )
    )


if __name__ == "__main__":
    main()
