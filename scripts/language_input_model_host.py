#!/usr/bin/env python3
"""Offline Language Input model-pack manager and localhost translation host."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath
from typing import Sequence


CATALOG_SCHEMA_VERSION = 2
PACK_FORMAT = "language-input-model-pack-v1"
M2M100_CATALOG_SCHEMA_VERSION = 1
M2M100_PACK_FORMAT = "language-input-m2m100-model-pack-v1"
QUICKMT_MODEL_ID = "quickmt-gloss-route-v2"
M2M100_MODEL_ID = "m2m100-418m-int8"
INSTALL_FORMAT = "language-input-installed-component-v1"
M2M100_INSTALL_FORMAT = "language-input-installed-m2m100-component-v1"
MAX_COMPONENT_BYTES = 2 * 1024 * 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_WORD_BYTES = 128
MAX_WORDS = 9
MAX_GLOSS_CHARACTERS = 40
SUPPORTED_LANGUAGES = {"en", "ja", "es"}
SUPPORTED_MODELS = {QUICKMT_MODEL_ID, M2M100_MODEL_ID}
COMPONENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("unsafe archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe archive path")
    return path


def load_pack_catalog(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "format", "route_id", "runtime", "components", "routes"}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("model-pack catalog has unexpected or missing keys")
    if document["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported model-pack catalog schema")
    if document["format"] != PACK_FORMAT:
        raise ValueError("unsupported model-pack format")
    components = document["components"]
    routes = document["routes"]
    if not isinstance(components, list) or not components or not isinstance(routes, dict):
        raise ValueError("model-pack catalog has invalid components or routes")
    checked: dict[str, dict[str, object]] = {}
    component_keys = {
        "id",
        "display_name",
        "file_name",
        "file_size",
        "file_sha256",
        "revision",
        "provides",
        "requires",
        "license",
    }
    for component in components:
        if not isinstance(component, dict) or set(component) != component_keys:
            raise ValueError("catalog component has unexpected or missing keys")
        component_id = component["id"]
        if not isinstance(component_id, str) or not COMPONENT_ID.fullmatch(component_id):
            raise ValueError("catalog component id is invalid")
        if component_id in checked:
            raise ValueError("catalog contains duplicate component ids")
        if (
            not isinstance(component["file_size"], int)
            or component["file_size"] <= 0
            or component["file_size"] > MAX_COMPONENT_BYTES
            or not _is_sha256(component["file_sha256"])
            or component["license"] != "CC-BY-4.0"
        ):
            raise ValueError("catalog component integrity metadata is invalid")
        if not isinstance(component["provides"], list) or not component["provides"]:
            raise ValueError("catalog component provides is invalid")
        if not isinstance(component["requires"], list):
            raise ValueError("catalog component requires is invalid")
        if any(language not in SUPPORTED_LANGUAGES for language in component["provides"]):
            raise ValueError("catalog component provides an unsupported language")
        checked[component_id] = component
    for component in components:
        if any(required_id not in checked for required_id in component["requires"]):
            raise ValueError("catalog component has an unknown dependency")
    if set(routes) != SUPPORTED_LANGUAGES:
        raise ValueError("catalog routes must exactly define en, ja and es")
    for language, component_ids in routes.items():
        if (
            not isinstance(component_ids, list)
            or not component_ids
            or any(component_id not in checked for component_id in component_ids)
            or language not in checked[component_ids[-1]]["provides"]
        ):
            raise ValueError("catalog route is invalid")
    document["components_by_id"] = checked
    return document


def load_m2m100_pack_catalog(path: Path) -> dict[str, object]:
    """Load the independent MIT/CTranslate2 M2M100 catalog."""
    document = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "format", "route_id", "runtime", "components", "routes"}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("M2M100 catalog has unexpected or missing keys")
    if document["schema_version"] != M2M100_CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported M2M100 catalog schema")
    if document["format"] != M2M100_PACK_FORMAT:
        raise ValueError("unsupported M2M100 model-pack format")

    runtime = document["runtime"]
    runtime_keys = {"host", "architecture", "ctranslate2", "sentencepiece"}
    if (
        not isinstance(runtime, dict)
        or set(runtime) != runtime_keys
        or runtime["host"] != "LanguageInputModelHost"
        or runtime["architecture"] != "x64"
        or runtime["ctranslate2"] != "4.8.1"
        or runtime["sentencepiece"] != "0.2.1"
    ):
        raise ValueError("M2M100 catalog runtime metadata is invalid")

    components = document["components"]
    routes = document["routes"]
    if not isinstance(components, list) or not components or not isinstance(routes, dict):
        raise ValueError("M2M100 catalog has invalid components or routes")
    checked: dict[str, dict[str, object]] = {}
    component_keys = {
        "id",
        "display_name",
        "file_name",
        "file_size",
        "file_sha256",
        "revision",
        "repository",
        "provides",
        "requires",
        "license",
        "license_sha256",
        "notice_sha256",
        "runtime_bytes",
        "files",
        "source",
    }
    for component in components:
        if not isinstance(component, dict) or set(component) != component_keys:
            raise ValueError("M2M100 catalog component has unexpected or missing keys")
        component_id = component["id"]
        if not isinstance(component_id, str) or not COMPONENT_ID.fullmatch(component_id):
            raise ValueError("M2M100 catalog component id is invalid")
        if component_id in checked:
            raise ValueError("M2M100 catalog contains duplicate component ids")
        if (
            not isinstance(component["display_name"], str)
            or not component["display_name"]
            or not isinstance(component["file_name"], str)
            or not component["file_name"].endswith(".limodel")
            or not isinstance(component["file_size"], int)
            or component["file_size"] <= 0
            or component["file_size"] > MAX_COMPONENT_BYTES
            or not _is_sha256(component["file_sha256"])
            or not isinstance(component["revision"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", component["revision"])
            or not isinstance(component["repository"], str)
            or component["repository"] != "facebook/m2m100_418M"
            or component["license"] != "MIT"
            or not _is_sha256(component["license_sha256"])
            or not _is_sha256(component["notice_sha256"])
            or not isinstance(component["runtime_bytes"], int)
            or component["runtime_bytes"] <= 0
            or component["runtime_bytes"] > MAX_COMPONENT_BYTES
        ):
            raise ValueError("M2M100 catalog component integrity metadata is invalid")
        try:
            file_name = _safe_relative_path(component["file_name"])
        except ValueError as error:
            raise ValueError("M2M100 catalog component path is invalid") from error
        if len(file_name.parts) != 1 or not component["file_name"].endswith(".limodel"):
            raise ValueError("M2M100 catalog component path is invalid")
        source = component["source"]
        if (
            not isinstance(source, dict)
            or set(source) != {"file_name", "size", "sha256"}
            or source["file_name"] != "pytorch_model.bin"
            or not isinstance(source["size"], int)
            or source["size"] <= 0
            or source["size"] > MAX_COMPONENT_BYTES
            or not _is_sha256(source["sha256"])
        ):
            raise ValueError("M2M100 source metadata is invalid")
        files = component["files"]
        if not isinstance(files, list) or not files:
            raise ValueError("M2M100 catalog component has no runtime files")
        file_names: set[str] = set()
        runtime_total = 0
        for row in files:
            if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
                raise ValueError("M2M100 catalog runtime file metadata is invalid")
            try:
                relative = _safe_relative_path(row["path"])
            except ValueError as error:
                raise ValueError("M2M100 catalog runtime file path is invalid") from error
            if relative.parts[0] != "model" or row["path"] in file_names:
                raise ValueError("M2M100 catalog runtime file path is invalid")
            if (
                not isinstance(row["size"], int)
                or row["size"] <= 0
                or row["size"] > MAX_COMPONENT_BYTES
                or not _is_sha256(row["sha256"])
            ):
                raise ValueError("M2M100 catalog runtime file metadata is invalid")
            file_names.add(row["path"])
            runtime_total += row["size"]
        if runtime_total != component["runtime_bytes"]:
            raise ValueError("M2M100 catalog runtime byte total is invalid")
        provides = component["provides"]
        requires = component["requires"]
        if (
            not isinstance(provides, list)
            or not provides
            or len(provides) != len(set(provides))
            or any(language not in SUPPORTED_LANGUAGES for language in provides)
            or not isinstance(requires, list)
            or any(not isinstance(value, str) for value in requires)
        ):
            raise ValueError("M2M100 catalog component languages or dependencies are invalid")
        checked[component_id] = component
    for component in components:
        if any(required_id not in checked for required_id in component["requires"]):
            raise ValueError("M2M100 catalog component has an unknown dependency")
    if set(routes) != SUPPORTED_LANGUAGES:
        raise ValueError("M2M100 catalog routes must exactly define en, ja and es")
    for language, component_ids in routes.items():
        if (
            not isinstance(component_ids, list)
            or len(component_ids) != 1
            or component_ids[0] not in checked
            or language not in checked[component_ids[0]]["provides"]
        ):
            raise ValueError("M2M100 catalog route is invalid")
    document["components_by_id"] = checked
    return document


def _read_small_archive_json(archive: zipfile.ZipFile, name: str) -> dict[str, object]:
    info = archive.getinfo(name)
    if info.file_size <= 0 or info.file_size > 1024 * 1024:
        raise ValueError(f"archive {name} is empty or too large")
    document = json.loads(archive.read(info).decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"archive {name} must contain an object")
    return document


def audit_pack(
    pack_path: Path,
    catalog_path: Path,
    *,
    verify_payloads: bool = True,
) -> dict[str, object]:
    pack_path = pack_path.resolve(strict=True)
    if not pack_path.is_file() or pack_path.is_symlink():
        raise ValueError("model pack must be a real file")
    catalog = load_pack_catalog(catalog_path)
    pack_size = pack_path.stat().st_size
    pack_hash = sha256_file(pack_path)
    matches = [
        component
        for component in catalog["components"]
        if component["file_size"] == pack_size and component["file_sha256"] == pack_hash
    ]
    if len(matches) != 1:
        raise ValueError("model pack is not an exact artifact from the installed catalog")
    catalog_component = matches[0]

    with zipfile.ZipFile(pack_path, "r") as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("model pack contains duplicate entries")
        if any(entry.compress_type != zipfile.ZIP_STORED for entry in entries):
            raise ValueError("model pack contains compressed entries")
        if "manifest.json" not in names or "NOTICE.txt" not in names:
            raise ValueError("model pack is missing its manifest or notice")
        for name in names:
            _safe_relative_path(name)
        manifest = _read_small_archive_json(archive, "manifest.json")
        manifest_keys = {
            "format",
            "component_id",
            "repository",
            "revision",
            "license",
            "provides",
            "requires",
            "runtime_bytes",
            "notice_sha256",
            "files",
        }
        if not isinstance(manifest, dict) or set(manifest) != manifest_keys:
            raise ValueError("model pack manifest has unexpected or missing keys")
        if (
            manifest["format"] != PACK_FORMAT
            or manifest["component_id"] != catalog_component["id"]
            or manifest["revision"] != catalog_component["revision"]
            or manifest["license"] != catalog_component["license"]
            or manifest["provides"] != catalog_component["provides"]
            or manifest["requires"] != catalog_component["requires"]
            or not _is_sha256(manifest["notice_sha256"])
        ):
            raise ValueError("model pack manifest disagrees with the catalog")
        files = manifest["files"]
        if not isinstance(files, list) or not files:
            raise ValueError("model pack manifest has no runtime files")
        expected_names = {"manifest.json", "NOTICE.txt"}
        runtime_total = 0
        checked_files: list[dict[str, object]] = []
        for row in files:
            if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
                raise ValueError("model pack file row is invalid")
            relative = _safe_relative_path(row["path"])
            if relative.parts[0] != "model" or len(relative.parts) < 2:
                raise ValueError("model pack runtime file is outside model/")
            if (
                not isinstance(row["size"], int)
                or row["size"] < 0
                or not _is_sha256(row["sha256"])
            ):
                raise ValueError("model pack runtime file metadata is invalid")
            expected_names.add(row["path"])
            runtime_total += row["size"]
            checked_files.append(row)
        if set(names) != expected_names or runtime_total != manifest["runtime_bytes"]:
            raise ValueError("model pack entries or runtime byte total do not match")
        notice = archive.read("NOTICE.txt")
        if not notice or hashlib.sha256(notice).hexdigest() != manifest["notice_sha256"]:
            raise ValueError("model pack notice hash mismatch")
        if verify_payloads:
            for row in checked_files:
                entry = archive.getinfo(row["path"])
                if entry.file_size != row["size"]:
                    raise ValueError("model pack runtime file size mismatch")
                digest = hashlib.sha256()
                with archive.open(entry, "r") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != row["sha256"]:
                    raise ValueError("model pack runtime file hash mismatch")
    return {
        "component": catalog_component,
        "manifest": manifest,
        "pack_path": str(pack_path),
        "pack_size": pack_size,
        "pack_sha256": pack_hash,
    }


def audit_m2m100_pack(
    pack_path: Path,
    catalog_path: Path,
    *,
    verify_payloads: bool = True,
) -> dict[str, object]:
    """Audit the independent M2M100/ MIT model package format."""
    pack_path = pack_path.resolve(strict=True)
    if not pack_path.is_file() or pack_path.is_symlink():
        raise ValueError("M2M100 model pack must be a real file")
    catalog = load_m2m100_pack_catalog(catalog_path)
    pack_size = pack_path.stat().st_size
    pack_hash = sha256_file(pack_path)
    matches = [
        component
        for component in catalog["components"]
        if component["file_size"] == pack_size and component["file_sha256"] == pack_hash
    ]
    if len(matches) != 1:
        raise ValueError("M2M100 model pack is not an exact artifact from the installed catalog")
    catalog_component = matches[0]

    with zipfile.ZipFile(pack_path, "r") as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("M2M100 model pack contains duplicate entries")
        if any(entry.compress_type != zipfile.ZIP_STORED for entry in entries):
            raise ValueError("M2M100 model pack contains compressed entries")
        required_names = {"manifest.json", "NOTICE.txt", "LICENSE.txt"}
        if not required_names.issubset(names):
            raise ValueError("M2M100 model pack is missing its manifest, license, or notice")
        for name in names:
            _safe_relative_path(name)
        manifest = _read_small_archive_json(archive, "manifest.json")
        manifest_keys = {
            "format",
            "component_id",
            "repository",
            "revision",
            "license",
            "license_sha256",
            "provides",
            "requires",
            "runtime_bytes",
            "notice_sha256",
            "files",
            "source",
        }
        if not isinstance(manifest, dict) or set(manifest) != manifest_keys:
            raise ValueError("M2M100 model pack manifest has unexpected or missing keys")
        if (
            manifest["format"] != M2M100_PACK_FORMAT
            or manifest["component_id"] != catalog_component["id"]
            or manifest["repository"] != catalog_component["repository"]
            or manifest["revision"] != catalog_component["revision"]
            or manifest["license"] != catalog_component["license"]
            or manifest["license_sha256"] != catalog_component["license_sha256"]
            or manifest["notice_sha256"] != catalog_component["notice_sha256"]
            or manifest["provides"] != catalog_component["provides"]
            or manifest["requires"] != catalog_component["requires"]
            or manifest["source"] != catalog_component["source"]
        ):
            raise ValueError("M2M100 model pack manifest disagrees with the catalog")
        files = manifest["files"]
        if not isinstance(files, list) or files != catalog_component["files"]:
            raise ValueError("M2M100 model pack runtime files disagree with the catalog")
        if not isinstance(manifest["runtime_bytes"], int) or manifest["runtime_bytes"] != catalog_component["runtime_bytes"]:
            raise ValueError("M2M100 model pack runtime byte total is invalid")
        expected_names = required_names | {row["path"] for row in files}
        if set(names) != expected_names:
            raise ValueError("M2M100 model pack entries do not exactly match its manifest")
        notice = archive.read("NOTICE.txt")
        if not notice or hashlib.sha256(notice).hexdigest() != catalog_component["notice_sha256"]:
            raise ValueError("M2M100 model pack notice hash mismatch")
        license_bytes = archive.read("LICENSE.txt")
        if not license_bytes or hashlib.sha256(license_bytes).hexdigest() != catalog_component["license_sha256"]:
            raise ValueError("M2M100 model pack license hash mismatch")
        if verify_payloads:
            for row in files:
                entry = archive.getinfo(row["path"])
                if entry.file_size != row["size"]:
                    raise ValueError("M2M100 model file size mismatch")
                digest = hashlib.sha256()
                with archive.open(entry, "r") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != row["sha256"]:
                    raise ValueError("M2M100 model file SHA256 mismatch")
    return {
        "component": catalog_component,
        "manifest": manifest,
        "pack_path": str(pack_path),
        "pack_size": pack_size,
        "pack_sha256": pack_hash,
    }


def _real_model_root(model_root: Path) -> Path:
    model_root.mkdir(parents=True, exist_ok=True)
    root = model_root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("model root must be a real directory")
    return root


_VERIFIED_COMPONENT_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}


def _component_stat_token(component_root: Path) -> tuple[object, ...]:
    """Cheap, content-free fingerprint used to invalidate the verified cache.

    The token combines the root directory mtime with a recursive listing of
    ``(relative path, size, mtime_ns)`` for every entry below the component.
    Building it only stats the tree; it never reads file contents, so it is
    orders of magnitude cheaper than re-hashing ~1.2 GB of model data.
    """
    entries: list[tuple[str, int, int]] = []
    total = 0
    try:
        for current, dir_names, file_names in os.walk(component_root):
            current_path = Path(current)
            for name in dir_names + file_names:
                child = current_path / name
                stat = child.lstat()
                entries.append(
                    (
                        str(child.relative_to(component_root)),
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
                )
                total += 1
        root_stat = component_root.stat()
    except OSError:
        return ("unreadable", str(component_root))
    return (root_stat.st_mtime_ns, total, tuple(sorted(entries)))


def _invalidate_component_cache(component_root: Path) -> None:
    """Explicitly drop a cached verification result for one component."""
    try:
        key = str(component_root.resolve(strict=True))
    except OSError:
        key = str(component_root)
    _VERIFIED_COMPONENT_CACHE.pop(key, None)


def _cached_load(component_root: Path, loader):
    """Return a verified component record, re-hashing at most once per process.

    The first call for a component performs the full size + SHA-256 check.  The
    result is memoised against a cheap stat token; later calls re-stat the tree
    and only re-verify when the token changed (edit/rename/add/remove).
    """
    try:
        key = str(component_root.resolve(strict=True))
    except OSError:
        return loader(component_root)
    token = _component_stat_token(component_root)
    cached = _VERIFIED_COMPONENT_CACHE.get(key)
    if cached is not None and cached[0] == token:
        return cached[1]
    record = loader(component_root)
    _VERIFIED_COMPONENT_CACHE[key] = (token, record)
    return record


def _load_installed_component_uncached(component_root: Path) -> dict[str, object]:
    component_root = component_root.resolve(strict=True)
    if not component_root.is_dir() or component_root.is_symlink():
        raise ValueError("installed component root is invalid")
    record_path = component_root / "installed.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    required = {"format", "component_id", "pack_size", "pack_sha256", "manifest"}
    if not isinstance(record, dict) or set(record) != required or record["format"] != INSTALL_FORMAT:
        raise ValueError("installed component record is invalid")
    manifest = record["manifest"]
    if not isinstance(manifest, dict) or manifest.get("component_id") != record["component_id"]:
        raise ValueError("installed component manifest is invalid")
    for row in manifest.get("files", []):
        relative = _safe_relative_path(row.get("path"))
        if relative.parts[0] != "model":
            raise ValueError("installed component path is invalid")
        path = component_root.joinpath(*relative.parts)
        resolved = path.resolve(strict=True)
        if path.is_symlink() or component_root not in resolved.parents:
            raise ValueError("installed component file escaped its root")
        if resolved.stat().st_size != row.get("size") or sha256_file(resolved) != row.get("sha256"):
            raise ValueError("installed component file failed verification")
    return record


def load_installed_component(component_root: Path) -> dict[str, object]:
    return _cached_load(component_root, _load_installed_component_uncached)


def installed_components(model_root: Path) -> dict[str, dict[str, object]]:
    root = _real_model_root(model_root)
    result: dict[str, dict[str, object]] = {}
    for path in root.iterdir():
        if path.name.startswith(".") or not path.is_dir() or path.is_symlink():
            continue
        try:
            record = load_installed_component(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if record["component_id"] == path.name:
            result[path.name] = record
    return result


def install_pack(
    pack_path: Path,
    catalog_path: Path,
    model_root: Path,
    *,
    replace: bool = False,
) -> dict[str, object]:
    audited = audit_pack(pack_path, catalog_path)
    component = audited["component"]
    component_id = component["id"]
    root = _real_model_root(model_root)
    present = installed_components(root)
    missing = [dependency for dependency in component["requires"] if dependency not in present]
    if missing:
        raise ValueError("missing required component(s): " + ", ".join(missing))
    target = root / component_id
    if target.exists() and not replace:
        raise FileExistsError(f"component is already installed: {component_id}")
    staging = root / f".staging-{component_id}-{uuid.uuid4().hex}"
    backup = root / f".backup-{component_id}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        with zipfile.ZipFile(audited["pack_path"], "r") as archive:
            for row in audited["manifest"]["files"]:
                relative = _safe_relative_path(row["path"])
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with archive.open(row["path"], "r") as source, destination.open("xb") as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                if written != row["size"] or digest.hexdigest() != row["sha256"]:
                    raise ValueError("model pack changed during installation")
        record = {
            "format": INSTALL_FORMAT,
            "component_id": component_id,
            "pack_size": audited["pack_size"],
            "pack_sha256": audited["pack_sha256"],
            "manifest": audited["manifest"],
        }
        (staging / "installed.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        load_installed_component(staging)
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
        return record
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _load_installed_m2m100_component_uncached(component_root: Path) -> dict[str, object]:
    component_root = component_root.resolve(strict=True)
    if not component_root.is_dir() or component_root.is_symlink():
        raise ValueError("installed M2M100 component root is invalid")
    record_path = component_root / "installed.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    required = {"format", "component_id", "pack_size", "pack_sha256", "manifest"}
    if not isinstance(record, dict) or set(record) != required or record["format"] != M2M100_INSTALL_FORMAT:
        raise ValueError("installed M2M100 component record is invalid")
    manifest = record["manifest"]
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != M2M100_PACK_FORMAT
        or manifest.get("component_id") != record["component_id"]
        or not isinstance(manifest.get("files"), list)
        or not manifest.get("files")
    ):
        raise ValueError("installed M2M100 component manifest is invalid")
    for row in manifest.get("files", []):
        relative = _safe_relative_path(row.get("path"))
        if relative.parts[0] != "model":
            raise ValueError("installed M2M100 component path is invalid")
        path = component_root.joinpath(*relative.parts)
        resolved = path.resolve(strict=True)
        if path.is_symlink() or component_root not in resolved.parents:
            raise ValueError("installed M2M100 component file escaped its root")
        if resolved.stat().st_size != row.get("size") or sha256_file(resolved) != row.get("sha256"):
            raise ValueError("installed M2M100 component file failed verification")
    for name, key in (("LICENSE.txt", "license_sha256"), ("NOTICE.txt", "notice_sha256")):
        path = component_root / name
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get(key):
            raise ValueError("installed M2M100 component license or notice failed verification")
    return record


def load_installed_m2m100_component(component_root: Path) -> dict[str, object]:
    return _cached_load(component_root, _load_installed_m2m100_component_uncached)


def installed_m2m100_components(model_root: Path) -> dict[str, dict[str, object]]:
    root = _real_model_root(model_root)
    result: dict[str, dict[str, object]] = {}
    for path in root.iterdir():
        if path.name.startswith(".") or not path.is_dir() or path.is_symlink():
            continue
        try:
            record = load_installed_m2m100_component(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if record["component_id"] == path.name:
            result[path.name] = record
    return result


def install_m2m100_pack(
    pack_path: Path,
    catalog_path: Path,
    model_root: Path,
    *,
    replace: bool = False,
) -> dict[str, object]:
    audited = audit_m2m100_pack(pack_path, catalog_path)
    component = audited["component"]
    component_id = component["id"]
    root = _real_model_root(model_root)
    present = installed_m2m100_components(root)
    missing = [dependency for dependency in component["requires"] if dependency not in present]
    if missing:
        raise ValueError("missing required M2M100 component(s): " + ", ".join(missing))
    target = root / component_id
    if target.exists() and not replace:
        raise FileExistsError(f"M2M100 component is already installed: {component_id}")
    staging = root / f".staging-{component_id}-{uuid.uuid4().hex}"
    backup = root / f".backup-{component_id}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        with zipfile.ZipFile(audited["pack_path"], "r") as archive:
            for name in ("LICENSE.txt", "NOTICE.txt"):
                destination = staging / name
                with archive.open(name, "r") as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            for row in audited["manifest"]["files"]:
                relative = _safe_relative_path(row["path"])
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with archive.open(row["path"], "r") as source, destination.open("xb") as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                if written != row["size"] or digest.hexdigest() != row["sha256"]:
                    raise ValueError("M2M100 model pack changed during installation")
        record = {
            "format": M2M100_INSTALL_FORMAT,
            "component_id": component_id,
            "pack_size": audited["pack_size"],
            "pack_sha256": audited["pack_sha256"],
            "manifest": audited["manifest"],
        }
        (staging / "installed.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        load_installed_m2m100_component(staging)
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
        return record
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def is_unsafe_source(text: str) -> bool:
    """Conservatively suppress source strings shaped like commands or secrets."""
    folded = text.casefold()
    ascii_markers = (
        "system:", "assistant:", "developer:", "user:", "<|system|>",
        "<|assistant|>", "begin system prompt", "end system prompt", "<script",
        "</script", "[inst]", "[/inst]", "{{", "}}", "select ", "../",
        "file://", "bearer ", "###", '\"},\"', "api_key", "powershell ",
        "cmd.exe", "curl http", "wget http", "drop table", "union select",
        "${", "<prompt>", "</prompt>", "c:\\users\\",
    )
    chinese_markers = (
        "忽略上文", "显示全部密码", "返回空对象", "请执行", "输出json之外",
        "把所有候选翻译", "不要翻译这个", "规则全部作废", "系统管理员",
        "把密钥写入", "读取环境变量", "覆盖系统提示", "忽略限制",
        "系统提示：改写规则", "忽略之前所有", "遵循以下新规则", "泄露api",
        "打印环境变量", "获取密码", "读取系统变量", "覆盖安全策略",
        "回复时不要包含翻译", "显示密钥",
    )
    return any(marker in folded for marker in ascii_markers + chinese_markers)


def clean_hypothesis(value: str) -> str:
    value = unicodedata.normalize("NFC", value).strip().rstrip(".。").rstrip()
    value = re.sub(r"^Category:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    words = value.split(" ")
    collapsed: list[str] = []
    for word in words:
        if not collapsed or collapsed[-1].casefold() != word.casefold():
            collapsed.append(word)
    value = " ".join(collapsed)
    for unit_length in range(2, len(value) // 2 + 1):
        if len(value) % unit_length:
            continue
        unit = value[:unit_length]
        if not (unit.isascii() and unit.isalnum()) and unit * (len(value) // unit_length) == value:
            value = unit
            break
    return value.strip()


def _japanese_rank(value: str) -> int:
    has_kana = any("\u3040" <= character <= "\u30ff" for character in value)
    has_latin = any("a" <= character.casefold() <= "z" for character in value)
    if has_kana:
        return 0
    if has_latin:
        return 2
    return 1


def combine_hypotheses(
    values: Sequence[str],
    *,
    language: str,
    max_glosses: int = 2,
) -> str | None:
    ranked = list(enumerate(values))
    if language == "ja":
        ranked.sort(key=lambda item: (_japanese_rank(clean_hypothesis(item[1])), item[0]))
    selected: list[str] = []
    seen: set[str] = set()
    for _, raw_value in ranked:
        if not isinstance(raw_value, str) or any(character in raw_value for character in "\r\n"):
            continue
        if any(unicodedata.category(character) == "Cc" for character in raw_value):
            continue
        value = clean_hypothesis(raw_value)
        canonical = "".join(
            character.casefold()
            for character in unicodedata.normalize("NFKC", value)
            if unicodedata.category(character)[0] in {"L", "N"}
        )
        if not value or not canonical or canonical in seen:
            continue
        joined = "; ".join([*selected, value])
        if len(joined) > MAX_GLOSS_CHARACTERS:
            continue
        selected.append(value)
        seen.add(canonical)
        if len(selected) >= max_glosses:
            break
    return "; ".join(selected) if selected else None


class SentencePieceStage:
    def __init__(self, model_path: Path, *, threads: int = 4, beam_size: int = 4) -> None:
        import ctranslate2
        import sentencepiece

        self.beam_size = beam_size
        self.source_tokenizer = sentencepiece.SentencePieceProcessor(
            model_file=str(model_path / "src.spm.model")
        )
        self.target_tokenizer = sentencepiece.SentencePieceProcessor(
            model_file=str(model_path / "tgt.spm.model")
        )
        self.translator = ctranslate2.Translator(
            str(model_path),
            device="cpu",
            compute_type="int8",
            inter_threads=1,
            intra_threads=max(1, min(threads, 8)),
        )

    def translate_hypotheses(self, texts: Sequence[str], count: int) -> list[list[str]]:
        encoded = [self.source_tokenizer.encode(text, out_type=str) for text in texts]
        results = self.translator.translate_batch(
            encoded,
            max_batch_size=MAX_WORDS,
            batch_type="examples",
            beam_size=self.beam_size,
            num_hypotheses=count,
            max_input_length=64,
            max_decoding_length=64,
            return_scores=False,
        )
        return [
            [self.target_tokenizer.decode(hypothesis) for hypothesis in result.hypotheses]
            for result in results
        ]

    def translate(self, texts: Sequence[str]) -> list[str]:
        return [row[0] for row in self.translate_hypotheses(texts, 1)]


class QuickMtRuntime:
    def __init__(self, model_root: Path, catalog_path: Path, *, threads: int = 4) -> None:
        self.model_root = _real_model_root(model_root)
        self.catalog = load_pack_catalog(catalog_path)
        self.threads = threads
        self._stages: dict[str, SentencePieceStage] = {}

    def available_languages(self) -> list[str]:
        present = installed_components(self.model_root)
        return sorted(
            language
            for language, component_ids in self.catalog["routes"].items()
            if all(component_id in present for component_id in component_ids)
        )

    def _stage(self, component_id: str) -> SentencePieceStage:
        stage = self._stages.get(component_id)
        if stage is None:
            component_root = self.model_root / component_id
            load_installed_component(component_root)
            stage = SentencePieceStage(component_root / "model", threads=self.threads)
            self._stages[component_id] = stage
        return stage

    def prepare(self, language: str) -> None:
        """Verify and load only the components needed by one target language."""
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported target language")
        for component_id in self.catalog["routes"][language]:
            try:
                load_installed_component(self.model_root / component_id)
            except (OSError, ValueError) as error:
                raise MissingModelComponentError(
                    f"missing or invalid model component: {component_id}"
                ) from error
            self._stage(component_id)

    def translate(self, words: Sequence[str], language: str) -> dict[str, str]:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported target language")
        unique: list[str] = []
        for word in words:
            if (
                not isinstance(word, str)
                or not word
                or len(word.encode("utf-8")) > MAX_WORD_BYTES
                or any(character in word for character in "\r\n\t")
            ):
                raise ValueError("invalid source word")
            if word not in unique:
                unique.append(word)
        if not unique or len(unique) > MAX_WORDS:
            raise ValueError("request must contain one to nine unique words")
        self.prepare(language)
        submitted = [word for word in unique if not is_unsafe_source(word)]
        if not submitted:
            return {}
        route = self.catalog["routes"][language]
        values = submitted
        for component_id in route[:-1]:
            values = self._stage(component_id).translate(values)
        rows = self._stage(route[-1]).translate_hypotheses(values, 4)
        result: dict[str, str] = {}
        for source, hypotheses in zip(submitted, rows, strict=True):
            gloss = combine_hypotheses(hypotheses, language=language)
            if gloss:
                result[source] = gloss
        return result


class MissingModelComponentError(FileNotFoundError):
    """The selected model pack is not installed or failed its integrity check."""


class MissingModelRuntimeError(FileNotFoundError):
    """The selected model's local inference runtime is not installed."""


class LocalModelRequestError(RuntimeError):
    """The selected local inference runtime could not answer a request."""


M2M100_LANGUAGE_CODES = (
    "af", "am", "ar", "ast", "az", "ba", "be", "bg", "bn", "br", "bs",
    "ca", "ceb", "cs", "cy", "da", "de", "el", "en", "es", "et", "fa",
    "ff", "fi", "fr", "fy", "ga", "gd", "gl", "gu", "ha", "he", "hi",
    "hr", "ht", "hu", "hy", "id", "ig", "ilo", "is", "it", "ja", "jv",
    "ka", "kk", "km", "kn", "ko", "lb", "lg", "ln", "lo", "lt", "lv", "mg",
    "mk", "ml", "mn", "mr", "ms", "my", "ne", "nl", "no", "ns", "oc", "or",
    "pa", "pl", "ps", "pt", "ro", "ru", "sd", "si", "sk", "sl", "so", "sq",
    "sr", "ss", "su", "sv", "sw", "ta", "th", "tl", "tn", "tr", "uk", "ur",
    "uz", "vi", "wo", "xh", "yi", "yo", "zh", "zu",
)


class M2m100Tokenizer:
    """Minimal offline tokenizer compatible with the official M2M100 tokenizer."""

    def __init__(self, model_path: Path) -> None:
        try:
            import sentencepiece
        except ImportError as error:
            raise MissingModelRuntimeError("SentencePiece runtime is not installed") from error
        try:
            vocabulary = json.loads((model_path / "vocab.json").read_text(encoding="utf-8"))
            shared_vocabulary = json.loads(
                (model_path / "shared_vocabulary.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MissingModelComponentError("installed M2M100 tokenizer files are unreadable") from error
        if (
            not isinstance(vocabulary, dict)
            or vocabulary.get("<unk>") != 3
            or not isinstance(shared_vocabulary, list)
            or len(vocabulary) != 128004
            or len(shared_vocabulary) != 128112
        ):
            raise MissingModelComponentError("installed M2M100 tokenizer metadata is invalid")
        try:
            self.sentencepiece = sentencepiece.SentencePieceProcessor(
                model_file=str(model_path / "sentencepiece.bpe.model")
            )
        except (OSError, RuntimeError) as error:
            raise MissingModelComponentError("installed M2M100 SentencePiece model is invalid") from error
        self.language_tokens = {language: f"__{language}__" for language in M2M100_LANGUAGE_CODES}

    def encode(self, text: str) -> list[str]:
        return [
            self.language_tokens["zh"],
            *self.sentencepiece.encode(text, out_type=str),
            "</s>",
        ]

    def target_prefix(self, language: str) -> str:
        try:
            return self.language_tokens[language]
        except KeyError as error:
            raise ValueError("unsupported target language") from error

    def decode(self, tokens: Sequence[str]) -> str:
        pieces: list[str] = []
        for token in tokens:
            if token in {"</s>", "<pad>"}:
                break
            if token.startswith("__") and token.endswith("__"):
                continue
            pieces.append(token)
        try:
            return self.sentencepiece.decode(pieces)
        except (RuntimeError, TypeError) as error:
            raise LocalModelRequestError("M2M100 returned invalid token output") from error


class M2m100Stage:
    def __init__(
        self,
        model_root: Path,
        threads: int = 4,
    ) -> None:
        self.model_root = _real_model_root(model_root)
        try:
            import ctranslate2
        except ImportError as error:
            raise MissingModelRuntimeError("CTranslate2 runtime is not installed") from error
        component_root = self.model_root
        load_installed_m2m100_component(component_root)
        model_path = component_root / "model"
        self.tokenizer = M2m100Tokenizer(model_path)
        try:
            self.translator = ctranslate2.Translator(
                str(model_path),
                device="cpu",
                compute_type="int8",
                inter_threads=1,
                intra_threads=max(1, min(threads, 8)),
            )
        except (OSError, RuntimeError) as error:
            raise MissingModelComponentError("installed M2M100 CTranslate2 model is invalid") from error

    def translate_hypotheses(
        self,
        texts: Sequence[str],
        language: str,
        *,
        num_hypotheses: int = 1,
    ) -> list[list[str]]:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported target language")
        encoded = [self.tokenizer.encode(text) for text in texts]
        target_prefix = [[self.tokenizer.target_prefix(language)] for _ in texts]
        results = self.translator.translate_batch(
            encoded,
            target_prefix=target_prefix,
            max_batch_size=MAX_WORDS,
            batch_type="examples",
            beam_size=4,
            num_hypotheses=num_hypotheses,
            max_input_length=64,
            max_decoding_length=64,
            return_scores=False,
        )
        return [
            [self.tokenizer.decode(hypothesis) for hypothesis in result.hypotheses]
            for result in results
        ]


class M2m100Runtime:
    def __init__(self, model_root: Path, catalog_path: Path, *, threads: int = 4) -> None:
        self.model_root = _real_model_root(model_root)
        self.catalog = load_m2m100_pack_catalog(catalog_path)
        self.threads = threads
        self._stage: M2m100Stage | None = None

    def available_languages(self) -> list[str]:
        present = installed_m2m100_components(self.model_root)
        return sorted(
            language
            for language, component_ids in self.catalog["routes"].items()
            if all(component_id in present for component_id in component_ids)
        )

    def _stage_for(self, language: str) -> M2m100Stage:
        route = self.catalog["routes"].get(language)
        if not isinstance(route, list) or len(route) != 1:
            raise ValueError("M2M100 route is invalid")
        present = installed_m2m100_components(self.model_root)
        missing = [component_id for component_id in route if component_id not in present]
        if missing:
            raise MissingModelComponentError(
                "missing required M2M100 component(s): " + ", ".join(missing)
            )
        component_root = self.model_root / route[0]
        if self._stage is None:
            self._stage = M2m100Stage(component_root, threads=self.threads)
        return self._stage

    def translate(self, words: Sequence[str], language: str) -> dict[str, str]:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported target language")
        unique: list[str] = []
        for word in words:
            if (
                not isinstance(word, str)
                or not word
                or len(word.encode("utf-8")) > MAX_WORD_BYTES
                or any(character in word for character in "\r\n\t")
            ):
                raise ValueError("invalid source word")
            if word not in unique:
                unique.append(word)
        if not unique or len(unique) > MAX_WORDS:
            raise ValueError("request must contain one to nine unique words")
        submitted = [word for word in unique if not is_unsafe_source(word)]
        if not submitted:
            return {}
        stage = self._stage_for(language)
        rows = stage.translate_hypotheses(submitted, language, num_hypotheses=1)
        result: dict[str, str] = {}
        for source, hypotheses in zip(submitted, rows, strict=True):
            gloss = combine_hypotheses(hypotheses, language=language, max_glosses=1)
            if gloss:
                result[source] = gloss
        return result

    def close(self) -> None:
        self._stage = None


def _parse_translation_request(document: object) -> tuple[str, list[str], str]:
    if not isinstance(document, dict):
        raise ValueError("request must be an object")
    messages = document.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("request has no messages")
    user_messages = [row for row in messages if isinstance(row, dict) and row.get("role") == "user"]
    if len(user_messages) != 1 or not isinstance(user_messages[0].get("content"), str):
        raise ValueError("request must have exactly one user message")
    payload = json.loads(user_messages[0]["content"])
    if not isinstance(payload, dict) or not set(payload).issubset(
        {"target_language", "words", "model"}
    ) or not {"target_language", "words"}.issubset(payload):
        raise ValueError("translation payload has unexpected keys")
    language = payload["target_language"]
    words = payload["words"]
    model = payload.get("model", QUICKMT_MODEL_ID)
    if (
        language not in SUPPORTED_LANGUAGES
        or not isinstance(words, list)
        or not isinstance(model, str)
        or model not in SUPPORTED_MODELS
    ):
        raise ValueError("translation payload is invalid")
    return language, words, model


class ModelHostServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        token: str,
        quickmt: QuickMtRuntime,
        m2m100: M2m100Runtime | None,
    ) -> None:
        super().__init__(address, ModelHostHandler)
        self.token = token
        self.quickmt = quickmt
        self.m2m100 = m2m100
        self.last_request = time.monotonic()

    def close_runtimes(self) -> None:
        if self.m2m100 is not None:
            self.m2m100.close()


class ModelHostHandler(BaseHTTPRequestHandler):
    server: ModelHostServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.token
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))

    def _json(self, status: int, document: object) -> None:
        body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path != "/health":
            self._json(404, {"error": "not-found"})
            return
        self.server.last_request = time.monotonic()
        quickmt_languages = self.server.quickmt.available_languages()
        m2m100_languages = (
            self.server.m2m100.available_languages() if self.server.m2m100 else []
        )
        self._json(
            200,
            {
                "status": "ok",
                "languages": quickmt_languages,
                "models": {
                    QUICKMT_MODEL_ID: {"languages": quickmt_languages},
                    M2M100_MODEL_ID: {"languages": m2m100_languages},
                },
            },
        )

    def do_POST(self) -> None:
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path not in {"/v1/chat/completions", "/warmup"}:
            self._json(404, {"error": "not-found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json(413, {"error": "invalid-request-size"})
            return
        self.server.last_request = time.monotonic()
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/warmup":
                if (
                    not isinstance(request, dict)
                    or set(request) != {"language", "model"}
                    or request["model"] != QUICKMT_MODEL_ID
                    or not isinstance(request["language"], str)
                    or request["language"] not in SUPPORTED_LANGUAGES
                ):
                    raise ValueError("invalid warmup request")
                self.server.quickmt.prepare(request["language"])
                self.server.last_request = time.monotonic()
                self._json(200, {"status": "ready"})
                return
            language, words, model = _parse_translation_request(request)
            if model == M2M100_MODEL_ID:
                if self.server.m2m100 is None:
                    raise MissingModelRuntimeError("M2M100 runtime is not installed")
                result = self.server.m2m100.translate(words, language)
            else:
                result = self.server.quickmt.translate(words, language)
        except MissingModelComponentError as error:
            self._json(409, {"error": "missing-model-component", "detail": str(error)})
            self.server.last_request = time.monotonic()
            return
        except MissingModelRuntimeError as error:
            self._json(409, {"error": "missing-model-runtime", "detail": str(error)})
            self.server.last_request = time.monotonic()
            return
        except FileNotFoundError as error:
            self._json(409, {"error": "missing-model-component", "detail": str(error)})
            self.server.last_request = time.monotonic()
            return
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._json(400, {"error": "invalid-request"})
            self.server.last_request = time.monotonic()
            return
        except LocalModelRequestError as error:
            self._json(502, {"error": "local-model-failed", "detail": str(error)})
            self.server.last_request = time.monotonic()
            return
        except Exception:
            self._json(500, {"error": "translation-failed"})
            self.server.last_request = time.monotonic()
            return
        self.server.last_request = time.monotonic()
        content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        self._json(
            200,
            {
                "id": "language-input-local",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            },
        )


def serve(
    model_root: Path,
    catalog_path: Path,
    *,
    m2m100_catalog_path: Path | None,
    port: int,
    token: str,
    idle_seconds: int,
    threads: int,
) -> None:
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ValueError("host token must contain at least 32 non-whitespace characters")
    quickmt = QuickMtRuntime(model_root, catalog_path, threads=threads)
    m2m100 = None
    if m2m100_catalog_path is not None and m2m100_catalog_path.is_file():
        m2m100 = M2m100Runtime(model_root, m2m100_catalog_path, threads=threads)
    server = ModelHostServer(("127.0.0.1", port), token, quickmt, m2m100)
    server.timeout = 1.0
    print(f"READY {server.server_port}", flush=True)
    try:
        while time.monotonic() - server.last_request < idle_seconds:
            server.handle_request()
    finally:
        server.close_runtimes()
        server.server_close()


def default_model_root() -> Path:
    """Match WeaselUserDataPath without requiring the native Weasel runtime."""
    if os.name != "nt":
        return Path.home() / ".local" / "share" / "Rime" / "language_input" / "models"
    import winreg

    user_dir: str | None = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Rime\Weasel") as key:
            value, value_type = winreg.QueryValueEx(key, "RimeUserDir")
            if value_type == winreg.REG_SZ and isinstance(value, str) and value:
                user_dir = os.path.expandvars(value)
    except OSError:
        pass
    if not user_dir:
        app_data = os.environ.get("APPDATA")
        if not app_data:
            raise RuntimeError("APPDATA is unavailable")
        user_dir = str(Path(app_data) / "Rime")
    return Path(user_dir) / "language_input" / "models"


def _pack_manifest_format(pack_path: Path) -> str | None:
    try:
        with zipfile.ZipFile(pack_path, "r") as archive:
            manifest = _read_small_archive_json(archive, "manifest.json")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError):
        return None
    value = manifest.get("format")
    return value if isinstance(value, str) else None


def manage_models(
    catalog_path: Path,
    model_root: Path,
    m2m100_catalog_path: Path | None = None,
) -> int:
    if os.name != "nt":
        raise RuntimeError("the graphical model importer is available only on Windows")
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    message_box = ctypes.windll.user32.MessageBoxW
    selected = ctypes.create_unicode_buffer(32768)
    dialog = OPENFILENAMEW()
    dialog.lStructSize = ctypes.sizeof(dialog)
    dialog.lpstrFilter = "Language Input model package (*.limodel)\0*.limodel\0All files\0*.*\0\0"
    dialog.lpstrFile = ctypes.cast(selected, wintypes.LPWSTR)
    dialog.nMaxFile = len(selected)
    dialog.lpstrTitle = "Import a Language Input AI language pack"
    dialog.lpstrDefExt = "limodel"
    dialog.Flags = 0x00001000 | 0x00000800 | 0x00080000  # FILEMUSTEXIST, PATHMUSTEXIST, EXPLORER
    if not ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(dialog)):
        return 0
    pack_path = Path(selected.value)
    m2m100_pack = _pack_manifest_format(pack_path) == M2M100_PACK_FORMAT
    try:
        if m2m100_pack:
            if m2m100_catalog_path is None:
                raise ValueError("M2M100 catalog is not installed")
            record = install_m2m100_pack(pack_path, m2m100_catalog_path, model_root)
        else:
            record = install_pack(pack_path, catalog_path, model_root)
    except FileExistsError:
        answer = message_box(
            None,
            "This language component is already installed. Verify and replace it?",
            "Language Input AI language packs",
            0x00000004 | 0x00000020,
        )
        if answer != 6:
            return 0
        try:
            if m2m100_pack:
                if m2m100_catalog_path is None:
                    raise ValueError("M2M100 catalog is not installed")
                record = install_m2m100_pack(
                    pack_path, m2m100_catalog_path, model_root, replace=True
                )
            else:
                record = install_pack(pack_path, catalog_path, model_root, replace=True)
        except Exception as error:
            message_box(None, str(error), "Language pack import failed", 0x00000010)
            return 1
    except Exception as error:
        message_box(None, str(error), "Language pack import failed", 0x00000010)
        return 1
    message_box(
        None,
        f"Installed {record['component_id']}. Restart Weasel before using it.",
        "Language pack installed",
        0x00000040,
    )
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--m2m100-catalog", type=Path)
    parser.add_argument("--models", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--audit-pack", type=Path)
    actions.add_argument("--import-pack", type=Path)
    actions.add_argument("--list", action="store_true")
    actions.add_argument("--serve", action="store_true")
    actions.add_argument("--manage", action="store_true")
    parser.add_argument(
        "--m2m100",
        action="store_true",
        help="use the independent M2M100/MIT pack catalog for audit/import/list",
    )
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token")
    parser.add_argument("--idle-seconds", type=int, default=600)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.audit_pack:
        if args.m2m100:
            if args.m2m100_catalog is None:
                raise ValueError("--m2m100-catalog is required for --m2m100")
            result = audit_m2m100_pack(args.audit_pack, args.m2m100_catalog)
        else:
            result = audit_pack(args.audit_pack, args.catalog)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    if args.manage and args.models is None:
        args.models = default_model_root()
    if args.models is None:
        raise ValueError("--models is required for this action")
    if args.import_pack:
        if args.m2m100:
            if args.m2m100_catalog is None:
                raise ValueError("--m2m100-catalog is required for --m2m100")
            result = install_m2m100_pack(
                args.import_pack,
                args.m2m100_catalog,
                args.models,
                replace=args.replace,
            )
        else:
            result = install_pack(args.import_pack, args.catalog, args.models, replace=args.replace)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.list:
        result = (
            installed_m2m100_components(args.models)
            if args.m2m100
            else installed_components(args.models)
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.manage:
        return manage_models(args.catalog, args.models, args.m2m100_catalog)
    if args.serve:
        if not args.token or not 0 <= args.port <= 65535 or args.idle_seconds < 1:
            raise ValueError("--serve requires a valid --token, --port and idle timeout")
        serve(
            args.models,
            args.catalog,
            m2m100_catalog_path=args.m2m100_catalog,
            port=args.port,
            token=args.token,
            idle_seconds=args.idle_seconds,
            threads=args.threads,
        )
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
