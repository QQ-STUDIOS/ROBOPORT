"""Portable deployment primitives used by the Windows operator scripts."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import psutil
import yaml


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def validate_config(config: dict) -> None:
    if config.get('schema_version') != 1:
        raise ValueError('unsupported deployment configuration version')
    url = urlparse(config.get('ollama_url', ''))
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.username or url.password or url.path not in ('', '/'):
        raise ValueError('Ollama must use a loopback HTTP endpoint')
    for key in ('ops_port', 'feed_port'):
        if not isinstance(config.get(key), int) or isinstance(config[key], bool) or not 1024 <= config[key] <= 65535:
            raise ValueError(f'invalid {key}')
    if config['ops_port'] == config['feed_port']:
        raise ValueError('operator ports must differ')
    for key in ('context_tokens', 'completion_tokens', 'max_completed_runs', 'max_artifact_bytes'):
        if not isinstance(config.get(key), int) or config[key] <= 0:
            raise ValueError(f'invalid {key}')
    if config['completion_tokens'] >= config['context_tokens']:
        raise ValueError('completion ceiling must be smaller than context')
    if not isinstance(config.get('model'), str) or not config['model'].strip() or config['model'].startswith('-'):
        raise ValueError('invalid explicit model name')


def load_tiers(path: Path) -> dict:
    tiers = read_json(path)
    if tiers.get('schema_version') != 1 or not isinstance(tiers.get('tiers'), dict) or not tiers['tiers']:
        raise ValueError('invalid hardware tier file')
    return tiers['tiers']


def gpu_vram_mib(gpu_info: str, vendor: str) -> int:
    """Largest memory.total (MiB) among nvidia-smi CSV rows naming the vendor."""
    best = 0
    for line in gpu_info.splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 2 and vendor.casefold() in parts[0].casefold() and parts[1].endswith('MiB'):
            try:
                best = max(best, int(parts[1][:-3].strip()))
            except ValueError:
                continue
    return best


def tier_fits(spec: dict, gpu_info: str, memory_bytes: int, model: str) -> bool:
    return (gpu_vram_mib(gpu_info, spec['gpu_vendor']) >= spec['min_vram_mib']
            # Same 2% allowance for firmware-reserved memory as Common.ps1.
            and memory_bytes >= spec['min_ram_gib'] * 2**30 * 0.98
            and model in spec.get('models', []))


def configure_release(release: Path, data: Path, config: dict) -> None:
    validate_config(config)
    machine = config.get('expected_machine')
    if not isinstance(machine, str) or not machine.strip():
        raise ValueError('installation is not bound to a machine')
    routes = {hint: {'provider': 'ollama', 'model': config['model'], 'base_url': config['ollama_url']}
              for hint in ('reasoning-strong', 'tool-use-capable', 'writing-strong', 'any')}
    local = {'models': routes, 'defaults': {'context_tokens': config['context_tokens'], 'max_tokens': config['completion_tokens']},
             'rea': {'approved_roots': [str((release / f'tests/fixtures/{name}').resolve()) for name in ('rea_project', 'rea_empty_project')],
                     'workspace': str(data / 'rea')}}
    launchpad = data / 'launchpad'
    launchpad.mkdir(parents=True, exist_ok=True)
    policy_path = launchpad / 'policy.yaml'
    if not policy_path.exists():
        policy_path.write_text(yaml.safe_dump({'schema_version': 1, 'environment_id': machine, 'expected_machine': machine, 'profiles': {}}), encoding='utf-8')
    (release / 'config').mkdir(parents=True, exist_ok=True)
    (release / 'config/agent_config.local.yaml').write_text(yaml.safe_dump(local), encoding='utf-8')


def prune_runs(root: Path, *, protected: set[str], max_runs: int, max_bytes: int) -> None:
    """Delete only complete, owned run batches; unknown directories survive.

    A batch is complete once benchmark.py has written its summary.json and
    nothing in it is newer. Runs are written one at a time, so terminal
    provenance alone can describe a batch that is still running, and a rerun
    of an existing label writes new files after the old summary.
    """
    candidates = []
    for path in root.iterdir() if root.exists() else []:
        if not path.is_dir() or path.is_symlink() or path.name in protected:
            continue
        provenance = list(path.rglob('provenance.json'))
        summary = path / 'summary.json'
        if not provenance or not summary.is_file():
            continue
        try:
            finished = summary.stat().st_mtime_ns
            if any(p.stat().st_mtime_ns > finished for p in path.rglob('*') if p.is_file()):
                continue
        except OSError:
            continue
        try:
            if any(read_json(p).get('status') not in ('ok', 'failed') for p in provenance):
                continue
        except (ValueError, OSError):
            continue
        if any(p.is_symlink() for p in path.rglob('*')):
            continue
        size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
        candidates.append((path.stat().st_mtime_ns, path, size))
    candidates.sort()
    total = sum(size for _, _, size in candidates)
    while candidates and (len(candidates) > max_runs or total > max_bytes):
        _, path, size = candidates.pop(0)
        shutil.rmtree(path)
        total -= size


def _owned_process(record: dict) -> psutil.Process | None:
    try:
        process = psutil.Process(record['pid'])
        if abs(process.create_time() - record['create_time']) > 0.01:
            raise ValueError('PID was reused; refusing to stop unrelated process')
        if Path(process.exe()).resolve() != Path(record['executable']).resolve():
            raise ValueError('process executable differs from ownership record')
        if process.cmdline() != record['command']:
            raise ValueError('process command differs from ownership record')
        return process
    except psutil.NoSuchProcess:
        return None


def stop_services(install: Path) -> None:
    path = install / 'state/processes.json'
    if not path.exists():
        return
    records = read_json(path)
    stop_path = install / 'state/stop-request.json'
    atomic_json(stop_path, records['supervisor'])
    proc = _owned_process(records['supervisor'])
    if proc and proc.pid != os.getpid():
        # Ask the supervisor to exit before touching its children, so it cannot
        # restart them concurrently. Native Job Objects clean up on its exit.
        try:
            proc.wait(timeout=2)
        except psutil.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    for record in reversed(records.get('children', [])):
        child = _owned_process(record)
        if child:
            descendants = child.children(recursive=True)
            for owned in reversed(descendants):
                owned.terminate()
            child.terminate()
            _, alive = psutil.wait_procs([child, *descendants], timeout=5)
            for owned in alive:
                owned.kill()
            psutil.wait_procs(alive, timeout=5)
    if path.exists() and read_json(path).get('supervisor') == records['supervisor']:
        path.unlink()
    if stop_path.exists() and read_json(stop_path) == records['supervisor']:
        stop_path.unlink()


@contextmanager
def instance_lock(install: Path):
    state = install / 'state'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'instance.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'1')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('deployment instance is already running') from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _record(pid: int) -> dict:
    process = psutil.Process(pid)
    import time
    deadline = time.monotonic() + 1
    while True:
        command = process.cmdline()
        if command:
            return {'pid': pid, 'create_time': process.create_time(), 'executable': process.exe(), 'command': command}
        if time.monotonic() >= deadline:
            raise ValueError('process command identity not ready; refusing incomplete ownership')
        time.sleep(.01)


def _serve(install: Path) -> None:
    from roboport_runtime.process_runner import _WindowsProcess
    manifest = read_json(install / 'state/deployment.json')
    release, data = Path(manifest['release_root']), Path(manifest['data_root'])
    config = manifest['config']
    validate_config(config)
    state = install / 'state/processes.json'
    if state.exists() and _owned_process(read_json(state)['supervisor']):
        raise ValueError('deployment already running')
    for port in (config['ops_port'], config['feed_port']):
        with socket.socket() as check:
            check.bind(('127.0.0.1', port))
    logs = data / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    for name in ('feeds', 'telemetry', 'benchmarks'):
        (data / name).mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update({'ROBOPORT_PROVIDER': 'ollama', 'OLLAMA_HOST': config['ollama_url'],
                'OLLAMA_MODEL_DEFAULT': config['model'], 'OLLAMA_MODEL_REASONING': config['model'],
                'ROBOPORT_FEED_SOURCE': 'runtime', 'ROBOPORT_FEED_GLOB': str(data / 'feeds/*.jsonl'),
                'ROBOPORT_SERVE_WEB': '1', 'ROBOPORT_BIND_HOST': '127.0.0.1', 'PORT': str(config['feed_port'])})
    python = str(release / '.venv/Scripts/python.exe') if os.name == 'nt' else sys.executable
    jobs = [('ops', [python, str(release / 'dashboard/bridge.py'), '--runs-dir', str(data / 'telemetry'), '--host', '127.0.0.1', '--port', str(config['ops_port']), '--data-root', str(data), '--policy', str(data / 'launchpad/policy.yaml')]),
            ('feed', [python, str(release / 'control_surface/collector/server.py')])]
    children, record = [], {'supervisor': _record(os.getpid()), 'children': []}
    atomic_json(state, record)
    stopping = threading.Event()
    if os.name != 'nt':
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    attempts = 0

    def stream_log(stream, path):
        with path.open('ab', buffering=0) as output:
            while chunk := stream.read(4096):
                if output.tell() + len(chunk) > 16 * 1024 * 1024:
                    output.seek(0)
                    output.truncate()
                output.write(chunk)

    def launch(name, argv):
        process = _WindowsProcess(argv, release, env) if os.name == 'nt' else subprocess.Popen(argv, cwd=release, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        for stream, suffix in ((process.stdout, 'out'), (process.stderr, 'err')):
            threading.Thread(target=stream_log, args=(stream, logs / f'{name}.{suffix}.log'), daemon=True).start()
        return process

    try:
        for name, argv in jobs:
            children.append(launch(name, argv))
        record['children'] = [_record(p.pid) for p in children]
        atomic_json(state, record)
        while not stopping.wait(.2):
            stop_path = install / 'state/stop-request.json'
            if stop_path.exists() and read_json(stop_path) == record['supervisor']:
                break
            for index, child in enumerate(children):
                if child.poll() is None:
                    continue
                attempts += 1
                if attempts > 3:
                    raise RuntimeError('operator surfaces exhausted restart allowance; inspect logs')
                if os.name == 'nt':
                    child.close()
                children[index] = launch(*jobs[index])
                record['children'] = [_record(p.pid) for p in children]
                atomic_json(state, record)
    finally:
        for child in children:
            if os.name == 'nt':
                child.close()
            else:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                child.wait(timeout=5)
        if state.exists() and read_json(state).get('supervisor') == record['supervisor']:
            state.unlink()


def serve(install: Path) -> None:
    with instance_lock(install):
        _serve(install)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['configure', 'serve', 'stop', 'prune'])
    parser.add_argument('--install', type=Path)
    parser.add_argument('--release', type=Path)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--machine', help='machine recorded by setup preflight')
    parser.add_argument('--hardware-tier', help='hardware tier selected by setup preflight')
    args = parser.parse_args()
    if args.action == 'configure':
        config = read_json(args.config)
        if config.get('expected_machine') and args.machine and config['expected_machine'].casefold() != args.machine.casefold():
            raise ValueError('configuration is pinned to a different machine')
        config.setdefault('expected_machine', args.machine)
        if args.hardware_tier:
            config['hardware_tier'] = args.hardware_tier
        configure_release(args.release, args.data, config)
    elif args.action == 'serve':
        serve(args.install)
    elif args.action == 'stop':
        stop_services(args.install)
    else:
        manifest = read_json(args.install / 'state/deployment.json')
        root = Path(manifest['data_root']) / 'benchmarks'
        protected = set(manifest.get('protected_runs', []))
        config = manifest['config']
        prune_runs(root, protected=protected, max_runs=config['max_completed_runs'], max_bytes=config['max_artifact_bytes'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
