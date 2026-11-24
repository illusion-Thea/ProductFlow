"""Own the actual Python process behind the Windows venv launcher."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from productflow_backend.infrastructure.verification import IsolationError, inside_run, manifest


def process_identity(pid: int) -> int:
    if os.name != "nt":
        return int(Path(f"/proc/{pid}/stat").read_text().split()[21])
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            raise ProcessLookupError(pid)
        raise IsolationError("cannot_verify_owned_process")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            raise IsolationError("cannot_verify_process_creation_time")
        return values[0].dwHighDateTime << 32 | values[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def register_child() -> None:
    token = os.environ.get("M101_PROCESS_TOKEN")
    if not token:
        return
    UUID(token)
    data = manifest()
    path = inside_run(Path(data["run_root"]) / "evidence" / f"child-{token}.json")
    temporary = path.with_suffix(".part")
    temporary.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "creation_time": process_identity(os.getpid()),
                "parent_pid": os.getppid(),
                "environment_id": data["environment_id"],
            }
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def stop_owned(pid: int, creation_time: int) -> None:
    try:
        if process_identity(pid) != creation_time:
            raise IsolationError("pid_reused_refusing_to_stop")
    except ProcessLookupError:
        return
    os.kill(pid, signal.SIGTERM)


@dataclass
class OwnedPython:
    launcher: subprocess.Popen
    pid: int
    creation_time: int

    def poll(self):
        return self.launcher.poll()

    def terminate(self):
        stop_owned(self.pid, self.creation_time)

    def wait(self, timeout=5):
        return self.launcher.wait(timeout=timeout)


def start_python(command: list[str], env: dict, *, stdout, cwd=None) -> OwnedPython:
    token = str(uuid4())
    values = {**env, "M101_PROCESS_TOKEN": token}
    data = json.loads(Path(env["M101_MANIFEST"]).read_text(encoding="utf-8"))
    path = inside_run(Path(data["run_root"]) / "evidence" / f"child-{token}.json", data)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=values,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stdout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 10
    while not path.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            if process.poll() is None:
                process.terminate()
            raise IsolationError("verification_python_handshake_failed")
        time.sleep(0.025)
    identity = json.loads(path.read_text())
    if identity["environment_id"] != data["environment_id"] or identity["creation_time"] != process_identity(
        identity["pid"]
    ):
        raise IsolationError("verification_python_identity_mismatch")
    return OwnedPython(process, identity["pid"], identity["creation_time"])
