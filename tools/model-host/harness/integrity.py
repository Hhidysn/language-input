#!/usr/bin/env python3
"""Integrity-enforcement harness.

Copies a real installed component (quickmt-zh-en) into a TEMP model root and
runs both the currently installed host and the patched host against it in four
states:

* valid              -> component listed
* corrupted (same size, changed bytes) -> component rejected
* oversized (extra byte)              -> component rejected
* missing file                        -> component rejected

It also launches the patched host against the corrupted root and confirms
``/health`` reports no available languages.  The live model root is only copied
from; it is never written to.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
from ab import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_CURRENT,
    DEFAULT_M2M100_CATALOG,
    DEFAULT_MODELS,
    DEFAULT_PATCHED,
    http_get,
    wait_ready,
)

DEFAULT_WORK = Path(__file__).resolve().parent.parent / "build" / "integrity"
SOURCE_COMPONENT = "quickmt-zh-en"
TARGET_FILE = Path("model") / "config.json"


def run_list(exe: Path, catalog: Path, models: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(exe), "--catalog", str(catalog), "--models", str(models), "--list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    stdout = (completed.stdout or "").strip()
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = None
    return {"exit": completed.returncode, "stdout": stdout, "parsed": parsed}


def run_health(exe: Path, catalog: Path, m2m100: Path, models: Path, port: int) -> dict[str, Any]:
    command = [
        str(exe),
        "--serve",
        "--catalog",
        str(catalog),
        "--models",
        str(models),
        "--port",
        str(port),
        "--token",
        "b" * 64,
        "--idle-seconds",
        "5",
    ]
    if m2m100.is_file():
        command += ["--m2m100-catalog", str(m2m100)]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(f"http://127.0.0.1:{port}", "b" * 64, process, timeout=60)
        status, body, _ = http_get(f"http://127.0.0.1:{port}/health", "b" * 64)
        return {"status": status, "json": json.loads(body.decode("utf-8"))}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-exe", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--patched-exe", type=Path, default=DEFAULT_PATCHED)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--m2m100-catalog", type=Path, default=DEFAULT_M2M100_CATALOG)
    parser.add_argument("--live-models", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    source = args.live_models / SOURCE_COMPONENT
    if not source.is_dir():
        raise SystemExit(f"source component not found: {source}")

    root = args.work / "models"
    component = root / SOURCE_COMPONENT
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    print(f"copying {source} -> {component}")
    shutil.copytree(source, component)
    target = component / TARGET_FILE
    original = target.read_bytes()

    hosts = [("current", args.current_exe), ("patched", args.patched_exe)]
    cases = ["valid", "corrupted", "oversized", "missing"]
    results: dict[str, Any] = {}

    def apply_case(case: str) -> None:
        target.write_bytes(original)
        if case == "corrupted":
            data = bytearray(original)
            data[len(data) // 2] ^= 0x01
            target.write_bytes(bytes(data))
        elif case == "oversized":
            target.write_bytes(original + b"X")
        elif case == "missing":
            target.unlink()

    for case in cases:
        apply_case(case)
        record: dict[str, Any] = {}
        for host_name, exe in hosts:
            listed = run_list(exe, args.catalog, root)
            present = SOURCE_COMPONENT in (listed["parsed"] or {})
            record[host_name] = {"exit": listed["exit"], "present": present}
        results[case] = record
        print(f"case {case:<10}: " + ", ".join(
            f"{h}: present={record[h]['present']} exit={record[h]['exit']}" for h in ("current", "patched")
        ))

    # /health must not advertise a language whose component is corrupt.
    apply_case("corrupted")
    health = run_health(args.patched_exe, args.catalog, args.m2m100_catalog, root, 51236)
    languages = health["json"].get("languages")
    quickmt_languages = health["json"].get("models", {}).get("quickmt-gloss-route-v2", {}).get("languages")
    print(f"patched /health on corrupted root: status={health['status']} languages={languages} quickmt={quickmt_languages}")

    expectations = {"valid": True, "corrupted": False, "oversized": False, "missing": False}
    ok = all(
        results[case][host]["present"] is expectations[case]
        for case in cases
        for host in ("current", "patched")
    )
    ok = ok and languages == [] and quickmt_languages == []

    out = args.work / "results.json"
    out.write_text(
        json.dumps({**results, "health_corrupted": health, "ok": ok}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("integrity expectation:", "PASS" if ok else "FAIL")
    shutil.rmtree(root)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
