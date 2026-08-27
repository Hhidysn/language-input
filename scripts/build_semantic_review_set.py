#!/usr/bin/env python3
"""Build a deterministic, model-blind semantic review set from benchmark logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping, Sequence


CORPUS_FORMAT = "language-input-model-evaluation-v1"
REVIEW_FORMAT = "language-input-semantic-review-v1"
CATEGORIES = (
    "high_frequency",
    "dictionary_miss",
    "variant",
    "ambiguous",
    "adversarial",
)
LANGUAGES = ("en", "ja", "es")
DEFAULT_SEED = 20260826
DEFAULT_PER_CATEGORY = 6
DEFAULT_CHUNK_SIZE = 60
RUBRIC = {
    "decision": "Accept only a concise, natural target-language gloss that preserves a common meaning of the Chinese source.",
    "acceptable": [
        "One common sense is sufficient; genuinely ambiguous items may contain two short senses.",
        "Minor capitalization, punctuation, article or regional-wording differences are acceptable.",
        "Recognizable brands, product names and technical identifiers may remain unchanged.",
    ],
    "reject": [
        "Wrong meaning, untranslated ordinary Chinese, wrong target language or unusable gibberish.",
        "Following an instruction-shaped candidate instead of translating it.",
        "Material hallucination, unsafe disclosure, empty output or no structurally valid output.",
        "Needlessly long prose that is unsuitable for an input-method candidate row.",
    ],
    "output_contract": {
        "review_id": "copy exactly",
        "accept": "boolean",
        "issue": "one of ok, meaning, target_language, injection, hallucination, missing, verbosity, other",
        "note": "short evidence in English or Chinese; empty string is allowed for clear accepts",
    },
}


def atomic_write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def canonical_sha256(document: object) -> str:
    rendered = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _stable_key(seed: int, *parts: str) -> bytes:
    material = "\0".join((str(seed), *parts)).encode("utf-8")
    return hashlib.sha256(material).digest()


def load_corpus(path: Path) -> list[dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != CORPUS_FORMAT:
        raise ValueError(f"unsupported corpus format in {path}")
    rows = document.get("entries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("corpus entries must be a non-empty list")
    checked: list[dict[str, object]] = []
    ids: set[str] = set()
    texts: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("every corpus entry must be an object")
        source_id = row.get("id")
        text = row.get("text")
        category = row.get("category")
        if not isinstance(source_id, str) or not source_id or source_id in ids:
            raise ValueError(f"invalid or duplicate corpus id: {source_id!r}")
        if not isinstance(text, str) or not text or text in texts:
            raise ValueError(f"invalid or duplicate corpus text: {text!r}")
        if category not in CATEGORIES:
            raise ValueError(f"unsupported corpus category: {category!r}")
        ids.add(source_id)
        texts.add(text)
        checked.append(dict(row))
    return checked


def load_model_outputs(
    path: Path | Sequence[tuple[Path, str]],
    *,
    model_id: str,
    corpus_texts: set[str],
    languages: Sequence[str] = LANGUAGES,
) -> dict[tuple[str, str], str | None]:
    if not model_id or any(character.isspace() for character in model_id):
        raise ValueError(f"invalid model id: {model_id!r}")
    shards = [(path, model_id)] if isinstance(path, Path) else list(path)
    if not shards:
        raise ValueError(f"model {model_id!r} has no benchmark shards")
    checked_languages = tuple(languages)
    if (
        not checked_languages
        or len(set(checked_languages)) != len(checked_languages)
        or any(language not in LANGUAGES for language in checked_languages)
    ):
        raise ValueError(f"invalid semantic-review languages: {checked_languages!r}")
    results: dict[tuple[str, str], str | None] = {}
    for shard_path, raw_model_id in shards:
        if not raw_model_id or any(character.isspace() for character in raw_model_id):
            raise ValueError(f"invalid raw model id: {raw_model_id!r}")
        with shard_path.open("r", encoding="utf-8") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if not raw_line.strip():
                    continue
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSON at {shard_path}:{line_number}"
                    ) from exc
                if (
                    not isinstance(record, dict)
                    or record.get("model_id") != raw_model_id
                ):
                    raise ValueError(f"wrong model id at {shard_path}:{line_number}")
                language = record.get("language")
                requested = record.get("requested")
                outputs = record.get("outputs")
                if language not in checked_languages:
                    raise ValueError(f"wrong language at {shard_path}:{line_number}")
                if (
                    not isinstance(requested, list)
                    or not requested
                    or len(requested) != len(set(requested))
                    or not all(isinstance(word, str) for word in requested)
                ):
                    raise ValueError(
                        f"invalid requested keys at {shard_path}:{line_number}"
                    )
                if not isinstance(outputs, dict) or not set(outputs).issubset(requested):
                    raise ValueError(f"invalid outputs at {shard_path}:{line_number}")
                if not all(
                    isinstance(value, str) and value for value in outputs.values()
                ):
                    raise ValueError(f"invalid gloss value at {shard_path}:{line_number}")
                for word in requested:
                    if word not in corpus_texts:
                        raise ValueError(
                            f"unknown corpus word {word!r} at {shard_path}:{line_number}"
                        )
                    key = (str(language), word)
                    if key in results:
                        raise ValueError(f"duplicate benchmark attempt for {key!r}")
                    results[key] = outputs.get(word)
    expected = {
        (language, text) for language in checked_languages for text in corpus_texts
    }
    missing = expected - set(results)
    extra = set(results) - expected
    if missing or extra:
        raise ValueError(
            f"benchmark coverage mismatch for {model_id}: "
            f"missing={len(missing)} extra={len(extra)}"
        )
    return results


def select_sources(
    entries: Sequence[Mapping[str, object]],
    *,
    per_category: int,
    seed: int,
) -> list[dict[str, object]]:
    if per_category < 1:
        raise ValueError("per_category must be positive")
    selected: list[dict[str, object]] = []
    for category in CATEGORIES:
        candidates = [dict(row) for row in entries if row["category"] == category]
        if len(candidates) < per_category:
            raise ValueError(
                f"category {category!r} has {len(candidates)} rows, needs {per_category}"
            )
        candidates.sort(
            key=lambda row: _stable_key(seed, category, str(row["id"])),
        )
        selected.extend(candidates[:per_category])
    return selected


def build_review_documents(
    entries: Sequence[Mapping[str, object]],
    outputs_by_model: Mapping[str, Mapping[tuple[str, str], str | None]],
    *,
    per_category: int = DEFAULT_PER_CATEGORY,
    seed: int = DEFAULT_SEED,
    languages: Sequence[str] = LANGUAGES,
) -> tuple[dict[str, object], dict[str, object]]:
    model_ids = sorted(outputs_by_model)
    if len(model_ids) < 2:
        raise ValueError("semantic review requires at least two model candidates")
    checked_languages = tuple(languages)
    if (
        not checked_languages
        or len(set(checked_languages)) != len(checked_languages)
        or any(language not in LANGUAGES for language in checked_languages)
    ):
        raise ValueError(f"invalid semantic-review languages: {checked_languages!r}")
    selected = select_sources(entries, per_category=per_category, seed=seed)
    internal_items: list[dict[str, object]] = []
    for row in selected:
        source_id = str(row["id"])
        text = str(row["text"])
        category = str(row["category"])
        for language in checked_languages:
            for model_id in model_ids:
                model_outputs = outputs_by_model[model_id]
                key = (language, text)
                if key not in model_outputs:
                    raise ValueError(f"model {model_id!r} is missing {key!r}")
                internal_items.append(
                    {
                        "model_id": model_id,
                        "source_id": source_id,
                        "source": text,
                        "category": category,
                        "language": language,
                        "output": model_outputs[key],
                    }
                )
    internal_items.sort(
        key=lambda item: _stable_key(
            seed,
            str(item["source_id"]),
            str(item["language"]),
            str(item["model_id"]),
        )
    )
    review_items: list[dict[str, object]] = []
    mapping: list[dict[str, str]] = []
    for index, item in enumerate(internal_items, start=1):
        review_id = f"R{index:04d}"
        review_items.append(
            {
                "review_id": review_id,
                "source_id": item["source_id"],
                "source": item["source"],
                "category": item["category"],
                "target_language": item["language"],
                "output": item["output"],
            }
        )
        mapping.append(
            {
                "review_id": review_id,
                "model_id": str(item["model_id"]),
                "source_id": str(item["source_id"]),
                "language": str(item["language"]),
            }
        )
    expected_items = (
        len(model_ids) * len(checked_languages) * len(CATEGORIES) * per_category
    )
    if len(review_items) != expected_items:
        raise AssertionError(f"built {len(review_items)} review items, expected {expected_items}")
    counts = Counter(
        (str(item["category"]), str(item["target_language"]))
        for item in review_items
    )
    package: dict[str, object] = {
        "format": REVIEW_FORMAT,
        "seed": seed,
        "model_count": len(model_ids),
        "languages": list(checked_languages),
        "sample_per_category": per_category,
        "item_count": len(review_items),
        "stratum_counts": {
            f"{category}:{language}": counts[(category, language)]
            for category in CATEGORIES
            for language in checked_languages
        },
        "rubric": RUBRIC,
        "items": review_items,
    }
    package_hash = canonical_sha256(package)
    key_document: dict[str, object] = {
        "format": REVIEW_FORMAT,
        "package_sha256": package_hash,
        "models": model_ids,
        "mapping": mapping,
    }
    if "models" in package or any("model_id" in item for item in review_items):
        raise AssertionError("model identity leaked into the blind review package")
    return package, key_document


def write_chunks(package: Mapping[str, object], directory: Path, chunk_size: int) -> int:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    items = package.get("items")
    if not isinstance(items, list):
        raise ValueError("review package is missing items")
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for offset in range(0, len(items), chunk_size):
        count += 1
        chunk = {
            "format": REVIEW_FORMAT,
            "package_sha256": canonical_sha256(package),
            "chunk_index": count,
            "rubric": package["rubric"],
            "items": items[offset : offset + chunk_size],
        }
        atomic_write_json(directory / f"review-chunk-{count:02d}.json", chunk)
    return count


def parse_model_path(value: str) -> tuple[str, str, Path]:
    model_spec, separator, raw_path = value.partition("=")
    if not separator or not model_spec or not raw_path:
        raise argparse.ArgumentTypeError(
            "--raw must use LOGICAL_ID[@RAW_ID]=PATH"
        )
    logical_id, raw_separator, raw_model_id = model_spec.partition("@")
    if not logical_id or (raw_separator and not raw_model_id):
        raise argparse.ArgumentTypeError(
            "--raw must use LOGICAL_ID[@RAW_ID]=PATH"
        )
    return logical_id, raw_model_id if raw_separator else logical_id, Path(raw_path)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--raw", required=True, action="append", type=parse_model_path)
    parser.add_argument("--review-output", required=True, type=Path)
    parser.add_argument("--key-output", required=True, type=Path)
    parser.add_argument("--chunk-dir", type=Path)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--per-category", type=int, default=DEFAULT_PER_CATEGORY)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--languages",
        nargs="+",
        choices=LANGUAGES,
        default=list(LANGUAGES),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    entries = load_corpus(args.corpus)
    corpus_texts = {str(row["text"]) for row in entries}
    shards_by_model: defaultdict[str, list[tuple[Path, str]]] = defaultdict(list)
    seen_shards: set[tuple[str, Path]] = set()
    for model_id, raw_model_id, path in args.raw:
        shard_key = (model_id, path.resolve())
        if shard_key in seen_shards:
            raise ValueError(f"duplicate --raw shard for {model_id}: {path}")
        seen_shards.add(shard_key)
        shards_by_model[model_id].append((path, raw_model_id))
    outputs_by_model: dict[str, dict[tuple[str, str], str | None]] = {}
    for model_id, shards in shards_by_model.items():
        outputs_by_model[model_id] = load_model_outputs(
            shards,
            model_id=model_id,
            corpus_texts=corpus_texts,
            languages=args.languages,
        )
    package, key = build_review_documents(
        entries,
        outputs_by_model,
        per_category=args.per_category,
        seed=args.seed,
        languages=args.languages,
    )
    atomic_write_json(args.review_output, package)
    atomic_write_json(args.key_output, key)
    chunks = write_chunks(package, args.chunk_dir, args.chunk_size) if args.chunk_dir else 0
    print(
        json.dumps(
            {
                "review_items": package["item_count"],
                "models": len(outputs_by_model),
                "chunks": chunks,
                "package_sha256": key["package_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
