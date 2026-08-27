#!/usr/bin/env python3
"""Validate, compare and unblind two Language Input semantic reviews."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from build_semantic_review_set import REVIEW_FORMAT, atomic_write_json, canonical_sha256


SUMMARY_FORMAT = "language-input-semantic-review-summary-v1"
ADJUDICATION_FORMAT = "language-input-semantic-adjudication-v1"
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
SEMANTIC_THRESHOLDS = {"en": 0.90, "ja": 0.85, "es": 0.85}


def load_json_object(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON root must be an object in {path}")
    return document


def _validate_decision(
    decision: object,
    *,
    expected_id: str | None = None,
) -> dict[str, object]:
    if not isinstance(decision, dict) or set(decision) != {
        "review_id",
        "accept",
        "issue",
        "note",
    }:
        raise ValueError(f"invalid semantic decision: {decision!r}")
    review_id = decision["review_id"]
    accept = decision["accept"]
    issue = decision["issue"]
    note = decision["note"]
    if not isinstance(review_id, str) or not review_id:
        raise ValueError("semantic decision has invalid review_id")
    if expected_id is not None and review_id != expected_id:
        raise ValueError(f"expected decision {expected_id}, found {review_id}")
    if type(accept) is not bool or issue not in ALLOWED_ISSUES:
        raise ValueError(f"semantic decision {review_id} has invalid accept/issue")
    if accept != (issue == "ok"):
        raise ValueError(f"semantic decision {review_id} accept/issue disagree")
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError(f"semantic decision {review_id} has invalid note")
    return dict(decision)


def load_reviewer(
    paths: Sequence[Path],
    *,
    reviewer: str,
    expected_ids: set[str],
) -> dict[str, dict[str, object]]:
    decisions: dict[str, dict[str, object]] = {}
    if not paths:
        raise ValueError(f"reviewer {reviewer} has no review files")
    for path in paths:
        document = load_json_object(path)
        if document.get("reviewer") != reviewer:
            raise ValueError(f"reviewer mismatch in {path}")
        if set(document) == {
            "reviewer",
            "reviewed_count",
            "first_id",
            "last_id",
            "rejects",
        }:
            ordered_expected = sorted(expected_ids)
            first_id = document["first_id"]
            last_id = document["last_id"]
            if first_id not in expected_ids or last_id not in expected_ids:
                raise ValueError("condensed reviewer range is outside the package")
            first_index = ordered_expected.index(first_id)
            last_index = ordered_expected.index(last_id)
            ordered_ids = ordered_expected[first_index : last_index + 1]
            if (
                document["reviewed_count"] != len(ordered_ids)
                or not ordered_ids
                or first_id != ordered_ids[0]
                or last_id != ordered_ids[-1]
            ):
                raise ValueError("condensed reviewer coverage declaration is invalid")
            rejects = document["rejects"]
            if not isinstance(rejects, list):
                raise ValueError("condensed reviewer rejects must be a list")
            overlap = set(ordered_ids) & set(decisions)
            if overlap:
                raise ValueError(
                    f"duplicate condensed reviewer coverage: {sorted(overlap)[:10]}"
                )
            decisions.update({
                review_id: {
                    "review_id": review_id,
                    "accept": True,
                    "issue": "ok",
                    "note": "",
                }
                for review_id in ordered_ids
            })
            seen_rejects: set[str] = set()
            for row in rejects:
                if not isinstance(row, dict) or set(row) != {
                    "review_id",
                    "issue",
                    "note",
                }:
                    raise ValueError("invalid condensed reviewer rejection")
                review_id = row["review_id"]
                issue = row["issue"]
                note = row["note"]
                if (
                    review_id not in expected_ids
                    or review_id in seen_rejects
                    or issue not in ALLOWED_ISSUES - {"ok"}
                    or not isinstance(note, str)
                    or not note
                    or len(note) > 500
                ):
                    raise ValueError(f"invalid condensed reviewer rejection {row!r}")
                seen_rejects.add(review_id)
                decisions[review_id] = {
                    "review_id": review_id,
                    "accept": False,
                    "issue": issue,
                    "note": note,
                }
            continue
        rows = document.get("decisions")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"review file has no decisions: {path}")
        for row in rows:
            checked = _validate_decision(row)
            review_id = str(checked["review_id"])
            if review_id in decisions:
                raise ValueError(f"duplicate reviewer {reviewer} decision {review_id}")
            decisions[review_id] = checked
    missing = expected_ids - set(decisions)
    extra = set(decisions) - expected_ids
    if missing or extra:
        raise ValueError(
            f"reviewer {reviewer} coverage mismatch: "
            f"missing={sorted(missing)[:10]} extra={sorted(extra)[:10]}"
        )
    return decisions


def validate_package_and_key(
    package: dict[str, object],
    key: dict[str, object],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, str]]]:
    if package.get("format") != REVIEW_FORMAT or key.get("format") != REVIEW_FORMAT:
        raise ValueError("semantic package or key has the wrong format")
    if key.get("package_sha256") != canonical_sha256(package):
        raise ValueError("semantic package SHA256 does not match the key")
    items = package.get("items")
    mapping = key.get("mapping")
    if not isinstance(items, list) or not isinstance(mapping, list):
        raise ValueError("semantic package or key is missing rows")
    items_by_id: dict[str, dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("semantic package item is not an object")
        review_id = item.get("review_id")
        if not isinstance(review_id, str) or review_id in items_by_id:
            raise ValueError(f"invalid or duplicate semantic review id {review_id!r}")
        items_by_id[review_id] = dict(item)
    mapping_by_id: dict[str, dict[str, str]] = {}
    for row in mapping:
        if not isinstance(row, dict) or set(row) != {
            "review_id",
            "model_id",
            "source_id",
            "language",
        }:
            raise ValueError("invalid semantic key mapping row")
        if not all(isinstance(value, str) and value for value in row.values()):
            raise ValueError("semantic key mapping contains an invalid value")
        review_id = row["review_id"]
        if review_id in mapping_by_id:
            raise ValueError(f"duplicate semantic key mapping {review_id}")
        mapping_by_id[review_id] = dict(row)
    if set(items_by_id) != set(mapping_by_id):
        raise ValueError("semantic package and key id sets differ")
    for review_id, item in items_by_id.items():
        mapping_row = mapping_by_id[review_id]
        if (
            item.get("source_id") != mapping_row["source_id"]
            or item.get("target_language") != mapping_row["language"]
        ):
            raise ValueError(f"semantic key metadata mismatch for {review_id}")
    return items_by_id, mapping_by_id


def build_disagreement_package(
    package: Mapping[str, object],
    items_by_id: Mapping[str, Mapping[str, object]],
    review_a: Mapping[str, Mapping[str, object]],
    review_b: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for review_id in sorted(items_by_id):
        a = review_a[review_id]
        b = review_b[review_id]
        if a["accept"] == b["accept"]:
            continue
        item = items_by_id[review_id]
        rows.append(
            {
                "review_id": review_id,
                "source_id": item["source_id"],
                "source": item["source"],
                "category": item["category"],
                "target_language": item["target_language"],
                "output": item["output"],
                "review_a": a,
                "review_b": b,
            }
        )
    result = {
        "format": ADJUDICATION_FORMAT,
        "package_sha256": canonical_sha256(package),
        "item_count": len(rows),
        "instructions": {
            "blind": "Do not inspect model mappings while adjudicating.",
            "references": "Every adjudication decision requires at least one stable bilingual reference URL.",
            "decision_keys": [
                "review_id",
                "accept",
                "issue",
                "note",
                "reference_urls",
            ],
        },
        "items": rows,
    }
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if "model_id" in serialized:
        raise AssertionError("model identity leaked into the disagreement package")
    return result


def load_adjudication(
    path: Path,
    *,
    package_sha256: str,
    disagreement_ids: set[str],
) -> dict[str, dict[str, object]]:
    document = load_json_object(path)
    if document.get("format") != ADJUDICATION_FORMAT:
        raise ValueError("adjudication has the wrong format")
    if document.get("package_sha256") != package_sha256:
        raise ValueError("adjudication package SHA256 mismatch")
    rows = document.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("adjudication decisions must be a list")
    decisions: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "review_id",
            "accept",
            "issue",
            "note",
            "reference_urls",
        }:
            raise ValueError("invalid adjudication decision")
        semantic = _validate_decision(
            {key: row[key] for key in ("review_id", "accept", "issue", "note")}
        )
        urls = row["reference_urls"]
        if (
            not isinstance(urls, list)
            or not urls
            or not all(isinstance(url, str) and url.startswith("https://") for url in urls)
        ):
            raise ValueError(f"adjudication {semantic['review_id']} lacks HTTPS references")
        semantic["reference_urls"] = list(urls)
        review_id = str(semantic["review_id"])
        if review_id in decisions:
            raise ValueError(f"duplicate adjudication decision {review_id}")
        decisions[review_id] = semantic
    if set(decisions) != disagreement_ids:
        raise ValueError("adjudication must cover exactly every reviewer disagreement")
    return decisions


def _dimension_summary(
    decisions: Mapping[str, Mapping[str, object]],
    items_by_id: Mapping[str, Mapping[str, object]],
    mapping_by_id: Mapping[str, Mapping[str, str]],
) -> dict[str, object]:
    counts: dict[str, dict[str, Counter[str]]] = {}
    categories: dict[str, Counter[str]] = {}
    for review_id, decision in decisions.items():
        mapping = mapping_by_id[review_id]
        model_id = mapping["model_id"]
        language = mapping["language"]
        category = str(items_by_id[review_id]["category"])
        counts.setdefault(model_id, {}).setdefault(language, Counter())["attempted"] += 1
        categories.setdefault(model_id, Counter())[f"{category}:attempted"] += 1
        if decision["accept"]:
            counts[model_id][language]["accepted"] += 1
            categories[model_id][f"{category}:accepted"] += 1
    result: dict[str, object] = {}
    for model_id in sorted(counts):
        language_rows: dict[str, object] = {}
        for language in sorted(counts[model_id]):
            attempted = counts[model_id][language]["attempted"]
            accepted = counts[model_id][language]["accepted"]
            language_rows[language] = {
                "attempted": attempted,
                "accepted": accepted,
                "acceptance": accepted / attempted,
            }
        category_rows: dict[str, object] = {}
        for category in sorted({key.split(":", 1)[0] for key in categories[model_id]}):
            attempted = categories[model_id][f"{category}:attempted"]
            accepted = categories[model_id][f"{category}:accepted"]
            category_rows[category] = {
                "attempted": attempted,
                "accepted": accepted,
                "acceptance": accepted / attempted,
            }
        result[model_id] = {
            "languages": language_rows,
            "categories": category_rows,
        }
    return result


def summarize_reviews(
    package: Mapping[str, object],
    items_by_id: Mapping[str, Mapping[str, object]],
    mapping_by_id: Mapping[str, Mapping[str, str]],
    review_a: Mapping[str, Mapping[str, object]],
    review_b: Mapping[str, Mapping[str, object]],
    *,
    adjudication: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    ids = sorted(items_by_id)
    agreement = sum(review_a[row]["accept"] == review_b[row]["accept"] for row in ids)
    a_yes = sum(bool(review_a[row]["accept"]) for row in ids) / len(ids)
    b_yes = sum(bool(review_b[row]["accept"]) for row in ids) / len(ids)
    observed = agreement / len(ids)
    expected = a_yes * b_yes + (1 - a_yes) * (1 - b_yes)
    kappa = (observed - expected) / (1 - expected) if not math.isclose(expected, 1.0) else 1.0
    disagreement_ids = {
        row for row in ids if review_a[row]["accept"] != review_b[row]["accept"]
    }
    final: dict[str, dict[str, object]] | None = None
    final_gate: dict[str, object] | None = None
    if adjudication is not None:
        if set(adjudication) != disagreement_ids:
            raise ValueError("adjudication id set does not match disagreements")
        final = {}
        for review_id in ids:
            if review_id in disagreement_ids:
                final[review_id] = dict(adjudication[review_id])
            else:
                final[review_id] = dict(review_a[review_id])
        dimensions = _dimension_summary(final, items_by_id, mapping_by_id)
        final_gate = {}
        for model_id, model_row in dimensions.items():
            languages = model_row["languages"]
            language_pass = {
                language: row["acceptance"] >= SEMANTIC_THRESHOLDS[language]
                for language, row in languages.items()
            }
            final_gate[model_id] = {
                "languages": language_pass,
                "passed": all(language_pass.values()),
            }
    else:
        dimensions = None
    return {
        "format": SUMMARY_FORMAT,
        "package_sha256": canonical_sha256(package),
        "reviewed_items": len(ids),
        "agreement": {
            "items": agreement,
            "rate": observed,
            "disagreements": len(disagreement_ids),
            "cohen_kappa": kappa,
        },
        "reviewer_a": {
            "accepted": int(a_yes * len(ids)),
            "dimensions": _dimension_summary(review_a, items_by_id, mapping_by_id),
            "issue_counts": dict(sorted(Counter(str(row["issue"]) for row in review_a.values()).items())),
        },
        "reviewer_b": {
            "accepted": int(b_yes * len(ids)),
            "dimensions": _dimension_summary(review_b, items_by_id, mapping_by_id),
            "issue_counts": dict(sorted(Counter(str(row["issue"]) for row in review_b.values()).items())),
        },
        "adjudicated": adjudication is not None,
        "final_dimensions": dimensions,
        "semantic_gate": final_gate,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--review-a", required=True, action="append", type=Path)
    parser.add_argument("--review-b", required=True, action="append", type=Path)
    parser.add_argument("--disagreements", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--adjudication", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    package = load_json_object(args.package)
    key = load_json_object(args.key)
    items_by_id, mapping_by_id = validate_package_and_key(package, key)
    expected_ids = set(items_by_id)
    review_a = load_reviewer(args.review_a, reviewer="A", expected_ids=expected_ids)
    review_b = load_reviewer(args.review_b, reviewer="B", expected_ids=expected_ids)
    disagreements = build_disagreement_package(package, items_by_id, review_a, review_b)
    atomic_write_json(args.disagreements, disagreements)
    adjudication = None
    if args.adjudication:
        adjudication = load_adjudication(
            args.adjudication,
            package_sha256=canonical_sha256(package),
            disagreement_ids={str(row["review_id"]) for row in disagreements["items"]},
        )
    summary = summarize_reviews(
        package,
        items_by_id,
        mapping_by_id,
        review_a,
        review_b,
        adjudication=adjudication,
    )
    atomic_write_json(args.summary, summary)
    print(
        json.dumps(
            {
                "reviewed_items": summary["reviewed_items"],
                "disagreements": summary["agreement"]["disagreements"],
                "agreement": summary["agreement"]["rate"],
                "cohen_kappa": summary["agreement"]["cohen_kappa"],
                "adjudicated": summary["adjudicated"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
