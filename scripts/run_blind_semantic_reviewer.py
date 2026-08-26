#!/usr/bin/env python3
"""Run one fresh, pure OpenCode semantic-review session and validate its JSON."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ALLOWED_ISSUES = {
    "ok",
    "meaning",
    "target_language",
    "injection",
    "hallucination",
    "missing",
    "verbosity",
    "other",
}


def atomic_write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def parse_event_stream(output: str) -> dict[str, object]:
    text_parts: list[str] = []
    for line_number, raw_line in enumerate(output.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid OpenCode event JSON on line {line_number}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"OpenCode event {line_number} is not an object")
        if event.get("type") == "text":
            part = event.get("part")
            text = part.get("text") if isinstance(part, dict) else None
            if not isinstance(text, str):
                raise ValueError(f"OpenCode text event {line_number} has no text")
            text_parts.append(text)
    if not text_parts:
        raise ValueError("OpenCode returned no text event")
    reviewer_text = "".join(text_parts).strip()
    if reviewer_text.startswith("```json\n") and reviewer_text.endswith("\n```"):
        reviewer_text = reviewer_text[len("```json\n") : -len("\n```")]
    try:
        document = json.loads(reviewer_text)
    except json.JSONDecodeError as exc:
        preview = reviewer_text[:240].replace("\r", "\\r").replace("\n", "\\n")
        raise ValueError(f"reviewer text is not strict JSON; prefix={preview!r}") from exc
    if not isinstance(document, dict):
        raise ValueError("reviewer response root must be an object")
    return document


def validate_review(
    review: dict[str, object],
    chunk: dict[str, object],
    *,
    reviewer: str,
) -> dict[str, object]:
    chunk_index = chunk.get("chunk_index")
    items = chunk.get("items")
    if not isinstance(chunk_index, int) or not isinstance(items, list) or not items:
        raise ValueError("invalid review chunk")
    if set(review) != {"reviewer", "chunk_index", "decisions"}:
        raise ValueError("review response has wrong top-level keys")
    if review["reviewer"] != reviewer or review["chunk_index"] != chunk_index:
        raise ValueError("reviewer or chunk index mismatch")
    decisions = review.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(items):
        raise ValueError(
            f"review decision count mismatch: expected {len(items)}, "
            f"found {len(decisions) if isinstance(decisions, list) else 'non-list'}"
        )
    expected_ids = [item.get("review_id") for item in items if isinstance(item, dict)]
    if len(expected_ids) != len(items) or not all(
        isinstance(review_id, str) for review_id in expected_ids
    ):
        raise ValueError("review chunk has invalid review ids")
    checked: list[dict[str, object]] = []
    for expected_id, decision in zip(expected_ids, decisions, strict=True):
        if not isinstance(decision, dict) or set(decision) != {
            "review_id",
            "accept",
            "issue",
            "note",
        }:
            raise ValueError(f"decision for {expected_id} has wrong keys")
        if decision["review_id"] != expected_id:
            raise ValueError(
                f"decision order/id mismatch: expected {expected_id}, "
                f"found {decision['review_id']!r}"
            )
        accept = decision["accept"]
        issue = decision["issue"]
        note = decision["note"]
        if type(accept) is not bool:
            raise ValueError(f"decision {expected_id} accept is not boolean")
        if issue not in ALLOWED_ISSUES:
            raise ValueError(f"decision {expected_id} has invalid issue {issue!r}")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError(f"decision {expected_id} has invalid note")
        if accept != (issue == "ok"):
            raise ValueError(f"decision {expected_id} accept/issue disagree")
        checked.append(dict(decision))
    return {
        "reviewer": reviewer,
        "chunk_index": chunk_index,
        "decisions": checked,
    }


def validate_condensed_review(
    review: dict[str, object],
    chunk: dict[str, object],
    *,
    reviewer: str,
) -> dict[str, object]:
    items = chunk.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("invalid review chunk")
    expected_ids = [item.get("review_id") for item in items if isinstance(item, dict)]
    if len(expected_ids) != len(items) or not all(
        isinstance(review_id, str) for review_id in expected_ids
    ):
        raise ValueError("review chunk has invalid review ids")
    if set(review) != {
        "reviewer",
        "reviewed_count",
        "first_id",
        "last_id",
        "rejects",
    }:
        raise ValueError("condensed review response has wrong top-level keys")
    if (
        review["reviewer"] != reviewer
        or review["reviewed_count"] != len(expected_ids)
        or review["first_id"] != expected_ids[0]
        or review["last_id"] != expected_ids[-1]
    ):
        raise ValueError("condensed reviewer coverage declaration is invalid")
    rejects = review.get("rejects")
    if not isinstance(rejects, list):
        raise ValueError("condensed reviewer rejects must be a list")
    expected_set = set(expected_ids)
    seen: set[str] = set()
    checked: list[dict[str, object]] = []
    for rejection in rejects:
        if not isinstance(rejection, dict) or set(rejection) != {
            "review_id",
            "issue",
            "note",
        }:
            raise ValueError("condensed rejection has wrong keys")
        review_id = rejection["review_id"]
        issue = rejection["issue"]
        note = rejection["note"]
        if (
            review_id not in expected_set
            or review_id in seen
            or issue not in ALLOWED_ISSUES - {"ok"}
            or not isinstance(note, str)
            or not note
            or len(note) > 500
        ):
            raise ValueError(f"invalid condensed rejection {rejection!r}")
        seen.add(str(review_id))
        checked.append(dict(rejection))
    return {
        "reviewer": reviewer,
        "reviewed_count": len(expected_ids),
        "first_id": expected_ids[0],
        "last_id": expected_ids[-1],
        "rejects": checked,
    }


def reviewer_prompt(reviewer: str) -> str:
    return (
        f"You are independent semantic reviewer {reviewer} for a Chinese "
        "input-method translation benchmark. The attached JSON is the only "
        "evidence you may use. Do not infer, search for, or mention model "
        "identities. Apply its rubric independently to every item. Return ONLY "
        "one strict JSON object with keys reviewer, chunk_index, decisions. "
        f"The reviewer value must be {reviewer}. The chunk_index value must "
        "match the attachment. The decisions array must contain exactly one "
        "object for every attached item, in attachment order, with keys "
        "review_id, accept, issue, note. accept must be a JSON boolean. issue "
        "must be one allowed rubric issue and must be ok exactly when accept is "
        "true. note must be concise and evidence-based; use an empty string for "
        "clear accepts. Do not use Markdown or omit any item."
    )


def condensed_reviewer_prompt(reviewer: str) -> str:
    return (
        f"You are independent semantic reviewer {reviewer} for a Chinese "
        "input-method translation benchmark. The attached JSON is the only "
        "evidence you may use. Do not infer, search for, or mention model "
        "identities. Apply its rubric independently to every item, including "
        "items you accept. Return ONLY one strict JSON object with keys "
        "reviewer, reviewed_count, first_id, last_id, rejects. The reviewer "
        f"value must be {reviewer}. reviewed_count must equal the number of "
        "attached items; first_id and last_id must copy the first and last "
        "attached review_id. Omit accepted items. rejects must contain exactly "
        "every rejected item in attachment order, each with keys review_id, "
        "issue, note. issue must be a non-ok rubric issue and note must give "
        "concise evidence. Do not use Markdown."
    )


def resolve_opencode_executable() -> Path:
    direct = shutil.which("opencode.exe")
    if direct:
        return Path(direct).resolve(strict=True)
    shim = shutil.which("opencode.cmd")
    if not shim:
        raise FileNotFoundError("opencode executable or npm shim was not found on PATH")
    npm_root = Path(shim).resolve(strict=True).parent
    executable = npm_root / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    return executable.resolve(strict=True)


def run_reviewer(
    *,
    model: str,
    reviewer: str,
    chunk_path: Path,
    timeout_seconds: int,
    condensed: bool = False,
) -> dict[str, object]:
    chunk_path = chunk_path.resolve(strict=True)
    chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
    if not isinstance(chunk, dict):
        raise ValueError("review chunk root must be an object")
    chunk_index = chunk.get("chunk_index")
    title = f"Language Input blind review {reviewer} chunk {chunk_index:02d}"
    command = [
        str(resolve_opencode_executable()),
        "run",
        "--pure",
        "--model",
        model,
        "--title",
        title,
        "--format",
        "json",
        condensed_reviewer_prompt(reviewer) if condensed else reviewer_prompt(reviewer),
        "--file",
        str(chunk_path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(
            f"OpenCode reviewer exited with {completed.returncode}: {stderr[:1000]}"
        )
    review = parse_event_stream(completed.stdout)
    return (
        validate_condensed_review(review, chunk, reviewer=reviewer)
        if condensed
        else validate_review(review, chunk, reviewer=reviewer)
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--chunk", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--condensed", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not args.reviewer or len(args.reviewer) > 16 or not args.reviewer.isascii():
        raise ValueError("reviewer must be a short ASCII label")
    review = run_reviewer(
        model=args.model,
        reviewer=args.reviewer,
        chunk_path=args.chunk,
        timeout_seconds=args.timeout_seconds,
        condensed=args.condensed,
    )
    atomic_write_json(args.output, review)
    print(
        json.dumps(
            {
                "reviewer": review["reviewer"],
                "chunk_index": review.get("chunk_index"),
                "decisions": (
                    review["reviewed_count"]
                    if args.condensed
                    else len(review["decisions"])
                ),
                "accepted": (
                    int(review["reviewed_count"]) - len(review["rejects"])
                    if args.condensed
                    else sum(
                        bool(decision["accept"])
                        for decision in review["decisions"]
                    )
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
