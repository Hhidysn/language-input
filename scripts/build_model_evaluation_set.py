#!/usr/bin/env python3
"""Build the deterministic Language Input local-model evaluation corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


FORMAT_VERSION = "language-input-model-evaluation-v1"
DEFAULT_COUNTS = {
    "high_frequency": 120,
    "dictionary_miss": 80,
    "variant": 40,
    "ambiguous": 40,
    "adversarial": 40,
}


@dataclass(frozen=True)
class EssayTerm:
    text: str
    weight: Decimal

    @property
    def serialized_weight(self) -> int | str:
        integral = self.weight.to_integral_value()
        if integral == self.weight:
            return int(integral)
        return format(self.weight, "f")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_source_bytes(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def source_record(
    path: Path,
    *,
    source_url: str | None = None,
    revision: str | None = None,
    license_id: str | None = None,
) -> dict[str, object]:
    raw = path.read_bytes()
    normalized = normalized_source_bytes(path)
    record: dict[str, object] = {
        "file": path.name,
        "raw_bytes": len(raw),
        "raw_sha256": sha256_bytes(raw),
        "newline_normalized_bytes": len(normalized),
        "newline_normalized_sha256": sha256_bytes(normalized),
    }
    if source_url:
        record["source_url"] = source_url
    if revision:
        record["revision"] = revision
    if license_id:
        record["license"] = license_id
    return record


def parse_essay(path: Path) -> list[EssayTerm]:
    best: dict[str, Decimal] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            raise ValueError(f"invalid essay row {path}:{line_number}")
        text = fields[0].strip()
        try:
            weight = Decimal(fields[-1].strip())
        except InvalidOperation as exc:
            raise ValueError(
                f"invalid essay weight {path}:{line_number}: {fields[-1]!r}"
            ) from exc
        if not text:
            continue
        current = best.get(text)
        if current is None or weight > current:
            best[text] = weight
    return sorted(
        (EssayTerm(text, weight) for text, weight in best.items()),
        key=lambda item: (-item.weight, item.text),
    )


def parse_glosspack(path: Path) -> set[str]:
    words: set[str] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not raw_line or raw_line.startswith("#"):
            continue
        fields = raw_line.split("\t", 1)
        if len(fields) != 2 or not fields[0] or not fields[1].strip():
            raise ValueError(f"invalid GlossPack row {path}:{line_number}")
        words.add(fields[0])
    return words


def is_han_character(character: str) -> bool:
    name = unicodedata.name(character, "")
    return name.startswith("CJK UNIFIED IDEOGRAPH-") or name.startswith(
        "CJK COMPATIBILITY IDEOGRAPH-"
    )


def is_ordinary_chinese_term(text: str) -> bool:
    return 2 <= len(text) <= 12 and all(is_han_character(char) for char in text)


def load_curated(path: Path) -> dict[str, list[dict[str, object]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != FORMAT_VERSION:
        raise ValueError(f"unsupported curated fixture format in {path}")
    result: dict[str, list[dict[str, object]]] = {}
    for category in ("variant", "ambiguous", "adversarial"):
        rows = document.get(category)
        if not isinstance(rows, list):
            raise ValueError(f"curated category {category!r} must be a list")
        checked: list[dict[str, object]] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"{category} row {index} must be an object")
            text = row.get("text")
            if not isinstance(text, str) or not text or len(text) > 64:
                raise ValueError(f"invalid {category} text at row {index}")
            if any(unicodedata.category(char) == "Cc" for char in text):
                raise ValueError(f"control character in {category} row {index}")
            checked.append(dict(row))
        result[category] = checked
    return result


def load_excluded_texts(paths: Sequence[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("format") != FORMAT_VERSION:
            raise ValueError(f"unsupported exclusion corpus format in {path}")
        rows = document.get("entries")
        if not isinstance(rows, list):
            raise ValueError(f"exclusion corpus has no entries in {path}")
        for row in rows:
            text = row.get("text") if isinstance(row, dict) else None
            if not isinstance(text, str) or not text:
                raise ValueError(f"invalid exclusion row in {path}")
            excluded.add(text)
    return excluded


def identity_normalizer(words: Sequence[str]) -> dict[str, str]:
    return {word: word for word in words}


def normalize_with_opencc(
    words: Sequence[str],
    *,
    executable: Path,
    config: Path,
    temporary_root: Path,
) -> dict[str, str]:
    executable = executable.resolve(strict=True)
    config = config.resolve(strict=True)
    temporary_root = temporary_root.resolve(strict=True)
    with tempfile.TemporaryDirectory(
        prefix="language-input-opencc-", dir=temporary_root
    ) as temporary:
        temporary_path = Path(temporary)
        input_path = temporary_path / "input.txt"
        output_path = temporary_path / "output.txt"
        input_path.write_text("\n".join(words) + "\n", encoding="utf-8", newline="\n")
        completed = subprocess.run(
            [
                str(executable),
                "-c",
                str(config),
                "-i",
                str(input_path),
                "-o",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"OpenCC failed with {completed.returncode}: {completed.stderr.strip()}"
            )
        converted = output_path.read_text(encoding="utf-8").splitlines()
        if len(converted) != len(words):
            raise ValueError(
                f"OpenCC returned {len(converted)} lines for {len(words)} inputs"
            )
        return dict(zip(words, converted, strict=True))


def _entry(
    category: str,
    number: int,
    text: str,
    **metadata: object,
) -> dict[str, object]:
    prefix = {
        "high_frequency": "hf",
        "dictionary_miss": "miss",
        "variant": "var",
        "ambiguous": "amb",
        "adversarial": "adv",
    }[category]
    return {
        "id": f"{prefix}-{number:03d}",
        "category": category,
        "text": text,
        **metadata,
    }


def build_entries(
    essay_terms: Sequence[EssayTerm],
    gloss_words: set[str],
    curated: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    counts: Mapping[str, int] = DEFAULT_COUNTS,
    normalizer: Callable[[Sequence[str]], Mapping[str, str]] = identity_normalizer,
    excluded_texts: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    expected_categories = set(DEFAULT_COUNTS)
    if set(counts) != expected_categories:
        raise ValueError(f"counts must contain exactly {sorted(expected_categories)}")
    for name, count in counts.items():
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"invalid count for {name}: {count!r}")
    for category in ("variant", "ambiguous", "adversarial"):
        if len(curated[category]) != counts[category]:
            raise ValueError(
                f"expected {counts[category]} curated {category} rows, "
                f"found {len(curated[category])}"
            )

    curated_texts = [
        str(row["text"])
        for category in ("variant", "ambiguous", "adversarial")
        for row in curated[category]
    ]
    duplicates = [
        text for text, amount in Counter(curated_texts).items() if amount != 1
    ]
    if duplicates:
        raise ValueError(f"duplicate curated texts: {duplicates}")
    curated_exclusions = sorted(set(curated_texts) & set(excluded_texts))
    if curated_exclusions:
        raise ValueError(f"curated texts reuse excluded sources: {curated_exclusions}")
    reserved = set(curated_texts) | set(excluded_texts)

    ordinary_source = [
        term
        for term in essay_terms
        if is_ordinary_chinese_term(term.text)
    ]
    normalized_by_source = dict(
        normalizer([term.text for term in ordinary_source])
    )
    if set(normalized_by_source) != {term.text for term in ordinary_source}:
        raise ValueError("normalizer must return exactly the requested key set")
    ordinary: list[tuple[EssayTerm, str]] = []
    seen_normalized: set[str] = set()
    for term in ordinary_source:
        normalized_text = normalized_by_source[term.text]
        if (
            not is_ordinary_chinese_term(normalized_text)
            or normalized_text in reserved
            or normalized_text in seen_normalized
        ):
            continue
        ordinary.append((term, normalized_text))
        seen_normalized.add(normalized_text)

    high_terms = ordinary[: counts["high_frequency"]]
    if len(high_terms) != counts["high_frequency"]:
        raise ValueError("essay source does not contain enough high-frequency terms")
    used = reserved | {normalized for _, normalized in high_terms}
    miss_terms = [
        (term, normalized_text)
        for term, normalized_text in ordinary
        if normalized_text not in used
        and normalized_text not in gloss_words
    ][: counts["dictionary_miss"]]
    if len(miss_terms) != counts["dictionary_miss"]:
        raise ValueError("essay source does not contain enough dictionary misses")

    entries: list[dict[str, object]] = []
    for number, (term, normalized_text) in enumerate(high_terms, start=1):
        entries.append(
            _entry(
                "high_frequency",
                number,
                normalized_text,
                source={
                    "kind": "rime-essay",
                    "weight": term.serialized_weight,
                    "original": term.text,
                    "t2s": normalized_text,
                },
            )
        )
    for number, (term, normalized_text) in enumerate(miss_terms, start=1):
        entries.append(
            _entry(
                "dictionary_miss",
                number,
                normalized_text,
                source={
                    "kind": "rime-essay",
                    "weight": term.serialized_weight,
                    "original": term.text,
                    "t2s": normalized_text,
                    "exact_gloss": False,
                    "normalized_gloss": False,
                },
            )
        )
    for category in ("variant", "ambiguous", "adversarial"):
        for number, row in enumerate(curated[category], start=1):
            metadata = {key: value for key, value in row.items() if key != "text"}
            entries.append(_entry(category, number, str(row["text"]), **metadata))

    expected_total = sum(counts.values())
    if len(entries) != expected_total:
        raise AssertionError(f"built {len(entries)} rows, expected {expected_total}")
    texts = [str(row["text"]) for row in entries]
    if len(texts) != len(set(texts)):
        raise ValueError("evaluation entries must be globally unique")
    if set(texts) & set(excluded_texts):
        raise AssertionError("evaluation entries overlap an exclusion corpus")
    return entries


def write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8", newline="\n")


def build_evaluation_set(
    *,
    essay_path: Path,
    gloss_path: Path,
    curated_path: Path,
    output_path: Path,
    manifest_path: Path,
    counts: Mapping[str, int] = DEFAULT_COUNTS,
    normalizer: Callable[[Sequence[str]], Mapping[str, str]] = identity_normalizer,
    provenance: Mapping[str, object] | None = None,
    exclusion_paths: Sequence[Path] = (),
) -> dict[str, object]:
    essay_terms = parse_essay(essay_path)
    gloss_words = parse_glosspack(gloss_path)
    curated = load_curated(curated_path)
    excluded_texts = load_excluded_texts(exclusion_paths)
    entries = build_entries(
        essay_terms,
        gloss_words,
        curated,
        counts=counts,
        normalizer=normalizer,
        excluded_texts=excluded_texts,
    )
    corpus = {
        "format": FORMAT_VERSION,
        "counts": dict(counts),
        "entries": entries,
    }
    write_json(output_path, corpus)
    manifest: dict[str, object] = {
        "format": FORMAT_VERSION,
        "counts": dict(counts),
        "sources": {
            "essay": source_record(essay_path),
            "glosspack": source_record(gloss_path),
            "curated": source_record(curated_path),
        },
        "output": {
            "file": output_path.name,
            "bytes": output_path.stat().st_size,
            "sha256": sha256_file(output_path),
        },
        "exclusions": {
            "sources": [source_record(path) for path in exclusion_paths],
            "unique_texts": len(excluded_texts),
        },
    }
    if provenance:
        manifest["provenance"] = dict(provenance)
    write_json(manifest_path, manifest)
    return manifest


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--essay", required=True, type=Path)
    parser.add_argument("--glosspack", required=True, type=Path)
    parser.add_argument("--curated", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--opencc-exe", type=Path)
    parser.add_argument("--opencc-config", type=Path)
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--essay-source-url")
    parser.add_argument("--essay-revision")
    parser.add_argument("--glosspack-source-url")
    parser.add_argument("--exclude-corpus", action="append", type=Path, default=[])
    parser.add_argument("--high-frequency-count", type=int, default=120)
    parser.add_argument("--dictionary-miss-count", type=int, default=80)
    parser.add_argument("--variant-count", type=int, default=40)
    parser.add_argument("--ambiguous-count", type=int, default=40)
    parser.add_argument("--adversarial-count", type=int, default=40)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    opencc_arguments = (args.opencc_exe, args.opencc_config, args.temp_dir)
    if any(opencc_arguments) and not all(opencc_arguments):
        raise ValueError(
            "--opencc-exe, --opencc-config and --temp-dir must be supplied together"
        )
    if all(opencc_arguments):
        normalizer = lambda words: normalize_with_opencc(  # noqa: E731
            words,
            executable=args.opencc_exe,
            config=args.opencc_config,
            temporary_root=args.temp_dir,
        )
        normalization = {
            "kind": "OpenCC t2s",
            "executable": str(args.opencc_exe),
            "config": source_record(args.opencc_config),
        }
    else:
        normalizer = identity_normalizer
        normalization = {"kind": "identity"}
    provenance = {
        "essay": {
            "source_url": args.essay_source_url,
            "revision": args.essay_revision,
            "license": "LGPL-3.0-or-later",
        },
        "glosspack": {
            "source_url": args.glosspack_source_url,
            "license": "CC-BY-SA-4.0",
        },
        "normalization": normalization,
    }
    counts = {
        "high_frequency": args.high_frequency_count,
        "dictionary_miss": args.dictionary_miss_count,
        "variant": args.variant_count,
        "ambiguous": args.ambiguous_count,
        "adversarial": args.adversarial_count,
    }
    manifest = build_evaluation_set(
        essay_path=args.essay,
        gloss_path=args.glosspack,
        curated_path=args.curated,
        output_path=args.output,
        manifest_path=args.manifest,
        counts=counts,
        normalizer=normalizer,
        provenance=provenance,
        exclusion_paths=args.exclude_corpus,
    )
    print(
        f"built {sum(manifest['counts'].values())} evaluation sources: "
        f"{args.output} ({manifest['output']['sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
