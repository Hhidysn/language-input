"""Plain-gloss ("hide the language/word-type badge") support.

The candidate window prepends a badge to every gloss.  The badge is built in
the shipped Lua filter ``language_input/gloss_filter.lua`` by the single line::

    env.marker = "〔" .. env.language .. "·词〕 "

There is **no** Rime configuration key that suppresses just this badge, so the
only non-invasive way to hide it is to shadow the shipped Lua module with a
copy whose ``env.marker`` assignment is empty.

Why a *user-dir* copy shadows the installed one
-----------------------------------------------
``librime-lua`` builds ``package.path`` as ``<user>/lua/?.lua`` **first** and
``<install>/data/lua/?.lua`` second (``librime/plugins/lua/src/modules.cc``),
and the schema loads the filter via
``lua_filter@*language_input.gloss_filter`` → ``require("language_input.gloss_filter")``.
A copy at ``<user_dir>\\lua\\language_input\\gloss_filter.lua`` therefore wins
without admin rights and survives a reinstall.  Nothing under the installation
directory (``C:\\Program Files``) is ever touched by this module.

Caveat
------
The shadow copy is a **full copy** of the shipped filter.  If a future product
version changes ``gloss_filter.lua``, the stale shadow copy would silently
shadow the new version until the user reverts.  Keep :func:`revert_plain_gloss`
reachable from the UI/CLI.

Lua modules are ``require``-cached per process, so a deploy
(``WeaselDeployer.exe /deploy``) and/or a ``WeaselServer`` restart is required
before a new copy takes effect.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from . import deploy as _deploy
from . import paths, yaml_io

__all__ = [
    "GlossBadgeError",
    "PLAIN_MARKER",
    "SHADOW_PATH",
    "installed_filter_path",
    "marker_line_replacement",
    "marker_is_empty",
    "sha256_file",
    "apply_plain_gloss",
    "revert_plain_gloss",
    "plain_gloss_state",
]

# Relative location of the shipped filter below the Weasel install root.
_INSTALLED_RELATIVE = Path("data") / "lua" / "language_input" / "gloss_filter.lua"

#: The exact replacement assignment written into the shadow copy.
PLAIN_MARKER = 'env.marker = ""'

# Matches exactly one ``env.marker = <value>`` assignment line, tolerating
# leading/trailing whitespace.  ``env.marker`` may not be followed by another
# word character (so ``env.marker_foo`` is not matched).  ``_read_text`` reads
# in text mode, which already normalises ``\r\n`` to ``\n`` (and the project
# standardises on LF), so no ``\r`` handling is needed here (N3).
_MARKER_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)env\.marker[ \t]*=.*$",
    re.MULTILINE,
)

# Recognises the already-plain assignment (idempotency / state probing).
_MARKER_EMPTY_RE = re.compile(
    r'^[ \t]*env\.marker[ \t]*=[ \t]*""[ \t]*$',
    re.MULTILINE,
)


class GlossBadgeError(RuntimeError):
    """The shadow copy could not be produced or the source was not recognised."""


def SHADOW_PATH(user_dir: str | Path) -> Path:
    """Path of the user-dir shadow copy of the gloss filter."""
    return Path(user_dir) / "lua" / "language_input" / "gloss_filter.lua"


def installed_filter_path() -> Path | None:
    """Path of the shipped filter, or ``None`` when the install is unresolved."""
    root = paths.weasel_root()
    if root is None:
        return None
    return Path(root) / _INSTALLED_RELATIVE


def sha256_file(path: str | Path) -> str | None:
    """SHA256 hex digest of ``path``, or ``None`` when it cannot be read."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()
    except OSError:
        return None


def marker_is_empty(text: str) -> bool:
    """True when ``text`` contains an ``env.marker = ""`` assignment."""
    return _MARKER_EMPTY_RE.search(text) is not None


def marker_line_replacement(text: str) -> str:
    """Return ``text`` with the ``env.marker = ...`` line replaced by ``""``.

    Every other byte is preserved.  Raises :class:`GlossBadgeError` unless the
    assignment is found **exactly once**, so a future upstream change to the
    filter fails loudly instead of silently producing a wrong shadow copy.
    """
    matches = list(_MARKER_LINE_RE.finditer(text))
    if len(matches) != 1:
        raise GlossBadgeError(
            "expected exactly one 'env.marker = ...' assignment in the gloss "
            f"filter, found {len(matches)}; refusing to write a shadow copy "
            "(the shipped filter may have changed)"
        )
    match = matches[0]
    replacement = f"{match.group('indent')}{PLAIN_MARKER}"
    return text[: match.start()] + replacement + text[match.end() :]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise GlossBadgeError(f"cannot read gloss filter {path}: {exc}") from exc


def _write_shadow(shadow: Path, text: str) -> dict[str, Any]:
    """Atomically write the shadow copy (UTF-8 no BOM, LF) and verify it.

    An existing shadow copy is backed up first (unless the content is already
    identical), matching :func:`revert_plain_gloss` and the project-wide
    backup-before-overwrite rule (S2).
    """
    backup: Path | None = None
    if shadow.is_file():
        try:
            existing = shadow.read_text(encoding="utf-8-sig")
        except OSError:
            existing = None
        if existing != text:
            backup = yaml_io.backup_file(shadow)
    yaml_io.atomic_write_text(shadow, text)
    raw = shadow.read_bytes()
    return {
        "shadow_path": str(shadow),
        "backup_path": str(backup) if backup else None,
        "bytes_written": len(raw),
        "no_bom": not raw.startswith(b"\xef\xbb\xbf"),
        "lf_only": b"\r" not in raw,
        "sha256": hashlib.sha256(raw).hexdigest().upper(),
    }


def _run_deploy() -> dict[str, Any]:
    deployer = paths.weasel_deployer_exe(paths.weasel_root())
    result = _deploy.run_deploy(deployer, timeout=120)
    return {
        "ran": result.ran,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "busy": result.busy,
        "stderr": result.stderr,
        "note": result.note,
        # /deploy returns 0 even for bad YAML: judge success by an empty stderr.
        "clean": bool(result.ran)
        and result.exit_code == 0
        and not result.stderr.strip(),
    }


def apply_plain_gloss(*, deploy: bool = True) -> dict[str, Any]:
    """Install the badge-free shadow copy and redeploy.

    Reads the **installed** filter (falling back to an existing shadow copy),
    replaces only the ``env.marker`` assignment, writes the result to
    ``<user_dir>\\lua\\language_input\\gloss_filter.lua`` (UTF-8 no BOM, LF,
    atomic) and then runs ``/deploy``.
    """
    user_dir = paths.rime_user_dir()
    shadow = SHADOW_PATH(user_dir)
    installed = installed_filter_path()

    source: Path | None = None
    if installed is not None and installed.is_file():
        source = installed
    elif shadow.is_file():
        source = shadow

    if source is None:
        raise GlossBadgeError(
            "cannot locate the gloss filter: neither the installed copy "
            f"({installed}) nor an existing shadow copy ({shadow}) exists"
        )

    original = _read_text(source)
    modified = marker_line_replacement(original)
    written = _write_shadow(shadow, modified)

    result: dict[str, Any] = {
        "action": "plain",
        "user_dir": str(user_dir),
        "source_path": str(source),
        "installed_path": str(installed) if installed is not None else None,
        "installed_sha256": sha256_file(installed) if installed else None,
        "marker_is_empty": marker_is_empty(modified),
        "deploy": None,
        "ok": False,
    }
    result.update(written)

    if deploy:
        result["deploy"] = _run_deploy()
        result["ok"] = bool(
            result["marker_is_empty"]
            and result["no_bom"]
            and result["lf_only"]
            and result["deploy"]["clean"]
        )
    else:
        result["ok"] = bool(
            result["marker_is_empty"] and result["no_bom"] and result["lf_only"]
        )
    return result


def revert_plain_gloss(*, deploy: bool = True) -> dict[str, Any]:
    """Remove the shadow copy (backing it up first) and redeploy."""
    user_dir = paths.rime_user_dir()
    shadow = SHADOW_PATH(user_dir)

    result: dict[str, Any] = {
        "action": "fancy",
        "user_dir": str(user_dir),
        "shadow_path": str(shadow),
        "existed": shadow.is_file(),
        "backup_path": None,
        "removed": False,
        "shadow_exists": shadow.is_file(),
        "deploy": None,
        "ok": False,
    }

    if shadow.is_file():
        backup = yaml_io.backup_file(shadow)
        result["backup_path"] = str(backup) if backup else None
        try:
            shadow.unlink()
        except OSError as exc:
            result["error"] = f"failed to delete shadow copy: {exc}"
            return result
        result["removed"] = True
        result["shadow_exists"] = shadow.exists()

    if deploy:
        result["deploy"] = _run_deploy()
        result["ok"] = bool(
            not result["shadow_exists"]
            and (result["deploy"]["clean"] or not result["deploy"]["ran"])
        )
    else:
        result["ok"] = not result["shadow_exists"]
    return result


def plain_gloss_state(user_dir: str | Path | None = None) -> dict[str, Any]:
    """Read-only snapshot of the plain-gloss state (never raises)."""
    if user_dir is None:
        user_dir = paths.rime_user_dir()
    shadow = SHADOW_PATH(user_dir)
    installed = installed_filter_path()

    shadow_exists = shadow.is_file()
    marker_empty = False
    shadow_text: str | None = None
    if shadow_exists:
        try:
            shadow_text = shadow.read_text(encoding="utf-8-sig")
        except OSError:
            shadow_text = None
    if shadow_text is not None:
        marker_empty = marker_is_empty(shadow_text)

    installed_sha = sha256_file(installed) if installed else None
    shadow_sha = sha256_file(shadow) if shadow_exists else None

    return {
        "user_dir": str(user_dir),
        "shadow_path": str(shadow),
        "installed_path": str(installed) if installed is not None else None,
        "shadow_exists": shadow_exists,
        "marker_is_empty": marker_empty,
        "sha256": shadow_sha,
        "installed_sha256": installed_sha,
        "active": bool(shadow_exists and marker_empty),
    }
