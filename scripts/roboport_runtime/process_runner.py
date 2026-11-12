"""Bounded child execution with owned process-family cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path


class _WindowsProcess:
    """Create suspended, assign a kill-on-close Job Object, then resume."""
    def __init__(self, argv, cwd, env):
        import msvcrt

        import pywintypes
        import win32api
        import win32con
        import win32event
        import win32file
        import win32job
        import win32pipe
        import win32process

        self.api, self.event, self.jobapi, self.processapi = win32api, win32event, win32job, win32process
        # pywin32 requires a string name; isolate each owned job from others.
        self.job = win32job.CreateJobObject(None, "Roboport-" + uuid.uuid4().hex)
        info = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        info['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info['BasicLimitInformation']['ActiveProcessLimit'] = 8
        info['JobMemoryLimit'] = 1024 * 1024 * 1024
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, info)
        handles = []
        self.handle = None
        try:
            sa = pywintypes.SECURITY_ATTRIBUTES()
            sa.bInheritHandle = True
            out_r, out_w = win32pipe.CreatePipe(sa, 0)
            handles.extend([out_r, out_w])
            err_r, err_w = win32pipe.CreatePipe(sa, 0)
            handles.extend([err_r, err_w])
            for h in (out_r, err_r):
                win32api.SetHandleInformation(h, win32con.HANDLE_FLAG_INHERIT, 0)
            null = win32file.CreateFile('NUL', win32con.GENERIC_READ, win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, sa, win32con.OPEN_EXISTING, 0, None)
            handles.append(null)
            startup = win32process.STARTUPINFO()
            startup.dwFlags = win32con.STARTF_USESTDHANDLES
            startup.hStdInput, startup.hStdOutput, startup.hStdError = null, out_w, err_w
            proc, thread, self.pid, _ = win32process.CreateProcess(argv[0], subprocess.list2cmdline(argv), None, None, True,
                win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW, env, str(cwd), startup)
            self.handle = proc
            try:
                win32job.AssignProcessToJobObject(self.job, proc)
                win32process.ResumeThread(thread)
            except Exception:
                win32process.TerminateProcess(proc, 1)
                raise
            finally:
                thread.Close()
            for h in (out_w, err_w, null):
                h.Close()
                handles.remove(h)
            self.stdout = os.fdopen(msvcrt.open_osfhandle(out_r.Detach(), os.O_RDONLY | os.O_BINARY), 'rb', buffering=0)
            handles.remove(out_r)
            self.stderr = os.fdopen(msvcrt.open_osfhandle(err_r.Detach(), os.O_RDONLY | os.O_BINARY), 'rb', buffering=0)
            handles.remove(err_r)
        except Exception:
            self.close()
            raise
        finally:
            for h in handles:
                h.Close()

    def poll(self):
        if self.event.WaitForSingleObject(self.handle, 0) == 0:
            return self.processapi.GetExitCodeProcess(self.handle)
        return None

    def kill_family(self):
        self.jobapi.TerminateJobObject(self.job, 1)

    def close(self):
        if self.job:
            self.job.Close()
            self.job = None
        if self.handle:
            self.handle.Close()
            self.handle = None


def run_bounded(argv: list[str], *, cwd: Path, env: dict[str, str], timeout_seconds: float, max_output_bytes: int, cancel_event: threading.Event | None = None, on_start=None) -> dict:
    if timeout_seconds <= 0 or max_output_bytes <= 0:
        return {'exit_code': None, 'stdout': '', 'stderr': '', 'error': {'code': 'invalid_limits'}}
    try:
        process = _WindowsProcess(argv, cwd, env) if os.name == 'nt' else subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except Exception as exc:  # noqa: BLE001 — native Windows launch failures must be structured
        return {'exit_code': None, 'stdout': '', 'stderr': '', 'error': {'code': 'dependency_error', 'message': str(exc)}}
    if on_start:
        try:
            on_start(process.pid)
        except BaseException:
            if os.name == 'nt':
                process.kill_family()
                process.close()
            else:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            raise
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def read(stream, index, limit):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                remaining = limit - len(buffers[index])
                buffers[index].extend(chunk[:max(0, remaining)])
                if len(chunk) > remaining:
                    overflow.set()
        finally:
            stream.close()

    readers = [threading.Thread(target=read, args=(process.stdout, 0, max_output_bytes), daemon=True),
               threading.Thread(target=read, args=(process.stderr, 1, 65536), daemon=True)]
    for thread in readers:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    error = None
    code = None
    try:
        while True:
            code = process.poll()
            if cancel_event is not None and cancel_event.is_set():
                error = {'code': 'cancelled'}
                break
            if overflow.is_set():
                error = {'code': 'output_limit'}
                break
            if time.monotonic() >= deadline:
                error = {'code': 'timeout'}
                break
            if code is not None:
                break
            overflow.wait(min(0.02, max(0, deadline - time.monotonic())))
    finally:
        # Also clean up children after a successful parent exit.
        if os.name == 'nt':
            process.kill_family()
            process.close()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        for thread in readers:
            thread.join(timeout=5)
    if overflow.is_set():
        error = {'code': 'output_limit'}
    return {'exit_code': code, 'stdout': buffers[0].decode('utf-8', errors='replace'),
            'stderr': buffers[1].decode('utf-8', errors='replace'), 'error': error}
