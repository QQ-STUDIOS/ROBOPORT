"""Project-local, pinned static REA operation; never accepts command strings."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from pathlib import Path

from .budget import current_budget, request_timeout
from .config import REPO, load_runtime_config
from .process_runner import run_bounded

VERSION = '6.4.0'


class ReaError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _unsafe(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _contained(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _snapshot(target: Path, destination: Path) -> dict:
    manifest = {}
    size = 0
    destination.mkdir()
    for parent, directories, files in os.walk(target, followlinks=False):
        request_timeout(120)
        for name in directories + files:
            if _unsafe(Path(parent) / name):
                raise ReaError('unsafe_path', 'symlinks and junctions are not accepted')
        for name in files:
            path = Path(parent) / name
            if not path.is_file():
                raise ReaError('unsafe_path', 'only regular project files are accepted')
            size += path.stat().st_size
            if size > 10 * 1024 * 1024 or len(manifest) >= 1000:
                raise ReaError('input_limit', 'project exceeds 10 MiB or 1000 files')
            relative = path.relative_to(target)
            dest = destination / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Recheck links before opening; read no-follow on platforms that
            # support it and compare file identity before/after copying.
            before = path.stat(follow_symlinks=False)
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                data = stream.read(10 * 1024 * 1024 + 1)
                after = os.fstat(stream.fileno())
            if len(data) > 10 * 1024 * 1024 or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or _unsafe(path):
                raise ReaError('unsafe_path', 'project changed while snapshotting')
            dest.write_bytes(data)
            manifest[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    return manifest


def analyze_project(project_path: str) -> dict:
    started = time.monotonic()
    workspace = None
    try:
        config = load_runtime_config().get('rea') or {}
        roots = [(Path(r) if Path(r).is_absolute() else REPO / r).resolve() for r in config.get('approved_roots', [])]
        original = Path(project_path)
        if not original.is_absolute():
            original = REPO / original
        # Reject a symlink/junction anywhere on the caller's path.
        if any(_unsafe(p) for p in [original, *original.parents] if p.exists()):
            raise ReaError('unsafe_path', 'target path traverses a symlink or junction')
        target = original.resolve(strict=True)
        if not target.is_dir() or not _contained(target, roots):
            raise ReaError('unapproved_path', 'target is outside approved project roots')
        budget = current_budget()
        run_dir = getattr(budget, 'artifact_dir', None)
        base = (run_dir / 'rea') if run_dir else Path(config.get('workspace', REPO / 'runs' / 'rea')).resolve()
        if target == base or target in base.parents:
            raise ReaError('unsafe_path', 'evidence workspace cannot be inside the target')
        base.mkdir(parents=True, exist_ok=True)
        workspace = base / uuid.uuid4().hex
        workspace.mkdir()
        snapshot = workspace / 'project'
        manifest = _snapshot(target, snapshot)
        package = REPO / 'integrations/rea/node_modules/rea-agents'
        if json.loads((package / 'package.json').read_text(encoding='utf-8'))['version'] != VERSION:
            raise ReaError('dependency_error', 'REA version differs from lock')
        node = shutil.which('node')
        if node is None:
            raise ReaError('dependency_error', 'supported Node runtime is missing')
        env = dict(os.environ)
        # The static workflow is run in an owned workspace. Isolate home/temp
        # resolution without altering the parent process's environment.
        home = workspace / 'home'
        home.mkdir()
        env.update({'HOME': str(home), 'USERPROFILE': str(home), 'TMP': str(home), 'TEMP': str(home), 'TMPDIR': str(home), 'NODE_OPTIONS': '--max-old-space-size=512'})
        result = run_bounded([node, str(package / 'scripts/rea.mjs'), 'analyze-javascript-application', str(snapshot), '--json'],
                             cwd=workspace, env=env, timeout_seconds=request_timeout(120), max_output_bytes=16 * 1024 * 1024)
        if result['error']:
            raise ReaError(result['error']['code'], result['error'].get('message', result['error']['code']))
        if result['exit_code'] != 0:
            raise ReaError('analysis_failed', result['stderr'][-2000:])
        try:
            evidence = json.loads(result['stdout'])
        except json.JSONDecodeError as exc:
            raise ReaError('invalid_json', 'REA did not emit valid JSON') from exc
        normalized = evidence.get('normalized_result') if isinstance(evidence, dict) else None
        if not isinstance(normalized, dict) or evidence.get('operation') != 'analyze_javascript_application' or not isinstance(evidence.get('evidence_id'), str) or not isinstance(normalized.get('graph'), dict) or not isinstance(evidence.get('limitations'), list):
            raise ReaError('invalid_contract', 'REA evidence contract is invalid')
        if normalized.get('integrity_contradictions'):
            raise ReaError('integrity_failure', 'REA reported contradictory input bytes')
        artifact = workspace / 'evidence.json'
        data = result['stdout'].encode('utf-8')
        tmp = artifact.with_suffix('.tmp')
        tmp.write_bytes(data)
        tmp.replace(artifact)
        (workspace / 'input-manifest.json').write_text(json.dumps({'target': str(target), 'files': manifest}, indent=2), encoding='utf-8')
        nodes = normalized['graph'].get('nodes') or []
        findings = [{'kind': n.get('kind'), 'observations': n.get('observations', [])[:2]} for n in nodes[:4]]
        samples = []
        for index, name in enumerate(sorted(manifest)):
            sample_path = snapshot / name
            if any(_unsafe(p) for p in [sample_path, *sample_path.parents]):
                raise ReaError('unsafe_path', 'snapshot sample traverses a link')
            before = sample_path.stat(follow_symlinks=False)
            if before.st_size > 10 * 1024 * 1024 or not stat.S_ISREG(before.st_mode):
                raise ReaError('input_limit', 'snapshot sample exceeds read limit')
            fd = os.open(sample_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                sample_data = stream.read(10 * 1024 * 1024 + 1)
                after = os.fstat(stream.fileno())
            if (len(sample_data) > 10 * 1024 * 1024 or
                    (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or
                    hashlib.sha256(sample_data).hexdigest() != manifest[name] or
                    any(_unsafe(p) for p in [sample_path, *sample_path.parents])):
                raise ReaError('integrity_failure', 'snapshot sample bytes changed')
            if index < 4 and len(sample_data) <= 4096 and name.endswith(('.js', '.html', '.json', '.md')):
                try:
                    samples.append({'path': name, 'text': sample_data.decode('utf-8')})
                except UnicodeDecodeError:
                    pass
        summary = {'status': 'ok', 'artifact_path': str(artifact), 'artifact_sha256': hashlib.sha256(data).hexdigest(),
                   'rea_version': VERSION, 'evidence_id': evidence['evidence_id'], 'elapsed_ms': int((time.monotonic() - started) * 1000),
                   'limitations': evidence['limitations'][:10], 'findings': findings, 'module_count': sum(n.get('kind') == 'javascript-module' for n in nodes),
                   'source_inventory': dict(list(sorted(manifest.items()))[:20]), 'inventory_complete': len(manifest) <= 20,
                   'source_samples': samples,
                   'scope': 'Static snapshot only; absent files may exist elsewhere. No runtime tests or performance measurements were executed.'}
        if len(json.dumps(summary).encode()) > 16384:
            summary['source_samples'] = []
            summary['source_inventory'] = {}
            summary['inventory_complete'] = False
            summary['findings'] = []
            summary['limitations'] = ['Inline evidence exceeded summary cap; inspect saved artifact.']
        return summary
    except (OSError, ValueError, KeyError) as exc:
        error = {'status': 'failed', 'error': {'code': getattr(exc, 'code', 'dependency_error'), 'message': str(exc)},
                 'rea_version': VERSION, 'elapsed_ms': int((time.monotonic() - started) * 1000)}
        if workspace:
            (workspace / 'failure.json').write_text(json.dumps(error, indent=2), encoding='utf-8')
        return error
