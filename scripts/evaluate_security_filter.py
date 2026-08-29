#!/usr/bin/env python3
"""Evaluate the deterministic pre-inference source safety filter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping, Sequence

from benchmark_local_model import atomic_write_json, is_unsafe_source


FORMAT = "language-input-model-safety-v1"


def load_rows(path: Path) -> list[dict[str, str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != FORMAT:
        raise ValueError(f"unsupported safety fixture format in {path}")
    rows = document.get("entries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("safety fixture needs entries")
    checked: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "text", "expected"}:
            raise ValueError("safety row has unexpected or missing keys")
        if not all(isinstance(row[key], str) and row[key] for key in row):
            raise ValueError("safety row contains an empty non-string field")
        if row["expected"] not in {"suppress", "translate"}:
            raise ValueError(f"invalid safety expectation: {row['expected']!r}")
        if row["id"] in seen:
            raise ValueError(f"duplicate safety id: {row['id']}")
        seen.add(row["id"])
        checked.append(dict(row))
    return checked


def evaluate_rows(rows: Sequence[Mapping[str, str]]) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for row in rows:
        actual = "suppress" if is_unsafe_source(row["text"]) else "translate"
        results.append(
            {
                "id": row["id"],
                "expected": row["expected"],
                "actual": actual,
                "passed": actual == row["expected"],
            }
        )
    unsafe = [row for row in results if row["expected"] == "suppress"]
    safe = [row for row in results if row["expected"] == "translate"]
    failures = [row for row in results if not row["passed"]]
    return {
        "format": FORMAT,
        "entries": len(results),
        "unsafe_entries": len(unsafe),
        "unsafe_suppressed": sum(bool(row["passed"]) for row in unsafe),
        "unsafe_recall": (
            sum(bool(row["passed"]) for row in unsafe) / len(unsafe) if unsafe else 0.0
        ),
        "safe_entries": len(safe),
        "safe_allowed": sum(bool(row["passed"]) for row in safe),
        "safe_specificity": (
            sum(bool(row["passed"]) for row in safe) / len(safe) if safe else 0.0
        ),
        "passed": not failures and bool(unsafe) and bool(safe),
        "failures": failures,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    summary = evaluate_rows(load_rows(args.fixture))
    atomic_write_json(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
