"""Model component installation / layout synthesis (M3).

This module deliberately mirrors the frozen host's install discipline
(``scripts/language_input_model_host.py``):

* ``synthesize_component`` builds the exact ``installed.json`` (5 top-level
  keys) plus the ``model/<files>`` payload, then self-verifies by re-reading
  and re-hashing — mirroring ``load_installed_component``.
* ``install_from_sources`` reproduces ``install_pack``'s backup/swap/rollback
  (``os.replace`` target→backup, staging→target, restore on failure,
  ``rmtree`` backup on success) and recovers from a swap interrupted between
  the two renames.
* ``install_from_limodel`` only delegates to the frozen host's own verifier
  (``LanguageInputModelHost.exe --import-pack``) when the pack matches the
  shipped catalog exactly.

All text writes are UTF-8 **without BOM**, LF, atomic (via
:func:`yaml_io.atomic_write_text`).  A BOM would make the host silently skip
the component.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath

from . import paths
from .models_catalog import (
    INSTALL_FORMAT,
    M2M100_INSTALL_FORMAT,
    M2M100_PACK_FORMAT,
    PACK_FORMAT,
    ModelComponent,
    installed_components,
)
from .models_download import IntegrityError, sha256_file
from .yaml_io import atomic_write_text

__all__ = [
    "SynthesisError",
    "synthesize_component",
    "verify_installed_component",
    "recover_interrupted",
    "install_from_sources",
    "install_from_dir",
    "install_from_limodel",
    "running_processes",
    "assert_host_stopped",
    "wait_for_model_processes",
]

_SERVER_EXE = "WeaselServer.exe"
_HOST_EXE = "LanguageInputModelHost.exe"
_INSTALL_KEYS = {"format", "component_id", "pack_size", "pack_sha256", "manifest"}
_BACKUP_PREFIX = ".backup-"
_STAGING_PREFIX = ".staging-"


class SynthesisError(RuntimeError):
    """Component layout could not be synthesized or self-verified."""


def _safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SynthesisError(f"unsafe component path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SynthesisError(f"unsafe component path: {value!r}")
    return path


def _manifest_path(descriptor_path: str) -> str:
    """Return the ``model/``-prefixed manifest path for a descriptor path."""
    if descriptor_path.startswith("model/"):
        return descriptor_path
    return "model/" + descriptor_path


def _aggregate_sha(files: list[dict]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for row in files:
        digest.update(f"{row['path']}:{row['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


# --- synthesis --------------------------------------------------------------

def synthesize_component(
    component: ModelComponent,
    files_dir: Path | str,
    staging_dir: Path | str,
    *,
    license_path: Path | str | None = None,
    notice_path: Path | str | None = None,
) -> dict:
    """Build an installable component layout in ``staging_dir``.

    Copies each descriptor payload file from ``files_dir`` to
    ``staging/model/<path>`` (verifying size + SHA-256), writes the 5-key
    ``installed.json``, and self-verifies.  M2M100 additionally requires
    byte-exact ``LICENSE.txt`` / ``NOTICE.txt`` at the component root.

    Returns ``{"record": ..., "verify": ...}``.
    """
    files_dir = Path(files_dir)
    staging = Path(staging_dir)
    if staging.exists():
        raise FileExistsError(f"staging directory already exists: {staging}")
    staging.mkdir(parents=True)

    if not component.files:
        raise SynthesisError(
            f"component {component.component_id!r} has no per-file trust data"
        )

    manifest_files: list[dict] = []
    payload_bytes = 0
    for item in component.files:
        source = files_dir / item.path
        if not source.is_file():
            raise FileNotFoundError(f"missing source file for {component.component_id}: {source}")
        relative = _safe_relative_path(_manifest_path(item.path))
        destination = staging.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        size = destination.stat().st_size
        digest = sha256_file(destination)
        if size != item.size or digest != item.sha256:
            raise IntegrityError(
                f"{item.path}: source does not match the descriptor "
                f"(expected size={item.size} sha256={item.sha256}, "
                f"got size={size} sha256={digest})"
            )
        manifest_files.append({"path": str(relative), "size": size, "sha256": digest})
        payload_bytes += size

    is_m2m100 = component.backend == "m2m100"
    manifest: dict = {
        "format": M2M100_PACK_FORMAT if is_m2m100 else PACK_FORMAT,
        "component_id": component.component_id,
        "repository": component.repository,
        "revision": component.revision,
        "license": component.license,
        "provides": list(component.provides),
        "requires": list(component.requires),
        "runtime_bytes": component.runtime_bytes,
        "files": manifest_files,
    }
    if component.license_sha256:
        manifest["license_sha256"] = component.license_sha256
    if component.notice_sha256:
        manifest["notice_sha256"] = component.notice_sha256

    if is_m2m100:
        for name, explicit, expected in (
            ("LICENSE.txt", license_path, component.license_sha256),
            ("NOTICE.txt", notice_path, component.notice_sha256),
        ):
            source = Path(explicit) if explicit is not None else (files_dir / name)
            if not source.is_file():
                raise FileNotFoundError(
                    f"M2M100 component requires {name} (looked in {source})"
                )
            destination = staging / name
            shutil.copyfile(source, destination)
            digest = sha256_file(destination)
            if expected and digest != expected:
                raise IntegrityError(
                    f"{name}: sha256 mismatch (expected {expected}, got {digest})"
                )
            manifest["license_sha256" if name == "LICENSE.txt" else "notice_sha256"] = digest

    record = {
        "format": M2M100_INSTALL_FORMAT if is_m2m100 else INSTALL_FORMAT,
        "component_id": component.component_id,
        "pack_size": int(component.file_size) if component.file_size else payload_bytes,
        "pack_sha256": component.file_sha256 or _aggregate_sha(manifest_files),
        "manifest": manifest,
    }
    text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(staging / "installed.json", text)

    verification = verify_installed_component(staging)
    if not verification["ok"]:
        raise SynthesisError(
            "self-verification of the synthesized layout failed: "
            + "; ".join(verification["errors"])
        )
    return {"record": record, "verify": verification}


# --- verification -----------------------------------------------------------

def verify_installed_component(component_root: Path | str) -> dict:
    """Re-read and re-hash an installed component (mirrors the host).

    Returns a per-file report: ``ok`` plus ``files[]`` with ``exists``,
    ``size_ok`` and ``sha256_ok``, and a list of structural ``errors``.
    """
    root = Path(component_root)
    result: dict = {
        "component_id": root.name,
        "root": str(root),
        "ok": False,
        "format": None,
        "files": [],
        "errors": [],
    }

    if root.is_symlink() or not root.is_dir():
        result["errors"].append("component root is not a real directory")
        return result
    try:
        record = json.loads((root / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["errors"].append(f"cannot read installed.json: {exc}")
        return result

    if not isinstance(record, dict) or set(record) != _INSTALL_KEYS:
        result["errors"].append("installed.json must have exactly the 5 required keys")
        return result
    result["component_id"] = record.get("component_id")
    result["format"] = record.get("format")
    if record["format"] not in (INSTALL_FORMAT, M2M100_INSTALL_FORMAT):
        result["errors"].append(f"unknown install format: {record['format']!r}")
        return result

    manifest = record.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("component_id") != record.get(
        "component_id"
    ):
        result["errors"].append("manifest.component_id does not match record.component_id")
        return result
    is_m2m100 = record["format"] == M2M100_INSTALL_FORMAT
    if is_m2m100 and manifest.get("format") != M2M100_PACK_FORMAT:
        result["errors"].append("M2M100 manifest.format is invalid")

    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        result["errors"].append("manifest.files is missing or empty")
        return result

    for row in rows:
        entry = {
            "path": row.get("path") if isinstance(row, dict) else None,
            "expected_size": row.get("size") if isinstance(row, dict) else None,
            "expected_sha256": row.get("sha256") if isinstance(row, dict) else None,
            "exists": False,
            "actual_size": None,
            "actual_sha256": None,
            "size_ok": False,
            "sha256_ok": False,
            "ok": False,
        }
        try:
            relative = _safe_relative_path(entry["path"])
            if relative.parts[0] != "model":
                raise SynthesisError("path is not under model/")
        except SynthesisError as exc:
            entry["error"] = str(exc)
            result["files"].append(entry)
            continue

        path = root.joinpath(*relative.parts)
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            result["files"].append(entry)
            continue
        if path.is_symlink() or root not in resolved.parents:
            entry["error"] = "file escaped its component root"
            result["files"].append(entry)
            continue

        entry["exists"] = True
        entry["actual_size"] = resolved.stat().st_size
        entry["actual_sha256"] = sha256_file(resolved)
        entry["size_ok"] = entry["actual_size"] == entry["expected_size"]
        entry["sha256_ok"] = entry["actual_sha256"] == entry["expected_sha256"]
        entry["ok"] = bool(entry["size_ok"] and entry["sha256_ok"])
        result["files"].append(entry)

    if is_m2m100:
        for name, key in (("LICENSE.txt", "license_sha256"), ("NOTICE.txt", "notice_sha256")):
            path = root / name
            entry = {
                "path": name,
                "expected_sha256": manifest.get(key),
                "exists": path.is_file(),
                "actual_sha256": None,
                "size_ok": None,
                "sha256_ok": False,
                "ok": False,
            }
            if entry["exists"]:
                entry["actual_sha256"] = sha256_file(path)
                entry["sha256_ok"] = entry["actual_sha256"] == entry["expected_sha256"]
                entry["ok"] = bool(entry["sha256_ok"])
            result["files"].append(entry)

    result["ok"] = not result["errors"] and bool(result["files"]) and all(
        entry.get("ok") for entry in result["files"]
    )
    return result


# --- interrupted-swap recovery ---------------------------------------------

def recover_interrupted(model_root: Path | str, *, exclude=()) -> dict:
    """Recover backups / clean staging left by an interrupted swap.

    * ``.backup-<id>-<uuid>`` with a missing target is restored to ``<id>``.
    * ``.backup-<id>-<uuid>`` with an existing target is deleted.
    * ``.staging-<id>-<uuid>`` is deleted, except names in ``exclude`` (used to
      protect the staging directory that is about to be installed).
    """
    root = Path(model_root)
    restored: list[str] = []
    cleaned: list[str] = []
    excluded_names = {Path(item).name for item in exclude}
    if not root.is_dir():
        return {"restored": restored, "cleaned": cleaned}
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return {"restored": restored, "cleaned": cleaned}

    for entry in entries:
        name = entry.name
        if name.startswith(_BACKUP_PREFIX):
            body = name[len(_BACKUP_PREFIX) :]
            component_id = body[:-33] if len(body) > 33 else body
            if not component_id:
                continue
            target = root / component_id
            if target.exists():
                shutil.rmtree(entry, ignore_errors=True)
                cleaned.append(name)
            else:
                os.replace(entry, target)
                restored.append(name)
        elif name.startswith(_STAGING_PREFIX):
            if name in excluded_names:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            cleaned.append(name)
    return {"restored": restored, "cleaned": cleaned}


# --- install ----------------------------------------------------------------

def install_from_sources(
    component: ModelComponent,
    staging_dir: Path | str,
    model_root: Path | str,
    *,
    replace: bool = False,
    check_running: bool = True,
    wait_timeout: float = 30.0,
) -> dict:
    """Swap a synthesized ``staging_dir`` into ``model_root``.

    Mirrors the host's ``install_pack``.  ``staging_dir`` and ``model_root``
    must be on the same volume (both renames are ``os.replace``).
    """
    root = Path(model_root)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise SynthesisError("model root must be a real directory")

    staging = Path(staging_dir)
    # Clean leftovers, but never the staging directory we are about to install.
    recovered = recover_interrupted(root, exclude=[staging])
    if check_running:
        assert_host_stopped(root, timeout=wait_timeout)

    verification = verify_installed_component(staging)
    if not verification["ok"]:
        raise SynthesisError(
            "refusing to install an invalid staging component: "
            + "; ".join(verification["errors"])
        )
    if verification["component_id"] != component.component_id:
        raise SynthesisError("staging component_id does not match the requested component")

    present = installed_components(root)
    missing = [dep for dep in component.requires if dep not in present]
    if missing:
        raise ValueError("missing required component(s): " + ", ".join(missing))

    target = root / component.component_id
    if target.exists() and not replace:
        raise FileExistsError(f"component is already installed: {component.component_id}")

    backup = root / f"{_BACKUP_PREFIX}{component.component_id}-{uuid.uuid4().hex}"
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(staging, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)

    record = json.loads((target / "installed.json").read_text(encoding="utf-8"))
    return {
        "record": record,
        "target": str(target),
        "recovered": recovered,
        "verification": verification,
    }


def install_from_dir(
    component: ModelComponent,
    files_dir: Path | str,
    model_root: Path | str,
    *,
    replace: bool = False,
    check_running: bool = True,
) -> dict:
    """Synthesize from a raw source directory, then install (convenience)."""
    root = Path(model_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f"{_STAGING_PREFIX}{component.component_id}-{uuid.uuid4().hex}"
    synthesize_component(component, files_dir, staging)
    try:
        return install_from_sources(
            component, staging, root, replace=replace, check_running=check_running
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def install_from_limodel(
    pack_path: Path | str,
    model_root: Path | str,
    *,
    replace: bool = False,
    m2m100: bool = False,
) -> dict:
    """Import an exact catalog ``.limodel`` via the frozen host.

    The pack is first checked against the shipped catalog (size + SHA-256).
    If it does not match exactly, this raises and points at
    :func:`install_from_sources` (user-made packs are rejected by the host's
    ``audit_pack``).
    """
    pack = Path(pack_path)
    if not pack.is_file():
        raise FileNotFoundError(f"model pack not found: {pack}")
    if pack.is_symlink():
        raise SynthesisError("model pack must be a real file")

    install_root = paths.weasel_root()
    quickmt_catalog = paths.packs_catalog(install_root)
    catalog = paths.m2m100_catalog(install_root) if m2m100 else quickmt_catalog
    if catalog is None or not catalog.is_file():
        raise FileNotFoundError(
            f"catalog not found for {'m2m100' if m2m100 else 'quickmt'}"
        )
    try:
        document = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SynthesisError(f"cannot read catalog {catalog}: {exc}") from exc

    size = pack.stat().st_size
    digest = sha256_file(pack)
    matches = [
        row
        for row in document.get("components", [])
        if isinstance(row, dict)
        and row.get("file_size") == size
        and str(row.get("file_sha256", "")).lower() == digest.lower()
    ]
    if len(matches) != 1:
        raise ValueError(
            f"model pack {pack.name} does not exactly match the installed catalog "
            "(size/SHA-256). The frozen host's --import-pack would reject it; use "
            "install_from_sources() to synthesize a layout from verified source files "
            "instead."
        )
    component = matches[0]

    root = Path(model_root)
    present = installed_components(root)
    if component.get("id") in present and not replace:
        raise FileExistsError(f"component is already installed: {component.get('id')}")

    assert_host_stopped(root)

    host = paths.find_model_host_exe(install_root) or paths.model_host_exe(install_root)
    if host is None or not host.is_file():
        raise FileNotFoundError("LanguageInputModelHost.exe was not found")

    command = [
        str(host),
        "--catalog",
        str(quickmt_catalog),
        "--models",
        str(root),
        "--import-pack",
        str(pack),
    ]
    if m2m100:
        command += ["--m2m100", "--m2m100-catalog", str(catalog)]
    if replace:
        command.append("--replace")

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise SynthesisError(f"host import timed out after 600s: {exc}") from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        raise SynthesisError(
            f"host --import-pack failed (exit {completed.returncode}): "
            f"{(stderr or stdout).strip()}"
        )
    record = None
    for line in reversed(stdout.strip().splitlines()):
        try:
            record = json.loads(line)
            break
        except ValueError:
            continue
    return {
        "component_id": component.get("id"),
        "exit_code": completed.returncode,
        "record": record,
        "stdout": stdout,
        "stderr": stderr,
    }


# --- process guards ---------------------------------------------------------

def _process_running(name: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return name.lower() in (completed.stdout or "").lower()


def running_processes() -> list[str]:
    """Names among WeaselServer / LanguageInputModelHost that are running."""
    return [name for name in (_SERVER_EXE, _HOST_EXE) if _process_running(name)]


def wait_for_model_processes(timeout: float = 30.0, poll: float = 0.5) -> list[str]:
    """Wait for BOTH WeaselServer and the model host to exit.

    The model host is a child of WeaselServer and ``Stop()`` only closes the
    kill-on-close job — it does **not** wait for the child to terminate
    (``LanguageInputRemote.cpp``), hence this explicit wait.  Returns the
    still-running names (empty on success).
    """
    deadline = time.monotonic() + timeout
    while True:
        running = running_processes()
        if not running:
            return []
        if time.monotonic() >= deadline:
            return running
        time.sleep(poll)


def assert_host_stopped(model_root: Path | str | None = None, *, timeout: float = 0.0) -> None:
    """Raise unless WeaselServer and the model host are stopped.

    ``timeout > 0`` waits up to that long for them to exit first.
    """
    if timeout > 0:
        running = wait_for_model_processes(timeout)
    else:
        running = running_processes()
    if running:
        raise RuntimeError(
            "refusing to modify model files while these processes are running: "
            + ", ".join(running)
        )
