"""Write the operator verification report for a Windows installation.

Verify-Roboport.ps1 runs this in the active release:

  python scripts/deployment_report.py --install <InstallRoot>
      [--restart-check] [--recovery-check] [--rollback-check]

Exit 0 when the live checks and the required recovery and rollback
observations have passed. Exit 2 when the live checks passed but those
observations are still pending. Exit 1 when a check failed. The report is
written to <data_root>/deployment-report.json whenever the manifest can be read.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib import error, request

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_deploy  # noqa: E402

_REQUIRED = ('recovery', 'rollback')


def _inside(parent: Path, child: Path) -> bool:
    parent_s = os.path.normcase(os.path.normpath(str(parent.resolve())))
    child_s = os.path.normcase(os.path.normpath(str(child.resolve())))
    try:
        return os.path.commonpath((parent_s, child_s)) == parent_s
    except ValueError:
        return False


def _json_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = windows_deploy.read_json(path)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _http_up(port: int, path: str) -> tuple[bool, str]:
    url = f'http://127.0.0.1:{port}{path}'
    try:
        with request.urlopen(url, timeout=2) as response:
            response.read(64)
            return True, f'HTTP {response.status}'
    except error.HTTPError as exc:
        return True, f'HTTP {exc.code}'
    except error.URLError as exc:
        return False, str(exc.reason)
    except (TimeoutError, OSError) as exc:
        return False, str(exc)


def _policy_ok(path: Path, machine: str) -> tuple[bool, str]:
    if not path.is_file():
        return False, 'policy file is missing'
    try:
        policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        return False, f'policy file is unreadable: {exc}'
    if not isinstance(policy, dict) or policy.get('schema_version') != 1:
        return False, 'unsupported launch pad policy'
    if policy.get('expected_machine') != machine:
        return False, 'policy is not bound to this installation'
    return True, 'policy matches the installation'


def _remember(previous: dict) -> dict:
    raw = previous.get('observations')
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in ('restart', 'recovery', 'rollback') if isinstance(raw.get(key), bool)}


def _report(install: Path, manifest: dict, data_root: Path, args: argparse.Namespace) -> tuple[dict, int]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({'name': name, 'ok': ok, 'detail': detail})

    manifest_ok = manifest.get('schema_version') == 1
    add('manifest', manifest_ok, 'deployment manifest is version 1' if manifest_ok else 'unsupported deployment manifest')

    release_value = manifest.get('release_root')
    release = Path(release_value) if isinstance(release_value, str) and release_value.strip() else None
    release_ok = release is not None and _inside(install, release)
    if release is None:
        release_detail = 'release_root is missing'
    elif release_ok:
        release_detail = 'release stays inside the installation'
    else:
        release_detail = 'release path escapes the installation'
    add('release_path', release_ok, release_detail)

    if release_ok and release is not None:
        python_ok = (release / '.venv' / 'Scripts' / 'python.exe').is_file()
        bridge_ok = (release / 'dashboard' / 'bridge.py').is_file()
        python_detail = 'deployment interpreter is present' if python_ok else 'deployment interpreter is missing'
        bridge_detail = 'ops console bridge is present' if bridge_ok else 'ops console bridge is missing'
    else:
        python_ok = bridge_ok = False
        python_detail = bridge_detail = 'skipped: release is not inside the installation'
    add('deployment_python', python_ok, python_detail)
    add('bridge', bridge_ok, bridge_detail)

    config = manifest.get('config') if isinstance(manifest.get('config'), dict) else {}
    try:
        windows_deploy.validate_config(config)
        config_ok, config_detail = True, 'configuration is valid'
    except ValueError as exc:
        config_ok, config_detail = False, str(exc)
    add('config', config_ok, config_detail)

    machine = config.get('expected_machine')
    machine_ok = isinstance(machine, str) and bool(machine.strip())
    add('machine', machine_ok, 'installation is bound to a machine' if machine_ok else 'installation is not bound to a machine')

    policy_ok, policy_detail = _policy_ok(data_root / 'launchpad' / 'policy.yaml', machine if machine_ok else '')
    add('policy', policy_ok, policy_detail)

    if config_ok:
        launch_ok, launch_detail = _http_up(config['ops_port'], '/')
        feed_ok, feed_detail = _http_up(config['feed_port'], '/health')
    else:
        launch_ok, feed_ok = False, False
        launch_detail = feed_detail = 'skipped: configuration is invalid'
    add('launch_pad', launch_ok, launch_detail)
    add('feed', feed_ok, feed_detail)
    surfaces = launch_ok and feed_ok

    previous = _json_file(data_root / 'deployment-report.json') or {}
    observations = _remember(previous)
    if args.restart_check:
        processes = _json_file(install / 'state' / 'processes.json')
        observations['restart'] = surfaces and isinstance(processes, dict) and isinstance(processes.get('supervisor'), dict)
    if args.recovery_check:
        task = _json_file(install / 'state' / 'startup-task.json')
        task_name = task.get('task_name') if isinstance(task, dict) else None
        observations['recovery'] = surfaces and isinstance(task_name, str) and bool(task_name.strip())
    if args.rollback_check:
        rolled = _json_file(install / 'state' / 'rolled-back-deployment.json')
        observations['rollback'] = surfaces and isinstance(rolled, dict) and isinstance(rolled.get('release_root'), str)

    pending = [name for name in _REQUIRED if observations.get(name) is not True]
    if not all(item['ok'] for item in checks):
        code, status = 1, 'failed'
    elif pending:
        code, status = 2, 'pending'
    else:
        code, status = 0, 'passed'
    report = {'schema_version': 1, 'status': status, 'checks': checks, 'observations': observations, 'pending': pending}
    return report, code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--restart-check', action='store_true')
    parser.add_argument('--recovery-check', action='store_true')
    parser.add_argument('--rollback-check', action='store_true')
    args = parser.parse_args(argv)
    try:
        install = args.install.expanduser().resolve()
    except OSError as exc:
        print(f'FAIL install: {exc}')
        return 1
    manifest = _json_file(install / 'state' / 'deployment.json')
    if manifest is None:
        print('FAIL manifest: deployment.json is missing or invalid')
        return 1
    data_root = manifest.get('data_root')
    if not isinstance(data_root, str) or not data_root.strip():
        print('FAIL manifest: data_root is missing')
        return 1
    report, code = _report(install, manifest, Path(data_root), args)
    try:
        windows_deploy.atomic_json(Path(data_root) / 'deployment-report.json', report)
    except OSError as exc:
        print(f'FAIL report: {exc}')
        return 1
    for check in report['checks']:
        if not check['ok']:
            print(f"FAIL {check['name']}: {check['detail']}")
    if code == 2:
        print('Pending observations: ' + ', '.join(report['pending']))
    elif code == 0:
        print('Live deployment checks passed, and recovery and rollback observations are recorded.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
