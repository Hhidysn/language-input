#!/usr/bin/env python3
"""Unit-level proof of the in-tree verification cache.

Imports the in-tree source (``scripts/language_input_model_host.py``) as a
module and drives it with a synthetic component in a TEMP directory, counting
real ``sha256_file`` calls:

* first load verifies (hashes once);
* second load is served from cache (no hashing);
* touching a file changes the stat token and forces re-verification;
* a same-size tamper is still rejected;
* ``_invalidate_component_cache`` forces re-verification.

No real model data is read or written.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent.parent / "scripts" / "language_input_model_host.py"
WORK = HERE.parent / "build" / "cache-test"


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("patched_host", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_component(root: Path, component_id: str, payload: bytes) -> Path:
    component = root / component_id
    (component / "model").mkdir(parents=True)
    (component / "model" / "data.bin").write_bytes(payload)
    record = {
        "format": "language-input-installed-component-v1",
        "component_id": component_id,
        "pack_size": len(payload),
        "pack_sha256": hashlib.sha256(payload).hexdigest(),
        "manifest": {
            "component_id": component_id,
            "files": [
                {
                    "path": "model/data.bin",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ]
        },
    }
    (component / "installed.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return component


def main(argv: Sequence[str] | None = None) -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    module = load_module(SOURCE)

    calls = {"count": 0}
    original_sha256_file = module.sha256_file

    def counting_sha256_file(path: Path) -> str:
        calls["count"] += 1
        return original_sha256_file(path)

    module.sha256_file = counting_sha256_file

    component = make_component(WORK, "comp-x", b"hello-model-bytes")

    checks: list[tuple[str, bool, str]] = []

    before = calls["count"]
    module.load_installed_component(component)
    first = calls["count"] - before
    checks.append(("first load verifies (hashes once)", first == 1, f"hash calls={first}"))

    before = calls["count"]
    module.load_installed_component(component)
    cached = calls["count"] - before
    checks.append(("second load served from cache (no hash)", cached == 0, f"hash calls={cached}"))

    before = calls["count"]
    module.installed_components(WORK)
    aggregate = calls["count"] - before
    checks.append(("installed_components from cache (no hash)", aggregate == 0, f"hash calls={aggregate}"))

    # Same-size tamper -> token changes (mtime/size listing) -> re-verify -> reject.
    data_path = component / "model" / "data.bin"
    original = data_path.read_bytes()
    tampered = bytearray(original)
    tampered[0] ^= 0x01
    data_path.write_bytes(bytes(tampered))
    before = calls["count"]
    rejected = False
    try:
        module.load_installed_component(component)
    except ValueError as error:
        rejected = "failed verification" in str(error)
    rehash = calls["count"] - before
    checks.append(("same-size tamper re-verifies and is rejected", rejected and rehash >= 1, f"hash calls={rehash}"))

    # Restore, then explicitly invalidate -> forces a fresh verification.
    data_path.write_bytes(original)
    module.load_installed_component(component)
    before = calls["count"]
    module._invalidate_component_cache(component)
    module.load_installed_component(component)
    explicit = calls["count"] - before
    checks.append(("explicit invalidation re-verifies", explicit == 1, f"hash calls={explicit}"))

    ok = True
    for name, passed, detail in checks:
        ok = ok and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name} ({detail})")

    shutil.rmtree(WORK)
    print("cache expectations:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
