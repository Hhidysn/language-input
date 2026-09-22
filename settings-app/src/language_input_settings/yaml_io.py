"""YAML / text I/O helpers for the Language Input settings app.

Writing rules (design doc §5.6):

* UTF-8 **without BOM**, LF newlines.
* Atomic: write a temp file in the *same* directory, then ``os.replace``.
* Back up every existing file before overwriting it
  (``<name>.bak-<YYYYmmddTHHMMSSZ>``).

These helpers are for **app-owned files only**, plus one explicitly surgical
operation on ``user.yaml`` (:func:`set_user_yaml_option`).  We deliberately do
**not** round-trip-parse Rime's existing YAML with ruamel for rewriting:

* ``__include`` / ``__patch`` / slash keys are ordinary YAML and *can* be
  parsed, but re-dumping would reformat unrelated content and risks clobbering
  live ``user.yaml`` state (``var/last_build_time`` etc.).
* Therefore :func:`set_user_yaml_option` performs a byte-preserving textual
  edit of only the relevant lines.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

try:  # ruamel is a declared dependency, but keep import failures graceful.
    from ruamel.yaml import YAML as _YAML

    HAS_RUAMEL = True
except Exception:  # pragma: no cover - only when dependency is missing
    _YAML = None  # type: ignore
    HAS_RUAMEL = False

__all__ = [
    "HAS_RUAMEL",
    "atomic_write_text",
    "atomic_write_yaml",
    "backup_file",
    "read_yaml",
    "read_yaml_text",
    "read_user_yaml_options",
    "set_user_yaml_option",
    "remove_user_yaml_option",
]

# --- Basic text I/O ---------------------------------------------------------

def atomic_write_text(path: str | os.PathLike[str], text: str) -> Path:
    """Atomically write ``text`` as UTF-8 (no BOM) with LF newlines.

    ``\\r\\n`` and lone ``\\r`` are normalised to ``\\n``.  A missing parent
    directory is created.  A temp file is created in the destination directory
    and moved into place with :func:`os.replace`, so a torn write can never be
    observed.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    normalised = text.replace("\r\n", "\n").replace("\r", "\n")

    fd, tmp_name = tempfile.mkstemp(
        prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalised)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return dest


def read_yaml_text(path: str | os.PathLike[str]) -> str:
    """Read a text file, tolerating an optional BOM (for app-owned files)."""
    return Path(path).read_text(encoding="utf-8-sig")


# --- YAML helpers (app-owned files only) ------------------------------------

def read_yaml(path: str | os.PathLike[str], typ: str = "safe") -> Any:
    """Parse a **app-owned** YAML file with ruamel.

    Never use this to rewrite Rime's existing configuration files; see the
    module docstring.
    """
    if _YAML is None:  # pragma: no cover - dependency missing
        raise RuntimeError(
            "ruamel.yaml is not installed; cannot parse YAML "
            "(install the 'ruamel.yaml' dependency)"
        )
    yaml = _YAML(typ=typ)
    with open(path, "r", encoding="utf-8-sig") as handle:
        return yaml.load(handle)


def atomic_write_yaml(path: str | os.PathLike[str], data: Any) -> Path:
    """Atomically dump ``data`` to YAML.

    **ONLY for app-owned files.**  Uses ``allow_unicode=True``, block style and
    a 2-space mapping indent, then writes atomically as UTF-8 (no BOM) with LF
    newlines.
    """
    if _YAML is None:  # pragma: no cover - dependency missing
        raise RuntimeError(
            "ruamel.yaml is not installed; cannot write YAML "
            "(install the 'ruamel.yaml' dependency)"
        )
    yaml = _YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    buffer = StringIO()
    yaml.dump(data, buffer)
    return atomic_write_text(path, buffer.getvalue())


# --- Backups ----------------------------------------------------------------

def backup_file(path: str | os.PathLike[str]) -> Path | None:
    """Copy ``path`` to ``<name>.bak-<UTCstamp>``.

    Returns the backup path, or ``None`` if the source does not exist.  If a
    backup with the same second-resolution timestamp already exists a numeric
    suffix is appended so an earlier backup is never overwritten.
    """
    src = Path(path)
    if not src.is_file():
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = src.with_name(src.name + f".bak-{stamp}")
    counter = 0
    while dest.exists():
        counter += 1
        dest = src.with_name(src.name + f".bak-{stamp}-{counter}")
    shutil.copy2(src, dest)
    return dest


# --- Surgical user.yaml option editing --------------------------------------

_RE_VAR = re.compile(r"^(\s*)var:\s*(#.*)?$")
_RE_OPTION = re.compile(r"^(\s*)option:\s*(#.*)?$")
_RE_OPTION_INLINE = re.compile(r"^(\s*)option:\s*(\{.*\})\s*(#.*)?$")
_RE_KEY = re.compile(r"^(\s*)([^:#][^:]*?)\s*:\s*(.*)$")

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_bool(token: str) -> bool | None:
    token = token.strip().strip('"').strip("'").lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    return None


def _upsert_flow_option(line: str, name: str, value: str) -> str:
    """Set ``name: value`` inside an inline ``option: {...}`` mapping."""
    match = _RE_OPTION_INLINE.match(line)
    if not match:  # pragma: no cover - caller checked
        return line
    indent, body, comment = match.group(1), match.group(2), match.group(3) or ""
    inner = body[1:-1].strip()
    key_pattern = re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*:\s*[^,}]*"
    )
    if key_pattern.search(inner):
        inner = key_pattern.sub(f"{name}: {value}", inner, count=1)
    else:
        inner = inner.rstrip()
        if inner and not inner.endswith(","):
            inner += ","
        inner = f"{inner} {name}: {value}".strip()
    rebuilt = f"{indent}option: {{{inner}}}"
    if comment:
        rebuilt += " " + comment.strip()
    return rebuilt


def _upsert_var_option(text: str, name: str, value: bool) -> str:
    """Return ``text`` with ``var/option/<name>`` set to ``value``.

    Pure and unit-testable.  All lines other than the affected block are
    preserved byte-for-byte.  Handles three shapes:

    1. block mapping ``var:\\n  option:\\n    <name>: ...`` (replace or insert)
    2. flow mapping ``var:\\n  option: {<name>: ...}``
    3. missing ``var:`` / ``option:`` (append a minimal block)
    """
    literal = "true" if value else "false"
    lines = text.split("\n")
    trailing = None
    if lines and lines[-1] == "":
        trailing = lines.pop()  # remember the terminating newline

    def find_var() -> int | None:
        for index, line in enumerate(lines):
            if _RE_VAR.match(line):
                return index
        return None

    def find_option(var_index: int) -> int | None:
        var_indent = _indent_of(lines[var_index])
        index = var_index + 1
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                index += 1
                continue
            if _indent_of(line) <= var_indent:
                break
            if _RE_OPTION.match(line) or _RE_OPTION_INLINE.match(line):
                return index
            index += 1
        return None

    var_index = find_var()

    if var_index is None:
        lines.append("var:")
        lines.append("  option:")
        lines.append(f"    {name}: {literal}")
    else:
        option_index = find_option(var_index)
        if option_index is None:
            option_indent = _indent_of(lines[var_index]) + 2
            lines.insert(var_index + 1, " " * option_indent + "option:")
            lines.insert(
                var_index + 2, " " * (option_indent + 2) + f"{name}: {literal}"
            )
        elif _RE_OPTION_INLINE.match(lines[option_index]):
            lines[option_index] = _upsert_flow_option(
                lines[option_index], name, literal
            )
        else:
            option_indent = _indent_of(lines[option_index])
            key_index = None
            scan = option_index + 1
            while scan < len(lines):
                line = lines[scan]
                stripped = line.strip()
                if not stripped:
                    scan += 1
                    continue
                if _indent_of(line) <= option_indent:
                    break
                match = _RE_KEY.match(line)
                if match and match.group(2).strip() == name:
                    key_index = scan
                    break
                scan += 1

            if key_index is not None:
                line = lines[key_index]
                indent = _indent_of(line)
                comment = ""
                raw_value = _RE_KEY.match(line).group(3)  # type: ignore[union-attr]
                if "#" in raw_value:
                    comment = " " + raw_value[raw_value.index("#") :].strip()
                lines[key_index] = " " * indent + f"{name}: {literal}{comment}"
            else:
                insert_at = option_index + 1
                while insert_at < len(lines):
                    line = lines[insert_at]
                    stripped = line.strip()
                    if stripped and _indent_of(line) <= option_indent:
                        break
                    insert_at += 1
                lines.insert(
                    insert_at,
                    " " * (option_indent + 2) + f"{name}: {literal}",
                )

    result = "\n".join(lines)
    if trailing is not None:
        result += "\n"
    return result


def set_user_yaml_option(
    path: str | os.PathLike[str], name: str, value: bool
) -> bool:
    """Surgically set ``var/option/<name>`` in ``user.yaml``.

    Checks whether anything would change **first**, then backs the file up and
    rewrites only the affected lines.  Returns ``True`` if the file changed.
    Writing is atomic (UTF-8 no BOM, LF).  The no-op check avoids accumulating
    backups on repeated applies of an unchanged value (N4).
    """
    target = Path(path)
    original = read_yaml_text(target)
    updated = _upsert_var_option(original, name, bool(value))
    if updated == original:
        return False
    backup_file(target)
    atomic_write_text(target, updated)
    return True


def _remove_var_option(text: str, name: str) -> "str | None":
    """Return ``text`` with ``var/option/<name>`` removed.

    Returns ``None`` when the option is spelled as an inline flow mapping
    (``option: {a: 1, b: 2}``): removing one entry from that shape without a
    real YAML round-trip is not safe, so callers must report it rather than
    silently skip it.  An unchanged string is returned when nothing matched.
    """
    lines = text.split("\n")
    trailing = None
    if lines and lines[-1] == "":
        trailing = lines.pop()

    var_index = None
    for index, line in enumerate(lines):
        if _RE_VAR.match(line):
            var_index = index
            break
    if var_index is None:
        return text

    var_indent = _indent_of(lines[var_index])
    option_index = None
    scan = var_index + 1
    while scan < len(lines):
        line = lines[scan]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            scan += 1
            continue
        if _indent_of(line) <= var_indent:
            break
        if _RE_OPTION.match(line) or _RE_OPTION_INLINE.match(line):
            option_index = scan
            break
        scan += 1
    if option_index is None:
        return text
    if _RE_OPTION_INLINE.match(lines[option_index]):
        return None

    option_indent = _indent_of(lines[option_index])
    key_index = None
    scan = option_index + 1
    while scan < len(lines):
        line = lines[scan]
        stripped = line.strip()
        if not stripped:
            scan += 1
            continue
        if _indent_of(line) <= option_indent:
            break
        if stripped.startswith("#"):
            scan += 1
            continue
        match = _RE_KEY.match(line)
        if match and match.group(2).strip() == name:
            key_index = scan
            break
        scan += 1
    if key_index is None:
        return text

    del lines[key_index]

    # Drop an ``option:`` block that no longer has any entries.
    remaining = False
    scan = option_index + 1
    while scan < len(lines):
        line = lines[scan]
        stripped = line.strip()
        if not stripped:
            scan += 1
            continue
        if _indent_of(line) <= option_indent:
            break
        if stripped.startswith("#"):
            scan += 1
            continue
        remaining = True
        break
    if not remaining:
        del lines[option_index]
        # ... and a ``var:`` block that becomes empty in turn.
        var_remaining = False
        scan = var_index + 1
        while scan < len(lines):
            line = lines[scan]
            stripped = line.strip()
            if not stripped:
                scan += 1
                continue
            if _indent_of(line) <= var_indent:
                break
            if stripped.startswith("#"):
                scan += 1
                continue
            var_remaining = True
            break
        if not var_remaining:
            del lines[var_index]

    result = "\n".join(lines)
    if trailing is not None:
        result += "\n"
    return result


def remove_user_yaml_option(
    path: str | os.PathLike[str], name: str
) -> dict[str, Any]:
    """Surgically remove ``var/option/<name>`` from ``user.yaml``.

    Pure-text, byte-preserving edit of only the affected lines (the same
    discipline as :func:`set_user_yaml_option`).  Backs the file up and writes
    atomically when something changes.  Returns
    ``{"path", "name", "removed", "unsafe"}`` where ``unsafe`` is True for an
    inline flow ``option:`` mapping that cannot be edited safely.
    """
    target = Path(path)
    result: dict[str, Any] = {
        "path": str(target),
        "name": name,
        "removed": False,
        "unsafe": False,
    }
    try:
        original = read_yaml_text(target)
    except OSError:
        return result
    updated = _remove_var_option(original, name)
    if updated is None:
        result["unsafe"] = True
        return result
    if updated == original:
        return result
    backup_file(target)
    atomic_write_text(target, updated)
    result["removed"] = True
    return result


def read_user_yaml_options(path: str | os.PathLike[str]) -> dict[str, bool]:
    """Read ``var/option/*`` booleans from ``user.yaml`` (read-only).

    Tolerant of block and flow mappings and of unknown/non-boolean values
    (which are skipped).  Returns an empty dict on any read/parse issue.
    """
    result: dict[str, bool] = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return result

    lines = text.split("\n")
    var_index = None
    for index, line in enumerate(lines):
        if _RE_VAR.match(line):
            var_index = index
            break
    if var_index is None:
        return result

    var_indent = _indent_of(lines[var_index])
    option_index = None
    scan = var_index + 1
    while scan < len(lines):
        line = lines[scan]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            scan += 1
            continue
        if _indent_of(line) <= var_indent:
            break
        if _RE_OPTION.match(line) or _RE_OPTION_INLINE.match(line):
            option_index = scan
            break
        scan += 1
    if option_index is None:
        return result

    inline = _RE_OPTION_INLINE.match(lines[option_index])
    if inline:
        for part in inline.group(2)[1:-1].split(","):
            if ":" not in part:
                continue
            key, raw = part.split(":", 1)
            parsed = _parse_bool(raw)
            if parsed is not None:
                result[key.strip()] = parsed
        return result

    option_indent = _indent_of(lines[option_index])
    scan = option_index + 1
    while scan < len(lines):
        line = lines[scan]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            scan += 1
            continue
        if _indent_of(line) <= option_indent:
            break
        match = _RE_KEY.match(line)
        if match:
            raw = match.group(3).split("#", 1)[0]
            parsed = _parse_bool(raw)
            if parsed is not None:
                result[match.group(2).strip()] = parsed
        scan += 1
    return result
