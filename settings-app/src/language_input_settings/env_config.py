"""Translation-backend selection via the ``LANGUAGE_INPUT_REMOTE_*`` env.

The frozen ``WeaselServer.exe`` reads these variables **once, at process
start** (design doc §5.2, ``LanguageInputRemote.cpp`` / ``RimeWithWeasel.cpp``).
The semantics verified by the M0 probe are:

* ``LANGUAGE_INPUT_REMOTE_ENABLED`` **absent** -> LOCAL (the bundled model host).
* present with a truthy value (``1`` / ``true`` / ``yes`` / ``on``) -> REMOTE
  (also needs ``..._URL`` + ``..._API_KEY`` and a usable ``..._LANGUAGE``).
* present with any other value (``0`` / ``false`` / ...) -> AI transport
  **disabled** (glossing entirely off) -- *not* local.

Therefore "select local" means **removing** every ``LANGUAGE_INPUT_REMOTE_*``
variable; a falsy value does *not* select local.

Windows environment blocks are case-insensitive and the server matches the
canonical upper-case spelling, so helpers here remove any case variant and
always write the canonical spelling.
"""

from __future__ import annotations

import os
from enum import Enum

__all__ = [
    "Backend",
    "REMOTE_ENABLED",
    "REMOTE_URL",
    "REMOTE_API_KEY",
    "REMOTE_MODEL",
    "REMOTE_LANGUAGE",
    "REMOTE_VARS",
    "TRUTHY_VALUES",
    "build_server_env",
    "describe_backend",
    "current_process_backend",
    "effective_remote_config",
]

REMOTE_ENABLED = "LANGUAGE_INPUT_REMOTE_ENABLED"
REMOTE_URL = "LANGUAGE_INPUT_REMOTE_URL"
REMOTE_API_KEY = "LANGUAGE_INPUT_REMOTE_API_KEY"
REMOTE_MODEL = "LANGUAGE_INPUT_REMOTE_MODEL"
REMOTE_LANGUAGE = "LANGUAGE_INPUT_REMOTE_LANGUAGE"

#: Every variable the frozen server consults for the translation backend.
REMOTE_VARS: tuple[str, ...] = (
    REMOTE_ENABLED,
    REMOTE_URL,
    REMOTE_API_KEY,
    REMOTE_MODEL,
    REMOTE_LANGUAGE,
)

#: The only spellings the frozen C++ treats as "true" (``IsTruthy``).
TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


class Backend(str, Enum):
    """The three effective translation-backend states."""

    local = "local"
    remote = "remote"
    off = "off"


def _as_backend(value: Backend | str) -> Backend:
    if isinstance(value, Backend):
        return value
    return Backend(str(value).strip().lower())


def _find_key(env: dict[str, str], name: str) -> str | None:
    """Return the actual key spelling for ``name`` (case-insensitive)."""
    if name in env:
        return name
    lowered = name.lower()
    for key in env:
        if key.lower() == lowered:
            return key
    return None


def _delete(env: dict[str, str], name: str) -> None:
    for _ in range(2):  # a single block should not contain two spellings
        key = _find_key(env, name)
        if key is None:
            return
        del env[key]


def _set(env: dict[str, str], name: str, value: str) -> None:
    _delete(env, name)
    env[name] = value


def build_server_env(
    base_env: dict[str, str],
    backend: Backend | str,
    *,
    url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    language: str | None = None,
) -> dict[str, str]:
    """Return a copy of ``base_env`` with the backend env applied.

    ``local`` deletes every ``LANGUAGE_INPUT_REMOTE_*`` variable; ``off`` sets
    ``..._ENABLED=0`` (and clears the rest); ``remote`` sets ``..._ENABLED=1``
    plus whichever of URL / API key / model / language were supplied.

    The returned mapping is a fresh ``dict``; ``base_env`` is never mutated.
    """
    env: dict[str, str] = dict(base_env)
    for name in REMOTE_VARS:
        _delete(env, name)

    selected = _as_backend(backend)
    if selected is Backend.local:
        return env

    if selected is Backend.off:
        _set(env, REMOTE_ENABLED, "0")
        return env

    _set(env, REMOTE_ENABLED, "1")
    for name, value in (
        (REMOTE_URL, url),
        (REMOTE_API_KEY, api_key),
        (REMOTE_MODEL, model),
        (REMOTE_LANGUAGE, language),
    ):
        if value:
            _set(env, name, str(value))
    return env


def describe_backend(env: dict[str, str]) -> Backend:
    """Classify an environment block into :class:`Backend` (design §5.2)."""
    enabled_key = _find_key(env, REMOTE_ENABLED)
    if enabled_key is None:
        return Backend.local
    value = str(env.get(enabled_key, "")).strip().lower()
    if value in TRUTHY_VALUES:
        return Backend.remote
    return Backend.off


def current_process_backend() -> Backend:
    """Backend as seen by *this* process (for UI display only).

    The server is the process that actually matters; use
    ``server.read_server_env()`` / ``server.apply_backend`` to inspect or set
    the live server.  This helper exists so the GUI can show something sensible
    even when the server is not running.
    """
    return describe_backend(dict(os.environ))


def effective_remote_config(env: dict[str, str]) -> dict[str, str | bool | None]:
    """Extract ``{enabled, url, model, language, has_api_key}`` from ``env``."""
    def get(name: str) -> str | None:
        key = _find_key(env, name)
        return env.get(key) if key is not None else None

    api_key = get(REMOTE_API_KEY)
    return {
        "enabled": get(REMOTE_ENABLED),
        "url": get(REMOTE_URL),
        "model": get(REMOTE_MODEL),
        "language": get(REMOTE_LANGUAGE),
        "has_api_key": bool(api_key),
    }
