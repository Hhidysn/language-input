#!/usr/bin/env python3
"""Build and audit a reproducible Language Input .limodel component pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence


PACK_FORMAT = "language-input-model-pack-v1"
FIXED_ZIP_TIME = (2026, 8, 29, 0, 0, 0)
MAX_COMPONENT_BYTES = 2 * 1024 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe component path: {value!r}")
    return path


def load_component_manifest(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "component_id",
        "repository",
        "revision",
        "license",
        "provides",
        "requires",
        "runtime_bytes",
        "files",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("component manifest has unexpected or missing keys")
    if document["schema_version"] != 1 or document["license"] != "CC-BY-4.0":
        raise ValueError("unsupported component manifest version or license")
    if not isinstance(document["files"], list) or not document["files"]:
        raise ValueError("component manifest has no files")
    if not isinstance(document["provides"], list) or not document["provides"]:
        raise ValueError("component manifest has no provided language")
    if not isinstance(document["requires"], list):
        raise ValueError("component manifest requires must be a list")
    seen: set[PurePosixPath] = set()
    total = 0
    for row in document["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
            raise ValueError("component file row is invalid")
        relative = validate_relative_path(row["path"])
        if relative in seen:
            raise ValueError(f"duplicate component path: {relative}")
        seen.add(relative)
        if not isinstance(row["size"], int) or row["size"] < 0:
            raise ValueError("component file size is invalid")
        if (
            not isinstance(row["sha256"], str)
            or len(row["sha256"]) != 64
            or any(ch not in "0123456789abcdef" for ch in row["sha256"])
        ):
            raise ValueError("component file SHA256 is invalid")
        total += row["size"]
    if total != document["runtime_bytes"] or total > MAX_COMPONENT_BYTES:
        raise ValueError("component runtime byte total is invalid")
    return document


def verified_component_files(
    manifest: dict[str, object], component_root: Path
) -> list[tuple[dict[str, object], Path]]:
    root = component_root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("component root must be a real directory")
    verified: list[tuple[dict[str, object], Path]] = []
    for row in manifest["files"]:
        relative = validate_relative_path(row["path"])
        source = root.joinpath(*relative.parts)
        resolved = source.resolve(strict=True)
        if source.is_symlink() or root not in resolved.parents:
            raise ValueError(f"component file escaped its root: {relative}")
        if not resolved.is_file() or resolved.stat().st_size != row["size"]:
            raise ValueError(f"component file size mismatch: {relative}")
        if sha256_file(resolved) != row["sha256"]:
            raise ValueError(f"component file SHA256 mismatch: {relative}")
        verified.append((row, resolved))
    return verified


def zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 0
    info.external_attr = 0
    return info


def build_pack(
    *,
    component_manifest_path: Path,
    component_root: Path,
    notice_path: Path,
    output_path: Path,
) -> dict[str, object]:
    manifest = load_component_manifest(component_manifest_path)
    files = verified_component_files(manifest, component_root)
    notice = notice_path.read_bytes()
    if not notice or len(notice) > 1024 * 1024:
        raise ValueError("notice file is empty or too large")
    output_path = output_path.resolve()
    if output_path.suffix.lower() != ".limodel":
        raise ValueError("output must use the .limodel extension")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pack_manifest = {
        "format": PACK_FORMAT,
        "component_id": manifest["component_id"],
        "repository": manifest["repository"],
        "revision": manifest["revision"],
        "license": manifest["license"],
        "provides": manifest["provides"],
        "requires": manifest["requires"],
        "runtime_bytes": manifest["runtime_bytes"],
        "notice_sha256": hashlib.sha256(notice).hexdigest(),
        "files": [
            {
                "path": f"model/{row['path']}",
                "size": row["size"],
                "sha256": row["sha256"],
            }
            for row, _ in files
        ],
    }
    manifest_bytes = (
        json.dumps(pack_manifest, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")

    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            archive.writestr(zip_info("manifest.json"), manifest_bytes)
            archive.writestr(zip_info("NOTICE.txt"), notice)
            for row, source in files:
                info = zip_info(f"model/{row['path']}")
                with source.open("rb") as input_stream, archive.open(info, "w") as output:
                    for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                        output.write(chunk)
        audit_pack(temporary, expected_manifest=pack_manifest)
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "component_id": manifest["component_id"],
        "output": str(output_path),
        "bytes": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
        "manifest": pack_manifest,
    }


def audit_pack(path: Path, *, expected_manifest: dict[str, object] | None = None) -> dict[str, object]:
    with zipfile.ZipFile(path, "r") as archive:
        names = [entry.filename for entry in archive.infolist()]
        if len(names) != len(set(names)):
            raise ValueError("pack contains duplicate entries")
        if any(entry.compress_type != zipfile.ZIP_STORED for entry in archive.infolist()):
            raise ValueError("pack contains a compressed entry")
        if "manifest.json" not in names or "NOTICE.txt" not in names:
            raise ValueError("pack is missing manifest or notice")
        for name in names:
            validate_relative_path(name)
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != PACK_FORMAT:
            raise ValueError("pack manifest format is invalid")
        expected_names = {"manifest.json", "NOTICE.txt"} | {
            row["path"] for row in manifest["files"]
        }
        if set(names) != expected_names:
            raise ValueError("pack entries do not exactly match its manifest")
        if hashlib.sha256(archive.read("NOTICE.txt")).hexdigest() != manifest["notice_sha256"]:
            raise ValueError("pack notice hash mismatch")
        for row in manifest["files"]:
            payload = archive.read(row["path"])
            if len(payload) != row["size"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
                raise ValueError(f"pack file verification failed: {row['path']}")
        if expected_manifest is not None and manifest != expected_manifest:
            raise ValueError("pack manifest differs from the expected manifest")
        return manifest


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component-manifest", required=True, type=Path)
    parser.add_argument("--component-root", required=True, type=Path)
    parser.add_argument("--notice", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else os.sys.argv[1:])
    result = build_pack(
        component_manifest_path=args.component_manifest,
        component_root=args.component_root,
        notice_path=args.notice,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
