#!/usr/bin/env python3
"""CLI parity harness: ``--list`` / ``--audit-pack`` / ``--import-pack``.

Runs the currently installed host and the patched host with the same CLI
arguments and compares the JSON they print.  ``--import-pack`` writes into two
separate TEMP model roots; the live model root is never modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from ab import DEFAULT_CATALOG, DEFAULT_CURRENT, DEFAULT_M2M100_CATALOG, DEFAULT_MODELS, DEFAULT_PATCHED

DEFAULT_PACK = Path(
    r"F:\documents\.i-wish-research\language-input-ai-models-20260826"
    r"\artifacts\quickmt-limodel-v2\quickmt-zh-en-c27cc802.limodel"
)
WORK = Path(__file__).resolve().parent.parent / "build" / "cli-parity"


def run(exe: Path, args: Sequence[str], timeout: int = 900) -> dict[str, Any]:
    completed = subprocess.run(
        [str(exe), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    stdout = (completed.stdout or "").strip()
    parsed = None
    for line in reversed(stdout.splitlines()):
        try:
            parsed = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    return {
        "exit": completed.returncode,
        "stdout": stdout,
        "stderr": (completed.stderr or "").strip(),
        "parsed": parsed,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-exe", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--patched-exe", type=Path, default=DEFAULT_PATCHED)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--m2m100-catalog", type=Path, default=DEFAULT_M2M100_CATALOG)
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    args = parser.parse_args(argv)

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    checks: list[tuple[str, bool, str]] = []

    # --- --list on the live root -------------------------------------------
    current_list = run(args.current_exe, ["--catalog", str(args.catalog), "--models", str(args.models), "--list"])
    patched_list = run(args.patched_exe, ["--catalog", str(args.catalog), "--models", str(args.models), "--list"])
    checks.append(
        (
            "--list JSON identical",
            current_list["parsed"] == patched_list["parsed"] and current_list["exit"] == patched_list["exit"] == 0,
            f"current_exit={current_list['exit']} patched_exit={patched_list['exit']}",
        )
    )

    # --- --audit-pack -------------------------------------------------------
    current_audit = run(args.current_exe, ["--catalog", str(args.catalog), "--audit-pack", str(args.pack)])
    patched_audit = run(args.patched_exe, ["--catalog", str(args.catalog), "--audit-pack", str(args.pack)])
    checks.append(
        (
            "--audit-pack JSON identical",
            current_audit["parsed"] == patched_audit["parsed"]
            and current_audit["exit"] == patched_audit["exit"] == 0,
            f"current_exit={current_audit['exit']} patched_exit={patched_audit['exit']}",
        )
    )

    # --- --import-pack into two TEMP roots ---------------------------------
    roots = {"current": WORK / "current-models", "patched": WORK / "patched-models"}
    for name, root in roots.items():
        root.mkdir(parents=True)
    current_import = run(
        args.current_exe,
        ["--catalog", str(args.catalog), "--models", str(roots["current"]), "--import-pack", str(args.pack)],
    )
    patched_import = run(
        args.patched_exe,
        ["--catalog", str(args.catalog), "--models", str(roots["patched"]), "--import-pack", str(args.pack)],
    )
    checks.append(
        (
            "--import-pack JSON identical",
            current_import["parsed"] == patched_import["parsed"]
            and current_import["exit"] == patched_import["exit"] == 0,
            f"current_exit={current_import['exit']} patched_exit={patched_import['exit']}",
        )
    )
    current_record = (roots["current"] / "quickmt-zh-en" / "installed.json").read_bytes()
    patched_record = (roots["patched"] / "quickmt-zh-en" / "installed.json").read_bytes()
    checks.append(
        (
            "--import-pack installed.json byte-identical",
            current_record == patched_record,
            f"bytes current={len(current_record)} patched={len(patched_record)}",
        )
    )

    ok = True
    for name, passed, detail in checks:
        ok = ok and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name} ({detail})")

    (WORK / "results.json").write_text(
        json.dumps(
            {
                "list": {"current": current_list, "patched": patched_list},
                "audit": {"current": current_audit, "patched": patched_audit},
                "import": {"current": current_import, "patched": patched_import},
                "ok": ok,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    shutil.rmtree(roots["current"])
    shutil.rmtree(roots["patched"])
    print("cli parity:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
