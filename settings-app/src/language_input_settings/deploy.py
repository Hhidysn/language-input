"""Running ``WeaselDeployer.exe /deploy`` safely.

Important semantics (design doc §5.6, ``Configurator.cpp:144``):

* **Exit code 0 does NOT mean the configuration was valid.**  The deployer
  deploys the Rime and Weasel configurations independently and ignores the
  return value of ``rime->deploy()``.  Callers must verify the generated
  configuration and/or runtime behaviour separately.
* A non-zero exit code means failure or "busy".  Exit code ``1`` is returned
  when the internal ``WeaselDeployerMutex`` is already held (another deployer
  is running).
* Compiling/deploying here is always bounded by a hard timeout; this module
  never blocks forever.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import winproc

__all__ = ["DeployResult", "run_deploy", "is_deployer_running"]

_MUTEX_NAME = "WeaselDeployerMutex"
_SYNCHRONIZE = 0x00100000


@dataclass
class DeployResult:
    """Outcome of a single deploy invocation.

    ``ran`` is True when the process was actually launched (even if it later
    timed out).  ``busy`` is True when exit code 1 indicates a concurrent
    deployer.
    """

    ran: bool
    exit_code: int | None
    timed_out: bool
    busy: bool
    stdout: str
    stderr: str
    note: str


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_deploy(deployer_exe: Path | str | None, timeout: float = 120.0) -> DeployResult:
    """Run ``<deployer_exe> /deploy`` with a hard timeout.

    Never raises for ordinary failures: missing executable, launch errors and
    timeouts are all reported through :class:`DeployResult`.

    Reminder: **exit code 0 does not prove the config was valid.**  Verify the
    deployed output separately (design doc §5.6 / V3).
    """
    if not deployer_exe:
        return DeployResult(
            ran=False,
            exit_code=None,
            timed_out=False,
            busy=False,
            stdout="",
            stderr="",
            note="deployer executable path could not be resolved",
        )

    exe = Path(deployer_exe)
    if not exe.is_file():
        return DeployResult(
            ran=False,
            exit_code=None,
            timed_out=False,
            busy=False,
            stdout="",
            stderr="",
            note=f"deployer executable not found: {exe}",
        )

    try:
        completed = subprocess.run(
            [str(exe), "/deploy"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(exe.parent),
            **winproc.no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        return DeployResult(
            ran=True,
            exit_code=None,
            timed_out=True,
            busy=False,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            note=(
                f"deploy timed out after {timeout:g}s and the process was "
                "killed; the configuration state is unknown"
            ),
        )
    except OSError as exc:
        return DeployResult(
            ran=False,
            exit_code=None,
            timed_out=False,
            busy=False,
            stdout="",
            stderr="",
            note=f"failed to launch deployer: {exc}",
        )

    busy = completed.returncode == 1
    if busy:
        note = "exit code 1: another deployer is already running (busy)"
    elif completed.returncode == 0:
        note = (
            "exit code 0. WARNING: this does NOT mean the config was valid; "
            "verify the deployed output separately."
        )
    else:
        note = f"deployer exited with code {completed.returncode}"

    return DeployResult(
        ran=True,
        exit_code=completed.returncode,
        timed_out=False,
        busy=busy,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        note=note,
    )


def is_deployer_running() -> bool:
    """Read-only probe for the ``WeaselDeployerMutex``.

    Opens the mutex with ``SYNCHRONIZE`` access; it never creates or holds it.
    Returns False on non-Windows or when the mutex does not exist.
    """
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:  # pragma: no cover - defensive
        return False

    try:
        open_mutex = kernel32.OpenMutexW
        open_mutex.restype = wintypes.HANDLE
        open_mutex.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        return False

    handle = open_mutex(_SYNCHRONIZE, False, _MUTEX_NAME)
    if not handle:
        return False
    close_handle(handle)
    return True
