"""Path resolution for the Language Input settings app.

All functions in this module are pure / read-only:

* They never create, modify or delete anything.
* They never raise on missing registry keys, missing environment variables
  or missing files; they return ``None`` instead.

Registry semantics mirror the frozen C++ side of the project:

* ``HKCU\\Software\\Rime\\Weasel`` ``RimeUserDir`` (``REG_SZ``) selects the
  Rime user directory.  When it is absent or empty we fall back to
  ``%APPDATA%\\Rime``.  The C++ side (``WeaselUtility.cpp``) does **not**
  expand environment variables stored in that value, so neither do we.
* ``HKLM\\Software\\Rime\\Weasel`` ``WeaselRoot`` / ``InstallDir`` locates
  the Weasel installation.  Both the 64-bit and the WOW6432Node views are
  probed defensively.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PathResolution",
    "rime_user_dir",
    "weasel_root",
    "weasel_deployer_exe",
    "weasel_server_exe",
    "model_host_exe",
    "find_model_host_exe",
    "model_root",
    "ai_cache_path",
    "packs_catalog",
    "m2m100_catalog",
    "resolve_paths",
]

# --- Registry constants -----------------------------------------------------

_HKCU = 0x80000001  # HKEY_CURRENT_USER
_HKLM = 0x80000002  # HKEY_LOCAL_MACHINE

_RIME_WEASEL_KEY = r"Software\Rime\Weasel"

_KEY_WOW64_64KEY = 0x0100
_KEY_WOW64_32KEY = 0x0200

_EXE_DEPLOYER = "WeaselDeployer.exe"
_EXE_SERVER = "WeaselServer.exe"
_EXE_MODEL_HOST = "LanguageInputModelHost.exe"

_DEFAULT_USER_DIR_NAME = "Rime"


def _winreg():
    """Import winreg lazily so the module also imports on non-Windows."""
    try:
        import winreg  # type: ignore

        return winreg
    except Exception:  # pragma: no cover - non-Windows
        return None


def _read_reg_value(hive: int, subkey: str, value_name: str, view: int) -> str | None:
    """Read a single registry string value; return None on any failure."""
    winreg = _winreg()
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
            data, _kind = winreg.QueryValueEx(key, value_name)
    except OSError:
        return None
    except Exception:  # pragma: no cover - defensive
        return None
    if data is None:
        return None
    return str(data)


def _read_reg_first(hive: int, subkey: str, value_names: tuple[str, ...]) -> str | None:
    """Return the first non-empty value across the given names and views."""
    for value_name in value_names:
        for view in (_KEY_WOW64_64KEY, _KEY_WOW64_32KEY):
            value = _read_reg_value(hive, subkey, value_name, view)
            if value:
                return value
    return None


def rime_user_dir() -> Path:
    """Resolve the Rime user directory.

    Reads ``HKCU\\Software\\Rime\\Weasel`` ``RimeUserDir``.  The stored value
    is used verbatim (no environment-variable expansion), matching the frozen
    C++ behaviour.  If it is absent or empty we fall back to
    ``%APPDATA%\\Rime``, and finally to ``~/AppData/Roaming/Rime``.
    """
    stored = _read_reg_first(_HKCU, _RIME_WEASEL_KEY, ("RimeUserDir",))
    if stored:
        return Path(stored)

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / _DEFAULT_USER_DIR_NAME
    return Path.home() / "AppData" / "Roaming" / _DEFAULT_USER_DIR_NAME


def weasel_root() -> Path | None:
    """Resolve the Weasel installation root, or ``None`` if unavailable.

    Reads ``HKLM\\Software\\Rime\\Weasel`` ``WeaselRoot`` then ``InstallDir``
    from both the 64-bit and the WOW6432Node registry views.
    """
    stored = _read_reg_first(_HKLM, _RIME_WEASEL_KEY, ("WeaselRoot", "InstallDir"))
    if stored:
        return Path(stored)
    return None


def weasel_deployer_exe(root: Path | None) -> Path | None:
    """``<root>\\WeaselDeployer.exe`` or ``None`` when root is unknown."""
    if root is None:
        return None
    return Path(root) / _EXE_DEPLOYER


def weasel_server_exe(root: Path | None) -> Path | None:
    """``<root>\\WeaselServer.exe`` or ``None`` when root is unknown."""
    if root is None:
        return None
    return Path(root) / _EXE_SERVER


def model_host_exe(root: Path | None) -> Path | None:
    """Canonical model-host path: ``<root>\\LanguageInputModelHost.exe``.

    Per the design doc the host ships next to ``WeaselServer.exe``.  Use
    :func:`find_model_host_exe` when a development/build layout also has to be
    probed.
    """
    if root is None:
        return None
    return Path(root) / _EXE_MODEL_HOST


def find_model_host_exe(root: Path | None) -> Path | None:
    """Return an existing model-host executable, or ``None``.

    Probes the canonical location first, then the ``model-host`` build
    sub-directory used by the repository's ``output\\`` layout.
    """
    if root is None:
        return None
    root = Path(root)
    candidates = (root / _EXE_MODEL_HOST, root / "model-host" / _EXE_MODEL_HOST)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # pragma: no cover - defensive
            continue
    return None


def model_root(user_dir: Path | None) -> Path | None:
    """``<user_dir>\\language_input\\models`` or ``None``."""
    if user_dir is None:
        return None
    return Path(user_dir) / "language_input" / "models"


def ai_cache_path(user_dir: Path | None) -> Path | None:
    """``<user_dir>\\language_input\\ai_cache_v3.json`` or ``None``."""
    if user_dir is None:
        return None
    return Path(user_dir) / "language_input" / "ai_cache_v3.json"


def packs_catalog(root: Path | None) -> Path | None:
    """``<root>\\data\\language_input\\models\\packs-v2.json`` or ``None``."""
    if root is None:
        return None
    return Path(root) / "data" / "language_input" / "models" / "packs-v2.json"


def m2m100_catalog(root: Path | None) -> Path | None:
    """``<root>\\data\\language_input\\models\\m2m100-packs-v1.json`` or ``None``."""
    if root is None:
        return None
    return Path(root) / "data" / "language_input" / "models" / "m2m100-packs-v1.json"


def _is_dir(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_dir()
    except OSError:  # pragma: no cover - defensive
        return False


def _is_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_file()
    except OSError:  # pragma: no cover - defensive
        return False


@dataclass(frozen=True)
class PathResolution:
    """Read-only snapshot of every path this application is interested in.

    ``None`` means "could not be resolved", never an error.  The ``*_exists``
    fields are point-in-time existence probes.
    """

    rime_user_dir: Path
    rime_user_dir_exists: bool
    weasel_root: Path | None
    weasel_root_exists: bool
    deployer_exe: Path | None
    deployer_exists: bool
    server_exe: Path | None
    server_exists: bool
    model_host_exe: Path | None
    model_host_exists: bool
    model_root: Path | None
    model_root_exists: bool
    ai_cache: Path | None
    packs_catalog: Path | None
    packs_catalog_exists: bool
    m2m100_catalog: Path | None
    m2m100_catalog_exists: bool


def resolve_paths() -> PathResolution:
    """Collect a full :class:`PathResolution` snapshot (never raises)."""
    user_dir = rime_user_dir()
    root = weasel_root()
    host = find_model_host_exe(root) or model_host_exe(root)
    mroot = model_root(user_dir)
    packs = packs_catalog(root)
    m2m = m2m100_catalog(root)
    return PathResolution(
        rime_user_dir=user_dir,
        rime_user_dir_exists=_is_dir(user_dir),
        weasel_root=root,
        weasel_root_exists=_is_dir(root),
        deployer_exe=weasel_deployer_exe(root),
        deployer_exists=_is_file(weasel_deployer_exe(root)),
        server_exe=weasel_server_exe(root),
        server_exists=_is_file(weasel_server_exe(root)),
        model_host_exe=host,
        model_host_exists=_is_file(host),
        model_root=mroot,
        model_root_exists=_is_dir(mroot),
        ai_cache=ai_cache_path(user_dir),
        packs_catalog=packs,
        packs_catalog_exists=_is_file(packs),
        m2m100_catalog=m2m,
        m2m100_catalog_exists=_is_file(m2m),
    )
