"""Fail-closed deployment acceptance. Missing target observations stay pending."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import psutil
import requests
from roboport_runtime.process_runner import run_bounded
from station_packs import load_output_schema
from windows_deploy import _owned_process, atomic_json, load_tiers, read_json, stop_services, tier_fits

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
REQUIRED = ['machine', 'versions', 'model', 'live_runs', 'gpu_inference', 'telemetry_endpoints', 'restart', 'reboot_recovery', 'rollback', 'artifact_retention', 'launchpad_walkthrough']


def _result(passed: bool, detail=None) -> dict:
    return {'status': 'passed' if passed else 'failed', 'detail': detail}


def _stub(value) -> bool:
    if isinstance(value, dict):
        return any(k in ('stub', '_stub', 'deterministic_stub') and v is True or _stub(v) for k, v in value.items())
    return isinstance(value, list) and any(_stub(v) for v in value)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def verify_artifacts(run_dir: Path, expected_mode: str) -> dict:
    checks = {}
    files = ['provenance.json', 'plan.json', 'budget.json', 'final_output.json', 'grading.json', 'run.log']
    checks['required_artifacts'] = all((run_dir / f).is_file() for f in files)
    try:
        provenance = read_json(run_dir / 'provenance.json')
    except (OSError, ValueError):
        provenance = {}
    checks['live_provenance'] = provenance.get('mode') == expected_mode and provenance.get('status') == 'ok' and bool(provenance.get('finished_at'))
    try:
        grading = read_json(run_dir / 'grading.json')
        results = grading.get('results') or []
        checks['grading'] = sorted(r.get('expectation', '') for r in results) == sorted(provenance.get('expectations', [])) and grading.get('pass_rate') == 1 and grading.get('blocker_failed') is False and bool(results) and all(r.get('verdict') == 'PASS' and r.get('evidence') and '[stub' not in r['evidence'] for r in results)
    except (OSError, ValueError, TypeError):
        checks['grading'] = False
    try:
        plan, output = read_json(run_dir / 'plan.json'), read_json(run_dir / 'final_output.json')
        schema_doc = load_output_schema(REPO)
        steps = plan.get('steps') or []
        checks['step_contracts'] = bool(steps)
        for step in steps:
            artifact = read_json(run_dir / 'steps' / f"{step['id']}.json")
            kind = step['output_type']
            ref = kind[5:-1] if kind.startswith('list[') else kind
            if ref not in schema_doc['definitions']:
                checks['step_contracts'] = False
                continue
            schema = {'definitions': schema_doc['definitions'], 'type': 'array', 'minItems': 1, 'items': {'$ref': f'#/definitions/{ref}'}} if kind.startswith('list[') else {'definitions': schema_doc['definitions'], '$ref': f'#/definitions/{ref}'}
            jsonschema.validate(artifact['output'], schema)
            checks['step_contracts'] &= artifact.get('status') == 'ok' and bool(artifact.get('criteria_results')) and all(c.get('passed') is True for c in artifact['criteria_results']) and not _stub(artifact['output'])
        checks['final_output'] = bool(output) and not _stub(output) and output == read_json(run_dir / 'steps' / f"{steps[-1]['id']}.json")['output']
        logs = _jsonl(run_dir / 'run.log')
        actual = [row for row in logs if row.get('event') == 'step_done' and row.get('llm_calls', 0) > 0]
        checks['model_usage'] = bool(actual) and all(row.get('provider') == 'ollama' and row.get('model') and row.get('prompt_tokens', 0) > 0 and row.get('completion_tokens', 0) > 0 for row in actual)
        budget = read_json(run_dir / 'budget.json')
        checks['budget'] = budget['llm_calls'] > 0 and budget['llm_calls'] <= budget['limits']['max_llm_calls'] and budget['tool_calls'] <= budget['limits']['max_tool_calls'] and budget['elapsed_seconds'] <= budget['limits']['max_wall_seconds'] and budget['spent_usd'] + budget['reserved_usd'] <= budget['limits']['max_usd']
        run_id = provenance.get('telemetry_run_id')
        feeds = _jsonl(Path(provenance['feed_path']))
        ops = _jsonl(Path(provenance['ops_log_path']))
        checks['telemetry'] = bool(run_id) and any(r.get('event') == 'crew_end' and r.get('run_id') == run_id and r.get('status') == 'ok' for r in feeds) and any(r.get('event') == 'run.start' and r.get('run_id') == run_id for r in ops) and any(r.get('event') == 'run.complete' for r in ops)
        if provenance.get('target') == 'environment_investigation':
            audit = _jsonl(run_dir / 'tool-results.jsonl')
            successes = [r['result'] for r in audit if r.get('tool') == 'rea_analyze_project' and r.get('result', {}).get('status') == 'ok']
            ref = output.get('evidence') or {}
            from roboport_runtime.investigation import validate_reference
            checks['rea_reference'] = not validate_reference(ref, successes)
            artifact = Path(ref['artifact_path']).resolve()
            checks['rea_artifact'] = run_dir.resolve() in artifact.parents and hashlib.sha256(artifact.read_bytes()).hexdigest() == ref['artifact_sha256']
            if not checks['rea_artifact']:
                raise ValueError('REA evidence path/hash is invalid')
            evidence = read_json(artifact)
            checks['rea_contract'] = evidence.get('operation') == 'analyze_javascript_application' and evidence.get('evidence_id') == ref['evidence_id'] and isinstance(evidence.get('normalized_result', {}).get('graph'), dict) and not evidence.get('normalized_result', {}).get('integrity_contradictions')
            checks['proposal'] = output.get('approval_required') is True and all(c.get('evidence_id') == ref.get('evidence_id') and c.get('validation') and c.get('expected_impact') and isinstance(c.get('uncertainties'), list) for c in output.get('changes', []))
    except (OSError, ValueError, TypeError, KeyError, IndexError, jsonschema.ValidationError):
        checks['artifact_validation'] = False
    return {'status': 'passed' if checks and all(checks.values()) else 'failed', 'checks': checks,
            'failed_checks': [k for k, v in checks.items() if not v], 'run_dir': str(run_dir)}


def observe_launchpad_walkthrough(data: Path) -> dict:
    """Target acceptance requires real console missions and named review evidence."""
    database = data / 'launchpad/launchpad.sqlite3'
    if not database.is_file():
        return {'status': 'pending', 'detail': 'Enroll identities and complete the native console walkthrough'}
    import hashlib
    import math

    from dashboard.launchpad.store import Store
    store = Store(database)
    validated = set()
    for mission in store.list_missions():
        if mission['state'] != 'completed' or mission.get('outcome', {}).get('test_fixture'):
            continue
        profile = mission['manifest'].get('profile')
        if profile not in {'jd_crew', 'environment_investigation'}:
            continue
        directory = data / 'missions' / mission['id'] / 'artifacts'
        if verify_artifacts(directory, 'live')['status'] != 'passed':
            continue
        hash_ = hashlib.sha256((directory / 'final_output.json').read_bytes()).hexdigest()
        with store.transaction() as db:
            reviewers = {r['id']: json.loads(r['body']) for r in db.execute('SELECT * FROM users')}
        for decision in reversed(mission['decisions']):
            if decision.get('manifest_hash') != mission['manifest']['manifest_hash'] or decision.get('evidence_hash') != hash_:
                continue
            if decision.get('verdict') != 'approved':
                break
            expiry = decision.get('expires_at')
            if expiry is not None and (not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time()):
                break
            reviewer = reviewers.get(decision.get('actor_id'), {})
            if reviewer.get('role') not in {'reviewer', 'admin'} or (mission['manifest'].get('independent_review') and decision['actor_id'] == mission['manifest']['owner']):
                break
            validated.add(profile)
            break
    return {'status': 'passed' if validated == {'jd_crew', 'environment_investigation'} else 'pending',
            'validated_profiles': sorted(validated), 'detail': 'Requires native JD and REA console runs with exact-hash named review; browser simulation cannot satisfy this check'}


def finalize_report(checks: dict, required: list[str]) -> dict:
    states = [checks.get(k, {}).get('status', 'pending') for k in required]
    status = 'passed' if states and all(s == 'passed' for s in states) else 'failed' if 'failed' in states else 'incomplete'
    return {'status': status, 'checks': checks, 'pending_checks': [k for k, s in zip(required, states) if s not in ('passed', 'failed')]}


# psutil.boot_time() is derived from uptime and drifts by fractions of a second
# between calls, so "newer boot" must clear a margin, not merely compare greater.
BOOT_TIME_TOLERANCE_SECONDS = 60.0


def check_recovery(previous: dict, *, boot_time: float, supervisor_create_time: float,
                   release_root: str, task_observation: dict) -> dict:
    baseline = previous.get('verified_boot_time')
    if (previous.get('release_root') != release_root or not baseline
            or boot_time <= baseline + BOOT_TIME_TOLERANCE_SECONDS):
        return {'status': 'pending', 'detail': 'Record a baseline for this release, reboot, sign in, then run -RecoveryCheck.'}
    return _result(task_observation.get('status') == 'passed' and supervisor_create_time > boot_time,
                   {'detail': 'Current scheduled task launch after a newer boot', 'task': task_observation})


def observe_startup_task(install: Path, supervisor) -> dict:
    path = install / 'state/startup-task.json'
    if not path.exists():
        return {'status': 'failed', 'detail': 'No registered startup task record'}
    record = read_json(path)
    env = dict(os.environ)
    env['ROBOPORT_TASK_NAME'] = record['task_name']
    code = """[Console]::OutputEncoding = [Text.Encoding]::UTF8;
$t = Get-ScheduledTask -TaskName $env:ROBOPORT_TASK_NAME -ErrorAction Stop;
$i = Get-ScheduledTaskInfo -TaskName $env:ROBOPORT_TASK_NAME -ErrorAction Stop;
[ordered]@{state=[string]$t.State; enabled=$t.Settings.Enabled; last_run_time=([DateTimeOffset]$i.LastRunTime).ToUnixTimeSeconds(); action_executable=$t.Actions[0].Execute; action_arguments=$t.Actions[0].Arguments} | ConvertTo-Json -Compress"""
    command = [str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'), '-NoProfile', '-Command', code]
    result = subprocess.run(command, env=env, capture_output=True, encoding='utf-8-sig', timeout=20, check=False)
    if result.returncode:
        return {'status': 'failed', 'detail': 'Scheduled task could not be observed'}
    observed = json.loads(result.stdout)
    match = observed.get('action_arguments') == record.get('action_arguments') and observed.get('action_executable', '').lower() == record.get('action_executable', '').lower()
    expected_script = str((install / 'operator/Start-Roboport.ps1').resolve()).lower()
    # Windows venv launchers may insert a redirector process between Python
    # and the task's PowerShell action; verify its owned ancestor chain.
    try:
        ancestors = supervisor.parents()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        ancestors = []
    parent_matches = False
    for proc in ancestors:
        try:
            args = [a.lower() for a in proc.cmdline()]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if expected_script in args and '-foreground' in args:
            parent_matches = True
            break
    launched = observed.get('last_run_time', 0)
    valid = match and observed.get('enabled') is True and observed.get('state') == 'Running' and launched >= psutil.boot_time() and 0 <= supervisor.create_time() - launched <= 30 and parent_matches
    return {'status': 'passed' if valid else 'failed', 'registered': match and observed.get('enabled') is True,
            'launch_parent_matches': parent_matches, **observed}


def run_measured(argv: list[str], *, cwd: Path, env: dict, timeout_seconds: float) -> tuple[dict, dict]:
    """Sample owned benchmark children; no electricity/host-price fabrication."""
    stop = threading.Event()
    metrics = {'peak_observed_child_rss_bytes': 0, 'peak_observed_child_cpu_seconds': 0.0,
               'sample_count': 0, 'electricity_cost_usd': None,
               'scope': 'sampled verifier child tree; excludes separately running Ollama and may miss short-lived children'}
    parent = psutil.Process()
    def sample():
        while not stop.wait(.05):
            rss, cpu = 0, 0.0
            for proc in parent.children(recursive=True):
                try:
                    rss += proc.memory_info().rss
                    times = proc.cpu_times()
                    cpu += times.user + times.system
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            metrics['peak_observed_child_rss_bytes'] = max(metrics['peak_observed_child_rss_bytes'], rss)
            metrics['peak_observed_child_cpu_seconds'] = max(metrics['peak_observed_child_cpu_seconds'], cpu)
            metrics['sample_count'] += 1
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        result = run_bounded(argv, cwd=cwd, env=env, timeout_seconds=timeout_seconds, max_output_bytes=4 * 1024 * 1024)
    finally:
        stop.set()
        thread.join(timeout=5)
    return result, metrics


def _get(url: str) -> dict:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def verify_target(install: Path, *, restart: bool = False, recovery: bool = False, rollback: bool = False) -> dict:
    manifest = read_json(install / 'state/deployment.json')
    config = manifest['config']
    release, data = Path(manifest['release_root']), Path(manifest['data_root'])
    report_path = data / 'deployment-report.json'
    previous = read_json(report_path) if report_path.exists() else {}
    checks = {name: {'status': 'pending'} for name in REQUIRED}
    bound = config.get('expected_machine') or ''
    checks['machine'] = _result(os.name == 'nt' and bool(bound) and socket.gethostname().upper() == bound.upper(), socket.gethostname())
    versions = {'python': sys.version.split()[0]}
    for name, argv in [('node', ['node', '--version']), ('ollama', ['ollama', '--version']), ('gpu_driver', ['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'])]:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        versions[name] = result.stdout.strip()
        if result.returncode:
            raise RuntimeError(f'{name} verification failed: {result.stderr[-1000:]}')
    # Installs from before hardware tiers have no recorded tier; any tier may match.
    tiers = load_tiers(release / 'deploy/windows/hardware-tiers.json')
    wanted = [config['hardware_tier']] if config.get('hardware_tier') else list(tiers)
    memory = psutil.virtual_memory().total
    fitting = [name for name in wanted if name in tiers and tier_fits(tiers[name], versions['gpu_driver'], memory, config['model'])]
    versions['hardware_tier'] = fitting[0] if fitting else None
    checks['versions'] = _result(bool(versions['node']) and bool(versions['ollama']) and bool(fitting), versions)
    tags = _get(config['ollama_url'].rstrip('/') + '/api/tags').get('models', [])
    matching = [m for m in tags if m.get('name') == config['model']]
    checks['model'] = _result(bool(matching) and bool(matching[0].get('digest')), matching)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    python = str(release / '.venv/Scripts/python.exe')
    env = dict(os.environ)
    env.update({'ROBOPORT_PROVIDER': 'ollama', 'OLLAMA_HOST': config['ollama_url'], 'OLLAMA_MODEL_DEFAULT': config['model'], 'OLLAMA_MODEL_REASONING': config['model']})
    batches, outcomes, measurements = [], [], []
    # Three matched investigation runs establish a baseline, not an improvement.
    for target, count, eval_file in [('jd_crew', 1, 'evals/deployment_jd.json'), ('environment_investigation', 3, 'evals/environment_investigation.json')]:
        label = f'verify-{stamp}-{target}'
        batch = data / 'benchmarks' / label
        batches.append(label)
        command = [python, str(release / 'scripts/benchmark.py'), '--target', target, '--eval-set', str(release / eval_file), '--live', '--grade', '--runs', str(count), '--label', label, '--out', str(batch), '--feed-log', str(data / 'feeds' / f'{label}.jsonl'), '--run-log', str(data / 'telemetry')]
        log_dir = data / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / f'{label}.log').open('w', encoding='utf-8') as log:
            result, resources = run_measured(command, cwd=release, env=env, timeout_seconds=650 * count)
            measurements.append({'target': target, **resources})
            log.write(result['stdout'] + result['stderr'])
            paths = list(batch.glob('eval_*/run_*'))
            outcomes.extend(verify_artifacts(p, 'live') for p in paths)
            if result['error'] or result['exit_code'] != 0 or len(paths) != count:
                outcomes.append({'status': 'failed', 'run_dir': str(batch), 'detail': 'benchmark failed, timed out, or run count differs'})
    checks['live_runs'] = _result(len(outcomes) == 4 and all(r['status'] == 'passed' for r in outcomes), outcomes)
    running = _get(config['ollama_url'].rstrip('/') + '/api/ps').get('models', [])
    gpu = [m for m in running if m.get('name') == config['model'] or m.get('model') == config['model']]
    checks['gpu_inference'] = _result(bool(gpu) and gpu[0].get('size_vram', 0) > 0, gpu)
    endpoints = []
    latest = next((read_json(Path(r['run_dir']) / 'provenance.json') for r in reversed(outcomes) if r.get('status') == 'passed'), {})
    for _ in range(20):
        endpoints = [_get(f"http://127.0.0.1:{config['ops_port']}/health"), _get(f"http://127.0.0.1:{config['feed_port']}/health")]
        if endpoints[0].get('active_log_path') == latest.get('ops_log_path') and endpoints[1].get('last_run_id') == latest.get('telemetry_run_id'):
            break
        time.sleep(.3)
    checks['telemetry_endpoints'] = _result(bool(latest) and all(item.get('source') == 'runtime' for item in endpoints) and endpoints[0].get('active_log_path') == latest.get('ops_log_path') and endpoints[1].get('last_run_id') == latest.get('telemetry_run_id'), endpoints)
    processes = read_json(install / 'state/processes.json')
    supervisor = _owned_process(processes['supervisor'])
    if not supervisor:
        raise RuntimeError('deployment supervisor is not running')
    task_file = install / 'state/startup-task.json'
    task_observation = observe_startup_task(install, supervisor) if task_file.exists() else {'status': 'failed', 'registered': False}
    task_present = task_observation.get('registered', False)
    if recovery:
        checks['reboot_recovery'] = check_recovery(previous, boot_time=psutil.boot_time(), supervisor_create_time=supervisor.create_time(), release_root=str(release), task_observation=task_observation)
    if restart:
        before = supervisor.create_time()
        stop_services(install)
        with (data / 'logs/supervisor.recovery.log').open('ab') as log:
            subprocess.Popen([python, str(release / 'scripts/windows_deploy.py'), 'serve', '--install', str(install)], cwd=release, env=env, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(20):
            time.sleep(.5)
            if (install / 'state/processes.json').exists():
                new = _owned_process(read_json(install / 'state/processes.json')['supervisor'])
                if new and new.create_time() > before:
                    try:
                        _get(f"http://127.0.0.1:{config['ops_port']}/health")
                        _get(f"http://127.0.0.1:{config['feed_port']}/api/state")
                        checks['restart'] = _result(all(verify_artifacts(Path(r['run_dir']), 'live')['status'] == 'passed' for r in outcomes if 'failed_checks' in r), 'Owned supervisor restarted; run evidence persists')
                        break
                    except requests.RequestException:
                        continue
        if checks['restart']['status'] == 'pending':
            checks['restart'] = _result(False, 'Restart did not become healthy')
    rollback_path = install / 'state/rolled-back-deployment.json'
    if rollback and rollback_path.exists():
        prior = read_json(install / 'state/previous-deployment.json')
        rolled_back = read_json(rollback_path)
        checks['rollback'] = _result(prior.get('release_root') == str(release) and rolled_back.get('release_root') != str(release), 'Restored previous release; live checks reran against it')
    if not manifest.get('baseline_runs') and checks['live_runs']['status'] == 'passed':
        manifest['baseline_runs'] = batches
    protected = set(manifest.get('baseline_runs', [])) | set(batches)
    checkpoint = install / 'state/previous-deployment.json'
    if checkpoint.exists():
        protected.update(read_json(checkpoint).get('protected_runs', []))
    manifest['protected_runs'] = sorted(protected)
    atomic_json(install / 'state/deployment.json', manifest)
    from windows_deploy import prune_runs
    prune_runs(data / 'benchmarks', protected=protected, max_runs=config['max_completed_runs'], max_bytes=config['max_artifact_bytes'])
    used = sum(p.stat().st_size for p in data.rglob('*') if p.is_file())
    checks['artifact_retention'] = _result(used <= config['max_artifact_bytes'], {'bytes': used, 'protected_batches': sorted(protected)})
    # Preserve successful recovery observations only when release identity matches.
    if previous.get('release_root') == str(release):
        for key in ('restart', 'reboot_recovery', 'rollback'):
            if checks[key]['status'] == 'pending' and previous.get('checks', {}).get(key, {}).get('status') == 'passed':
                checks[key] = previous['checks'][key]
    checks['launchpad_walkthrough'] = observe_launchpad_walkthrough(data)
    report = finalize_report(checks, REQUIRED)
    report.update({'created_at': datetime.now(UTC).isoformat(), 'release_root': str(release), 'verified_boot_time': previous.get('verified_boot_time', psutil.boot_time()) if previous.get('release_root') == str(release) else psutil.boot_time(),
                   'startup_task': task_present, 'local_resources': {'measurements': measurements, 'physical_memory_bytes': psutil.virtual_memory().total, 'api_spend_note': 'Local API charges are zero; electricity/host cost not measured.'}})
    atomic_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--restart-check', action='store_true')
    parser.add_argument('--recovery-check', action='store_true')
    parser.add_argument('--rollback-check', action='store_true')
    args = parser.parse_args()
    path = args.install / 'data/deployment-report.json'
    try:
        report = verify_target(args.install, restart=args.restart_check, recovery=args.recovery_check, rollback=args.rollback_check)
    except Exception as exc:  # noqa: BLE001 — persist a failed report on any target verification failure
        # Keep the release identity, boot baseline and recovery observations,
        # so a transient failure doesn't reset the reboot-recovery baseline.
        try:
            previous = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
        kept = {k: previous[k] for k in ('release_root', 'verified_boot_time', 'checks') if isinstance(previous, dict) and k in previous}
        report = {**kept, 'status': 'failed', 'error': str(exc), 'created_at': datetime.now(UTC).isoformat()}
        atomic_json(path, report)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 2 if report['status'] == 'incomplete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
