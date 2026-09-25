"""App-owned configuration for the Language Input settings app.

Stored at ``%APPDATA%\\LanguageInput\\config.json`` as UTF-8 (no BOM) with LF
newlines, written atomically.  Secrets (the remote API key) are stored as a
base64-encoded DPAPI blob using the current user's scope, never in plaintext.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .yaml_io import atomic_write_text, backup_file

__all__ = [
    "AppConfig",
    "config_dir",
    "config_path",
    "load_config",
    "saved_backend",
    "save_config",
    "encrypt_secret",
    "decrypt_secret",
]

_APP_DIR_NAME = "LanguageInput"
_CONFIG_NAME = "config.json"

_BACKENDS = ("local", "remote", "off")
_LANGUAGES = ("en", "ja", "es")

# CRYPTPROTECT_UI_FORBIDDEN - never show UI from a background/tray process.
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


@dataclass
class AppConfig:
    """Persisted application settings with safe defaults."""

    model_root_override: str | None = None
    api_key_dpapi: str | None = None
    backend: str = "local"  # one of: local | remote | off
    remote_url: str = ""
    remote_model: str = ""
    remote_language: str = ""
    language: str = "en"  # one of: en | ja | es
    start_minimized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        """Build a config, merging over defaults and ignoring unknown keys."""
        config = cls()
        if not isinstance(data, dict):
            return config
        known = {field.name for field in fields(cls)}
        for key, value in data.items():
            if key not in known:
                continue
            setattr(config, key, value)
        config.normalise()
        return config

    def normalise(self) -> None:
        """Clamp enumerated fields to their allowed values."""
        if self.backend not in _BACKENDS:
            self.backend = "local"
        if self.language not in _LANGUAGES:
            self.language = "en"
        if self.model_root_override is not None:
            self.model_root_override = str(self.model_root_override)


def config_dir() -> Path:
    """``%APPDATA%\\LanguageInput`` (falls back to a home-relative path)."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / _APP_DIR_NAME


def config_path() -> Path:
    """Full path to the app-owned ``config.json``."""
    return config_dir() / _CONFIG_NAME


def load_config() -> AppConfig:
    """Load config, merging over defaults.  Never raises."""
    path = config_path()
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, ValueError):
        return AppConfig()
    return AppConfig.from_dict(data)


def saved_backend() -> str | None:
    """Read the saved choice; match the engine's fail-closed malformed-file rule."""
    path = config_path()
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > 64 * 1024:
            return "off"
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "off"
    if not isinstance(data, dict) or data.get("backend") not in _BACKENDS:
        return "off"
    return data["backend"]


def save_config(config: AppConfig) -> Path:
    """Atomically persist ``config`` as UTF-8 (no BOM) JSON with LF newlines.

    An existing ``config.json`` is backed up first (``<name>.bak-<stamp>``) --
    but only when the serialized content actually changes, so re-saving an
    unchanged configuration does not accumulate backups (S1).
    """
    config.normalise()
    text = json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n"
    path = config_path()
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8-sig") == text:
                return path
        except OSError:
            pass
        backup_file(path)
    return atomic_write_text(path, text)


# --- DPAPI secret handling --------------------------------------------------

class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _crypt32():  # pragma: no cover - thin wrapper
    return ctypes.WinDLL("crypt32", use_last_error=True)


def encrypt_secret(plaintext: str) -> str | None:
    """Encrypt ``plaintext`` with user-scoped DPAPI; return base64 or None.

    Returns ``None`` on non-Windows (so the app still runs).  On Windows a
    failure raises :class:`RuntimeError` with a clear message.
    """
    if sys.platform != "win32":
        return None
    if plaintext is None:
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32 = _crypt32()

    data = plaintext.encode("utf-8")
    buffer = ctypes.create_string_buffer(data)
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()

    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise RuntimeError(
            "CryptProtectData failed (error %d)" % ctypes.get_last_error()
        )

    try:
        protected = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(protected).decode("ascii")


def decrypt_secret(blob_b64: str | None) -> str | None:
    """Decrypt a base64 DPAPI blob; return plaintext or None on any failure."""
    if not blob_b64 or sys.platform != "win32":
        return None

    try:
        protected = base64.b64decode(blob_b64)
    except (ValueError, TypeError):
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32 = _crypt32()

    buffer = ctypes.create_string_buffer(protected)
    blob_in = _DataBlob(
        len(protected), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    )
    blob_out = _DataBlob()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        return None

    try:
        clear = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
    try:
        return clear.decode("utf-8")
    except UnicodeDecodeError:
        return None
