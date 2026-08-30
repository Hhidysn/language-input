#!/usr/bin/env python3
"""Build the independent MIT/CTranslate2 M2M100 Language Input pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence


PACK_FORMAT = "language-input-m2m100-model-pack-v1"
FIXED_ZIP_TIME = (2026, 8, 31, 0, 0, 0)
MAX_COMPONENT_BYTES = 2 * 1024 * 1024 * 1024
RUNTIME_FILES = (
    "config.json",
    "model.bin",
    "sentencepiece.bpe.model",
    "shared_vocabulary.json",
    "vocab.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe component path: {value!r}")
    return path


def real_file(path: Path, description: str) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or path.is_symlink():
        raise ValueError(f"{description} must be a real file")
    return resolved


def load_input(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "component_id",
        "display_name",
        "repository",
        "revision",
        "license",
        "provides",
        "requires",
        "source",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("M2M100 pack input has unexpected or missing keys")
    source = document["source"]
    if (
        document["schema_version"] != 1
        or not isinstance(document["component_id"], str)
        or not document["component_id"]
        or not isinstance(document["display_name"], str)
        or not document["display_name"]
        or document["repository"] != "facebook/m2m100_418M"
        or not isinstance(document["revision"], str)
        or len(document["revision"]) != 40
        or any(char not in "0123456789abcdef" for char in document["revision"])
        or document["license"] != "MIT"
        or not isinstance(document["provides"], list)
        or not document["provides"]
        or any(not isinstance(value, str) for value in document["provides"])
        or not isinstance(document["requires"], list)
        or any(not isinstance(value, str) for value in document["requires"])
        or not isinstance(source, dict)
        or set(source) != {"file_name", "size", "sha256"}
        or source["file_name"] != "pytorch_model.bin"
        or not isinstance(source["size"], int)
        or source["size"] <= 0
        or source["size"] > MAX_COMPONENT_BYTES
        or not isinstance(source["sha256"], str)
        or len(source["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in source["sha256"])
    ):
        raise ValueError("M2M100 pack input metadata is invalid")
    return document


def zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 0
    info.external_attr = 0
    return info


def file_rows(model_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in RUNTIME_FILES:
        path = real_file(model_root / name, f"M2M100 runtime file {name}")
        size = path.stat().st_size
        if size <= 0 or size > MAX_COMPONENT_BYTES:
            raise ValueError(f"M2M100 runtime file has an invalid size: {name}")
        rows.append(
            {
                "path": f"model/{name}",
                "size": size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def build_pack(
    *,
    input_path: Path,
    model_root: Path,
    license_path: Path,
    notice_path: Path,
    output_path: Path,
) -> dict[str, object]:
    pack_input = load_input(input_path)
    model_root = model_root.resolve(strict=True)
    if not model_root.is_dir() or model_root.is_symlink():
        raise ValueError("M2M100 model root must be a real directory")
    license_file = real_file(license_path, "MIT license")
    notice_file = real_file(notice_path, "NOTICE")
    license_bytes = license_file.read_bytes()
    notice_bytes = notice_file.read_bytes()
    if not license_bytes or len(license_bytes) > 1024 * 1024:
        raise ValueError("license file is empty or too large")
    if not notice_bytes or len(notice_bytes) > 1024 * 1024:
        raise ValueError("NOTICE file is empty or too large")
    rows = file_rows(model_root)
    manifest = {
        "format": PACK_FORMAT,
        "component_id": pack_input["component_id"],
        "repository": pack_input["repository"],
        "revision": pack_input["revision"],
        "license": pack_input["license"],
        "license_sha256": sha256_bytes(license_bytes),
        "notice_sha256": sha256_bytes(notice_bytes),
        "provides": pack_input["provides"],
        "requires": pack_input["requires"],
        "runtime_bytes": sum(int(row["size"]) for row in rows),
        "files": rows,
        "source": pack_input["source"],
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    output = output_path.resolve()
    if output.suffix.casefold() != ".limodel":
        raise ValueError("output must use the .limodel extension")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            archive.writestr(zip_info("manifest.json"), manifest_bytes)
            archive.writestr(zip_info("NOTICE.txt"), notice_bytes)
            archive.writestr(zip_info("LICENSE.txt"), license_bytes)
            for row in rows:
                name = str(row["path"]).split("/", 1)[1]
                info = zip_info(str(row["path"]))
                with (model_root / name).open("rb") as source, archive.open(
                    info, "w"
                ) as destination:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        destination.write(chunk)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "component_id": pack_input["component_id"],
        "output": str(output),
        "bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "manifest": manifest,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--license", required=True, type=Path)
    parser.add_argument("--notice", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else os.sys.argv[1:])
    result = build_pack(
        input_path=args.input,
        model_root=args.model_root,
        license_path=args.license,
        notice_path=args.notice,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
