"""WeaselServer process control + backend application (M2, design §5.2).

The frozen ``WeaselServer.exe`` reads ``LANGUAGE_INPUT_REMOTE_*`` exactly once
at process start, so applying a backend is a full stop -> (clear cache) ->
start-with-an-explicit-env -> verify-env transaction.  Writing
``HKCU\\Environment`` is useless for an already-running process and is never
done here.

``maintenance_guard`` holds ``WeaselDeployerExclusiveMutex`` for the duration
of a transaction so the TSF's auto-recovery cannot spawn a server while we are
between stop and start (design §5.2 / V11, ``WeaselTSF.cpp``).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import env_config, paths, winproc

__all__ = [
    "ServerError",
    "running_pids",
    "is_running",
    "stop",
    "start",
    "restart",
    "wait_for_model_host_exit",
    "maintenance_guard",
    "server_stopped",
    "read_process_env",
    "read_server_env",
    "apply_backend",
]

_SERVER_EXE = "WeaselServer.exe"
_HOST_EXE = "LanguageInputModelHost.exe"
_EXCLUSIVE_MUTEX = "WeaselDeployerExclusiveMutex"

# Process-env reading (PROCESS_BASIC_INFORMATION etc.) is 64-bit Windows only;
# the whole app targets Windows 10/11 x64 (design §0).
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_VM_READ = 0x0010


class ServerError(RuntimeError):
    """A server-control step could not be completed."""


# --- path helpers -----------------------------------------------------------

def _server_exe() -> Path | None:
    try:
        exe = paths.weasel_server_exe(paths.weasel_root())
    except Exception:  # pragma: no cover - defensive
        return None
    if exe and exe.is_file():
        return exe
    return None


def _require_server_exe() -> Path:
    exe = _server_exe()
    if exe is None:
        raise ServerError(
            "WeaselServer.exe could not be resolved (HKLM WeaselRoot / InstallDir)"
        )
    return exe


# --- process listing --------------------------------------------------------

def _pids(image: str) -> list[int]:
    """Return running PIDs for ``image`` (never raises, never spawns a child).

    Uses ``kernel32.EnumProcesses`` /
    ``QueryFullProcessImageNameW`` via :mod:`winproc`; the old ``tasklist``
    probe flashed a console window on every call from the windowed bundle.
    """
    return winproc.list_process_ids(image)


def running_pids() -> list[int]:
    """PIDs of running ``WeaselServer.exe`` processes."""
    return _pids(_SERVER_EXE)


def is_running() -> bool:
    """True when at least one ``WeaselServer.exe`` is running."""
    return bool(running_pids())


# --- stop / start -----------------------------------------------------------

def _wait_gone(image: str, timeout: float, poll: float = 0.2) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if not _pids(image):
            return time.monotonic() - started
        time.sleep(poll)
    return None


def _wait_up(image: str, timeout: float, poll: float = 0.2) -> tuple[float | None, list[int]]:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        found = _pids(image)
        if found:
            return time.monotonic() - started, found
        time.sleep(poll)
    return None, []


def stop(timeout: float = 20.0, *, poll: float = 0.2) -> bool:
    """Ask the server to quit (``/q``) and poll until the process exits.

    Returns ``True`` when no ``WeaselServer.exe`` remains (including the
    already-stopped case), ``False`` on timeout.
    """
    if not running_pids():
        return True
    exe = _server_exe()
    if exe is None:
        return False
    try:
        subprocess.Popen(
            [str(exe), "/q"],
            cwd=str(exe.parent),
            **winproc.no_window_kwargs(detached=True),
        )
    except OSError as exc:
        raise ServerError(f"failed to launch WeaselServer.exe /q: {exc}") from exc
    return _wait_gone(_SERVER_EXE, timeout, poll) is not None


def wait_for_model_host_exit(timeout: float = 30.0, *, poll: float = 0.5) -> bool:
    """Wait for ``LanguageInputModelHost.exe`` (a child) to exit.

    ``WeaselServer``'s ``Stop()`` only closes the kill-on-close job and does
    **not** wait for the child (design §7.3), so callers must wait explicitly.
    Returns ``True`` when the host is gone.
    """
    return _wait_gone(_HOST_EXE, timeout, poll) is not None


def start(
    env: dict[str, str],
    *,
    wait: bool = True,
    timeout: float = 15.0,
) -> dict:
    """Launch ``WeaselServer.exe`` (no args -> self-replace) with ``env``.

    The injected mapping is used **directly** as the child environment (it is
    not merged here).  Returns a small structured result.
    """
    exe = _require_server_exe()
    try:
        proc = subprocess.Popen(
            [str(exe)],
            env=dict(env),
            cwd=str(exe.parent),
            **winproc.no_window_kwargs(detached=True),
        )
    except OSError as exc:
        raise ServerError(f"failed to launch WeaselServer.exe: {exc}") from exc

    result = {
        "launched_pid": proc.pid,
        "waited_seconds": 0.0,
        "pids": [],
    }
    if wait:
        elapsed, found = _wait_up(_SERVER_EXE, timeout)
        result["waited_seconds"] = round(elapsed, 3) if elapsed is not None else None
        result["pids"] = found
    return result


def restart(env: dict[str, str], *, stop_timeout: float = 20.0) -> dict:
    """Stop the running server then start it with ``env``."""
    stopped = stop(timeout=stop_timeout)
    if not stopped:
        raise ServerError(
            f"WeaselServer.exe did not exit within {stop_timeout:g}s; refusing to start"
        )
    return start(env)


# --- exclusive mutex (V11) --------------------------------------------------

@contextmanager
def maintenance_guard(name: str = _EXCLUSIVE_MUTEX):
    """Hold ``WeaselDeployerExclusiveMutex`` for a maintenance transaction.

    The TSF re-runs ``start_service.bat`` after a failed reconnect *unless*
    this mutex already exists (``WeaselTSF.cpp:242-258``).  Holding it for the
    whole stop/write/install window suppresses that auto-recovery (design §5.2
    / V11).  On non-Windows this is a no-op that yields ``False``.

    On Windows a failure to acquire the mutex is **fatal**: running a
    transaction without TSF suppression would silently risk a stale server
    respawning mid-write, so this raises :class:`ServerError` instead of
    yielding ``False`` for the caller to ignore.
    """
    if sys.platform != "win32":
        yield False
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.restype = wt.HANDLE
    create_mutex.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wt.HANDLE]
    close_handle.restype = wt.BOOL

    ctypes.set_last_error(0)
    handle = create_mutex(None, False, name)
    if not handle:
        raise ServerError(
            f"failed to acquire {name} (CreateMutexW error "
            f"{ctypes.get_last_error()}); refusing to run a maintenance "
            "transaction without TSF auto-recovery suppression"
        )
    try:
        yield True
    finally:
        close_handle(handle)


@contextmanager
def server_stopped(
    *,
    stop_timeout: float = 20.0,
    start_timeout: float = 15.0,
    start_env: dict[str, str] | None = None,
    restart: bool = True,
    wait_for_host: bool = True,
):
    """Exclusive-mutex + stop ``WeaselServer.exe`` transaction (design §5.6).

    Holds ``WeaselDeployerExclusiveMutex``, records whether the server was
    running (and its environment), stops it and waits for the model host, then
    yields a mutable ``state`` dict::

        {
            "was_running": bool,
            "env": dict | None,      # captured before the stop
            "stopped": bool,
            "model_host_exited": bool,
            "started": bool,
            "pids": list[int],
            "start_error": str | None,
        }

    On exit the server is restarted (only when it had been running and
    ``restart`` is true) with ``start_env`` or, when that is ``None``, the
    environment captured before the stop.  A failed stop raises
    :class:`ServerError`; a failed restart is recorded in
    ``state["start_error"]`` rather than raised, so the caller can surface it.
    """
    with maintenance_guard():
        was_running = is_running()
        captured = read_server_env() if was_running else None
        state: dict = {
            "was_running": was_running,
            "env": captured,
            "stopped": False,
            "model_host_exited": False,
            "started": False,
            "pids": [],
            "start_error": None,
        }
        if was_running and not stop(timeout=stop_timeout):
            raise ServerError(
                f"WeaselServer.exe did not exit within {stop_timeout:g}s; "
                "refusing to proceed with a live server"
            )
        state["stopped"] = True
        if wait_for_host:
            state["model_host_exited"] = wait_for_model_host_exit(stop_timeout)
        try:
            yield state
        finally:
            if was_running and restart:
                env = (
                    start_env
                    if start_env is not None
                    else (captured if captured is not None else dict(os.environ))
                )
                try:
                    started = start(env, wait=True, timeout=start_timeout)
                    state["pids"] = started.get("pids", []) or running_pids()
                    state["started"] = bool(state["pids"])
                    if not state["started"]:
                        state["start_error"] = (
                            "WeaselServer.exe did not appear after restart"
                        )
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    state["start_error"] = f"{type(exc).__name__}: {exc}"


# --- reading a live process environment block -------------------------------

def read_process_env(pid: int) -> dict[str, str] | None:
    """Best-effort read of ``pid``'s environment block (64-bit Windows).

    Returns ``None`` when the block cannot be read (e.g. access denied).  This
    mirrors the M0 probe's verified implementation.
    """
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    except OSError:  # pragma: no cover - defensive
        return None

    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.ReadProcessMemory.argtypes = [
        wt.HANDLE,
        wt.LPCVOID,
        wt.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    ntdll.NtQueryInformationProcess.argtypes = [
        wt.HANDLE,
        ctypes.c_int,
        wt.LPVOID,
        wt.ULONG,
        ctypes.POINTER(wt.ULONG),
    ]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long

    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ, False, int(pid)
    )
    if not handle:
        return None
    try:
        class _PBI(ctypes.Structure):
            _fields_ = [
                ("ExitStatus", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_void_p),
                ("BasePriority", ctypes.c_void_p),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p),
            ]

        info = _PBI()
        if ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(info), ctypes.sizeof(info), None
        ) != 0:
            return None
        peb = info.PebBaseAddress
        if not peb:
            return None

        pointer = ctypes.c_void_p()
        read = ctypes.c_size_t(0)
        # PEB->ProcessParameters at 0x20 (x64)
        if not kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(peb + 0x20),
            ctypes.byref(pointer),
            ctypes.sizeof(pointer),
            ctypes.byref(read),
        ):
            return None
        params = pointer.value
        if not params:
            return None

        env_ptr = ctypes.c_void_p()
        # RTL_USER_PROCESS_PARAMETERS->Environment at 0x80 (x64)
        if not kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(params + 0x80),
            ctypes.byref(env_ptr),
            ctypes.sizeof(env_ptr),
            ctypes.byref(read),
        ):
            return None
        if not env_ptr.value:
            return None

        chunks: list[bytes] = []
        offset = 0
        for _ in range(64):  # <= 256 KiB
            buffer = ctypes.create_string_buffer(4096)
            if not kernel32.ReadProcessMemory(
                handle,
                ctypes.c_void_p(env_ptr.value + offset),
                buffer,
                len(buffer),
                ctypes.byref(read),
            ):
                break
            data = buffer.raw[: read.value]
            chunks.append(data)
            offset += read.value
            if b"\x00\x00\x00\x00" in b"".join(chunks):
                break
            if read.value < len(buffer):
                break
        if not chunks:
            return None

        raw = b"".join(chunks).split(b"\x00\x00\x00\x00", 1)[0]
        text = raw.decode("utf-16-le", errors="replace")
        result: dict[str, str] = {}
        for item in text.split("\x00"):
            if "=" in item:
                key, _, value = item.partition("=")
                result[key] = value
        return result
    except Exception:  # pragma: no cover - defensive
        return None
    finally:
        kernel32.CloseHandle(handle)


def read_server_env() -> dict[str, str] | None:
    """Environment block of a running ``WeaselServer.exe`` (or ``None``)."""
    for pid in running_pids():
        env = read_process_env(pid)
        if env is not None:
            return env
    return None


# --- cache ------------------------------------------------------------------

def _clear_ai_cache(user_dir: Path | None) -> tuple[bool, str | None]:
    target = paths.ai_cache_path(user_dir or paths.rime_user_dir())
    if target is None:
        return False, None
    try:
        if target.is_file():
            target.unlink()
            return True, str(target)
    except OSError:
        return False, str(target)
    return False, str(target)


def _remote_snapshot(env: dict[str, str]) -> dict[str, str | bool | None]:
    config = env_config.effective_remote_config(env)
    return {
        "backend": env_config.describe_backend(env).value,
        "url": config.get("url"),
        "model": config.get("model"),
    }


# --- apply_backend ----------------------------------------------------------

def apply_backend(
    backend,
    *,
    url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    language: str | None = None,
    base_env: dict[str, str] | None = None,
    user_dir: Path | None = None,
    stop_timeout: float = 20.0,
    start_timeout: float = 15.0,
    verify_timeout: float = 8.0,
) -> dict:
    """Apply a translation backend as a stop -> (clear cache) -> start tx.

    Steps (design §5.2):

    1. Snapshot the *currently running* server's backend/endpoint/model.
    2. Hold ``WeaselDeployerExclusiveMutex``.
    3. Stop the server and wait for the model host to exit.
    4. Clear ``ai_cache_v3.json`` when backend / endpoint / model changed
       (cache key ignores the remote model, so a changed endpoint MUST clear).
    5. Start the server with the explicitly built env.
    6. Read the child's process environment back and verify it arrived.

    Returns a structured dict; never raises for an ordinary failure (the
    ``errors`` list carries the reason and ``ok`` is ``False``).
    """
    selected = backend if isinstance(backend, env_config.Backend) else env_config.Backend(
        str(backend).strip().lower()
    )
    base = dict(base_env) if base_env is not None else dict(os.environ)
    new_env = env_config.build_server_env(
        base, selected, url=url, api_key=api_key, model=model, language=language
    )
    # Build ``expected`` from *every* remote variable, not only the ones present
    # in ``new_env``: a missing key is an expectation too ("must be absent").
    # For the default ``local`` backend ``build_server_env`` deletes all of
    # them, so the old "only present keys" dict was empty and the verification
    # trivially matched even a stale remote server (B1).
    expected = {name: new_env.get(name) for name in env_config.REMOTE_VARS}

    before_running = is_running()
    before_env = read_server_env() if before_running else None
    before = _remote_snapshot(before_env) if before_env is not None else None
    after = _remote_snapshot(new_env)

    result: dict = {
        "backend": selected.value,
        "url": new_env.get(env_config.REMOTE_URL),
        "model": new_env.get(env_config.REMOTE_MODEL),
        "language": new_env.get(env_config.REMOTE_LANGUAGE),
        "server_exe": str(_server_exe()) if _server_exe() else None,
        "server_was_running": before_running,
        "before": before,
        "expected": expected,
        "stopped": False,
        "model_host_exited": False,
        "cache_cleared": False,
        "cache_path": None,
        "started": False,
        "pids": [],
        "observed": None,
        "env_verified": False,
        "ok": False,
        "errors": [],
    }

    try:
        with maintenance_guard():
            if before_running and not stop(timeout=stop_timeout):
                result["errors"].append(
                    f"WeaselServer.exe did not exit within {stop_timeout:g}s"
                )
                return result
            result["stopped"] = True
            result["model_host_exited"] = wait_for_model_host_exit(stop_timeout)

            # Cache key ignores the remote model (design §5.2 / R20), so any
            # backend / endpoint / model change requires an explicit clear.
            changed = (
                before is None
                or before.get("backend") != after.get("backend")
                or before.get("url") != after.get("url")
                or before.get("model") != after.get("model")
            )
            if changed:
                cleared, cache_path = _clear_ai_cache(user_dir)
                result["cache_cleared"] = cleared
                result["cache_path"] = cache_path
            else:
                result["cache_path"] = str(
                    paths.ai_cache_path(user_dir or paths.rime_user_dir())
                )

            started = start(new_env, wait=True, timeout=start_timeout)
            result["pids"] = started.get("pids", []) or running_pids()
            result["started"] = bool(result["pids"])
            if not result["started"]:
                result["errors"].append(
                    "WeaselServer.exe did not appear after start (no PID found); "
                    "cannot verify the injected environment"
                )

            observed, verified = _verify_env(expected, timeout=verify_timeout)
            result["observed"] = observed
            result["env_verified"] = verified
            if not verified:
                result["errors"].append(
                    "the running server's process environment does not match the "
                    "requested backend"
                )
    except ServerError as exc:
        result["errors"].append(str(exc))
        return result
    except Exception as exc:  # noqa: BLE001 - boundary
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        return result

    result["ok"] = bool(
        not result["errors"] and result["env_verified"] and result["started"]
    )
    return result


def _expected_matches(observed: dict[str, str], expected: dict[str, str | None]) -> bool:
    """True when every expected ``LANGUAGE_INPUT_REMOTE_*`` value is exact."""
    for name, value in expected.items():
        if value is None:
            # Key must be absent in the child.
            if name in observed:
                return False
        elif observed.get(name) != value:
            return False
    return True


def _verify_env(
    expected: dict[str, str | None], *, timeout: float
) -> tuple[dict[str, str], bool]:
    """Poll the running server's env until it matches ``expected``.

    ``expected`` carries ``None`` for every remote variable that must be
    *absent* (the ``local`` backend).  A read failure (``read_server_env()``
    returns ``None`` -- no server, or the env block is unreadable) is **not** a
    match: an unreadable environment can never prove the injected env arrived.
    """
    deadline = time.monotonic() + timeout
    observed: dict[str, str] = {}
    remote_upper = {name.upper() for name in env_config.REMOTE_VARS}
    while True:
        env = read_server_env()
        if env is not None:
            observed = {
                key: value
                for key, value in env.items()
                if key.upper() in remote_upper
            }
            if _expected_matches(observed, expected):
                return observed, True
        if time.monotonic() >= deadline:
            return observed, False
        time.sleep(0.25)
