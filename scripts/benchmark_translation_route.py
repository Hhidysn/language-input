#!/usr/bin/env python3
"""Benchmark a local CTranslate2 translation route on one target language."""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from benchmark_local_model import (
    DEFAULT_SEED,
    LANGUAGES,
    ORDINARY_CATEGORIES,
    BatchRecord,
    MemorySampler,
    atomic_write_json,
    chunked,
    is_unsafe_source,
    load_corpus,
    nearest_rank_percentile,
    script_status,
)


ROUTE_FORMAT = "language-input-translation-route-v1"
PROMPT_VERSION = "translation-route-v1"
MAX_GLOSS_CHARACTERS = 40
ALLOWED_STAGE_KINDS = {"marian", "m2m100", "sentencepiece"}


def load_route_config(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("format") != ROUTE_FORMAT:
        raise ValueError(f"unsupported translation route format in {path}")
    if set(document) != {"format", "route_id", "language", "stages"}:
        raise ValueError("translation route has unexpected or missing keys")
    route_id = document["route_id"]
    language = document["language"]
    stages = document["stages"]
    if (
        not isinstance(route_id, str)
        or not route_id
        or any(character.isspace() for character in route_id)
    ):
        raise ValueError("translation route needs a non-empty whitespace-free route_id")
    if language not in LANGUAGES:
        raise ValueError(f"unsupported target language: {language!r}")
    if not isinstance(stages, list) or not stages:
        raise ValueError("translation route needs at least one stage")
    checked_stages: list[dict[str, str]] = []
    stage_names: set[str] = set()
    for raw_stage in stages:
        if not isinstance(raw_stage, dict):
            raise ValueError("translation stage must be an object")
        common_required = {"name", "kind", "model_path", "revision"}
        optional = {"source_language", "target_language"}
        if not common_required.issubset(raw_stage):
            raise ValueError("translation stage has unexpected or missing keys")
        kind = raw_stage["kind"]
        if kind not in ALLOWED_STAGE_KINDS:
            raise ValueError(f"unsupported translation stage kind: {kind!r}")
        tokenizer_required = (
            {"source_tokenizer", "target_tokenizer"}
            if kind == "sentencepiece"
            else {"tokenizer_model"}
        )
        required = common_required | tokenizer_required
        if not required.issubset(raw_stage) or not set(raw_stage).issubset(
            required | optional
        ):
            raise ValueError("translation stage has unexpected or missing keys")
        if not all(
            isinstance(raw_stage[key], str) and raw_stage[key] for key in required
        ):
            raise ValueError("translation stage contains an empty non-string field")
        stage = {key: str(value) for key, value in raw_stage.items()}
        if stage["name"] in stage_names:
            raise ValueError(f"duplicate translation stage name: {stage['name']}")
        stage_names.add(stage["name"])
        if stage["kind"] == "m2m100":
            if stage.get("source_language") != "zh" or stage.get(
                "target_language"
            ) != language:
                raise ValueError("m2m100 stage language codes do not match the route")
        elif "source_language" in stage or "target_language" in stage:
            raise ValueError(
                "Marian and SentencePiece stages must not declare language-code prefixes"
            )
        model_path = Path(stage["model_path"]).resolve(strict=True)
        if not model_path.is_dir():
            raise ValueError(f"translation model is not a directory: {model_path}")
        stage["model_path"] = str(model_path)
        if stage["kind"] == "sentencepiece":
            for key in ("source_tokenizer", "target_tokenizer"):
                tokenizer_path = Path(stage[key]).resolve(strict=True)
                if not tokenizer_path.is_file():
                    raise ValueError(
                        f"SentencePiece tokenizer is not a file: {tokenizer_path}"
                    )
                stage[key] = str(tokenizer_path)
        checked_stages.append(stage)
    return {
        "format": ROUTE_FORMAT,
        "route_id": route_id,
        "language": language,
        "stages": checked_stages,
    }


def validate_translations(
    requested: Sequence[str],
    translated: Sequence[str],
    *,
    max_characters: int = MAX_GLOSS_CHARACTERS,
) -> tuple[dict[str, str], str | None]:
    if len(requested) != len(translated):
        return {}, "translation-count-mismatch"
    outputs: dict[str, str] = {}
    error: str | None = None
    for source, raw_value in zip(requested, translated, strict=True):
        if not isinstance(raw_value, str):
            error = error or "translation-not-string"
            continue
        value = unicodedata.normalize("NFC", raw_value).strip()
        if (
            not value
            or len(value) > max_characters
            or any(character in value for character in "\r\n")
            or any(unicodedata.category(character) == "Cc" for character in value)
        ):
            error = error or "invalid-translation-value"
            continue
        outputs[source] = value
    return outputs, error


def validate_hypotheses(
    requested: Sequence[str],
    hypotheses: Sequence[Sequence[str]],
    *,
    expected_count: int,
    max_characters: int = MAX_GLOSS_CHARACTERS,
) -> tuple[dict[str, list[str]], str | None]:
    if len(requested) != len(hypotheses):
        return {}, "hypothesis-row-count-mismatch"
    checked: dict[str, list[str]] = {}
    for source, row in zip(requested, hypotheses, strict=True):
        if (
            not isinstance(row, Sequence)
            or isinstance(row, (str, bytes))
            or len(row) != expected_count
        ):
            return {}, "hypothesis-count-mismatch"
        valid_values: list[str] = []
        for raw_value in row:
            if not isinstance(raw_value, str):
                continue
            if any(character in raw_value for character in "\r\n") or any(
                unicodedata.category(character) == "Cc" for character in raw_value
            ):
                continue
            value = clean_hypothesis(raw_value)
            if (
                not value
                or len(value) > max_characters
            ):
                continue
            valid_values.append(value)
        if valid_values:
            checked[source] = valid_values
    return checked, None


def clean_hypothesis(value: str) -> str:
    """Remove common beam-search artifacts without consulting a dictionary."""
    value = unicodedata.normalize("NFC", value).strip().rstrip(".。").rstrip()
    value = re.sub(r"^Category:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()

    # Translation beams often repeat a short word several times. Collapse exact
    # token runs first, then no-space repeats such as アカウントアカウント.
    words = value.split(" ")
    collapsed_words: list[str] = []
    for word in words:
        if not collapsed_words or collapsed_words[-1].casefold() != word.casefold():
            collapsed_words.append(word)
    value = " ".join(collapsed_words)
    for unit_length in range(2, len(value) // 2 + 1):
        if len(value) % unit_length:
            continue
        unit = value[:unit_length]
        if unit.isascii() and unit.isalnum():
            continue
        if unit * (len(value) // unit_length) == value:
            value = unit
            break
    return value.strip()


def combine_hypotheses(
    hypotheses: Mapping[str, Sequence[str]],
    *,
    max_glosses: int,
    max_characters: int = MAX_GLOSS_CHARACTERS,
    language: str | None = None,
) -> dict[str, str]:
    """Join up to two distinct model hypotheses without any dictionary lookup."""
    if max_glosses < 1:
        raise ValueError("max_glosses must be positive")
    outputs: dict[str, str] = {}
    for source, values in hypotheses.items():
        ranked_values = list(values)
        if language == "ja":
            rank = {
                "match": 0,
                "indeterminate-han-only": 1,
                "indeterminate-latin": 2,
                "mismatch": 3,
            }
            ranked_values = [
                value
                for _, value in sorted(
                    enumerate(ranked_values),
                    key=lambda item: (
                        rank.get(script_status(clean_hypothesis(item[1]), language), 4),
                        item[0],
                    ),
                )
            ]
        selected: list[str] = []
        seen: set[str] = set()
        for raw_value in ranked_values:
            value = clean_hypothesis(raw_value)
            canonical = "".join(
                character.casefold()
                for character in unicodedata.normalize("NFKC", value)
                if unicodedata.category(character)[0] in {"L", "N"}
            )
            if not value or not canonical or canonical in seen:
                continue
            joined = "; ".join([*selected, value])
            if len(joined) > max_characters:
                continue
            selected.append(value)
            seen.add(canonical)
            if len(selected) >= max_glosses:
                break
        if selected:
            outputs[source] = "; ".join(selected)
    return outputs


class CTranslate2Stage:
    def __init__(self, config: Mapping[str, str], *, threads: int, beam_size: int) -> None:
        import ctranslate2  # Imported lazily so repository unit tests need no ML runtime.

        self.name = config["name"]
        self.kind = config["kind"]
        self.beam_size = beam_size
        self.source_language = config.get("source_language")
        self.target_language = config.get("target_language")
        self.tokenizer = None
        self.source_tokenizer = None
        self.target_tokenizer = None
        if self.kind == "sentencepiece":
            import sentencepiece

            self.source_tokenizer = sentencepiece.SentencePieceProcessor(
                model_file=config["source_tokenizer"]
            )
            self.target_tokenizer = sentencepiece.SentencePieceProcessor(
                model_file=config["target_tokenizer"]
            )
        else:
            from transformers import AutoTokenizer

            self.tokenizer = AutoTokenizer.from_pretrained(
                config["tokenizer_model"],
                revision=config["revision"],
                local_files_only=True,
            )
            if self.kind == "m2m100":
                self.tokenizer.src_lang = self.source_language
        self.translator = ctranslate2.Translator(
            config["model_path"],
            device="cpu",
            compute_type="int8",
            inter_threads=1,
            intra_threads=threads,
        )

    def translate_hypotheses(
        self,
        texts: Sequence[str],
        *,
        num_hypotheses: int,
    ) -> list[list[str]]:
        if num_hypotheses < 1 or num_hypotheses > self.beam_size:
            raise ValueError("num_hypotheses must be between one and beam_size")
        if self.kind == "sentencepiece":
            assert self.source_tokenizer is not None
            encoded = [
                self.source_tokenizer.encode(text, out_type=str) for text in texts
            ]
        else:
            assert self.tokenizer is not None
            encoded = [
                self.tokenizer.convert_ids_to_tokens(self.tokenizer.encode(text))
                for text in texts
            ]
        target_prefix = None
        if self.kind == "m2m100":
            assert self.tokenizer is not None
            target_token_id = self.tokenizer.get_lang_id(self.target_language)
            target_token = self.tokenizer.convert_ids_to_tokens(target_token_id)
            target_prefix = [[target_token] for _ in texts]
        results = self.translator.translate_batch(
            encoded,
            target_prefix=target_prefix,
            max_batch_size=9,
            batch_type="examples",
            beam_size=self.beam_size,
            num_hypotheses=num_hypotheses,
            max_input_length=64,
            max_decoding_length=64,
            return_scores=False,
        )
        if self.kind == "sentencepiece":
            assert self.target_tokenizer is not None
            return [
                [self.target_tokenizer.decode(hypothesis) for hypothesis in result.hypotheses]
                for result in results
            ]
        assert self.tokenizer is not None
        return [
            [
                self.tokenizer.decode(
                    self.tokenizer.convert_tokens_to_ids(hypothesis),
                    skip_special_tokens=True,
                )
                for hypothesis in result.hypotheses
            ]
            for result in results
        ]

    def translate(self, texts: Sequence[str]) -> list[str]:
        return [
            hypotheses[0]
            for hypotheses in self.translate_hypotheses(texts, num_hypotheses=1)
        ]


class TranslationPipeline:
    def __init__(
        self,
        stage_configs: Sequence[Mapping[str, str]],
        *,
        threads: int,
        beam_size: int,
    ) -> None:
        self.stages = [
            CTranslate2Stage(stage, threads=threads, beam_size=beam_size)
            for stage in stage_configs
        ]

    def translate(self, texts: Sequence[str]) -> tuple[list[str], list[float]]:
        values = list(texts)
        timings: list[float] = []
        for stage in self.stages:
            started = time.perf_counter()
            values = stage.translate(values)
            timings.append((time.perf_counter() - started) * 1000)
        return values, timings

    def translate_hypotheses(
        self,
        texts: Sequence[str],
        *,
        num_hypotheses: int,
    ) -> tuple[list[list[str]], list[float]]:
        values = list(texts)
        timings: list[float] = []
        for stage in self.stages[:-1]:
            started = time.perf_counter()
            values = stage.translate(values)
            timings.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        hypotheses = self.stages[-1].translate_hypotheses(
            values,
            num_hypotheses=num_hypotheses,
        )
        timings.append((time.perf_counter() - started) * 1000)
        return hypotheses, timings


def run_benchmark(
    *,
    pipeline: TranslationPipeline,
    entries: Sequence[Mapping[str, object]],
    model_id: str,
    language: str,
    batch_size: int,
    raw_output_path: Path,
    suppress_unsafe_sources: bool,
    num_hypotheses: int,
    max_glosses: int,
) -> list[BatchRecord]:
    texts = [str(row["text"]) for row in entries]
    warmup = [
        text
        for text in texts
        if not suppress_unsafe_sources or not is_unsafe_source(text)
    ][:9]
    for _ in range(2):
        if warmup:
            pipeline.translate_hypotheses(
                warmup,
                num_hypotheses=num_hypotheses,
            )
    records: list[BatchRecord] = []
    raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_output_path.open("w", encoding="utf-8", newline="\n") as stream:
        for batch_index, words in enumerate(chunked(texts, batch_size), start=1):
            suppressed = (
                [word for word in words if is_unsafe_source(word)]
                if suppress_unsafe_sources
                else []
            )
            suppressed_set = set(suppressed)
            submitted = [word for word in words if word not in suppressed_set]
            error = None
            stage_timings: list[float] = []
            hypothesis_map: dict[str, list[str]] = {}
            started = time.perf_counter()
            try:
                hypothesis_rows, stage_timings = (
                    pipeline.translate_hypotheses(
                        submitted,
                        num_hypotheses=num_hypotheses,
                    )
                    if submitted
                    else ([], [])
                )
                hypothesis_map, error = validate_hypotheses(
                    submitted,
                    hypothesis_rows,
                    expected_count=num_hypotheses,
                )
                outputs = (
                    combine_hypotheses(
                        hypothesis_map,
                        max_glosses=max_glosses,
                        language=language,
                    )
                    if error is None
                    else {}
                )
            except Exception as exc:  # Record a failed batch instead of losing the run.
                outputs = {}
                hypothesis_map = {}
                error = f"translation-error:{type(exc).__name__}:{exc}"[:500]
            elapsed_ms = (time.perf_counter() - started) * 1000
            record = BatchRecord(
                model_id=model_id,
                language=language,
                batch_index=batch_index,
                size=len(words),
                elapsed_ms=elapsed_ms,
                http_status=None,
                valid=error is None,
                error=error,
                usage=None,
                timings={
                    "stage_ms": stage_timings,
                    "stage_names": [stage.name for stage in pipeline.stages],
                },
                outputs=outputs,
                script_status={
                    word: script_status(value, language)
                    for word, value in outputs.items()
                },
                suppressed=suppressed,
                submitted_size=len(submitted),
            )
            records.append(record)
            raw = asdict(record)
            raw["requested"] = words
            raw["hypotheses"] = hypothesis_map
            stream.write(json.dumps(raw, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
    return records


def summarize_single_language(
    records: Sequence[BatchRecord],
    entries: Sequence[Mapping[str, object]],
    *,
    route: Mapping[str, object],
    cold_start_ms: float,
    peak_working_set_bytes: int,
    threads: int,
    beam_size: int,
    num_hypotheses: int,
    max_glosses: int,
    suppress_unsafe_sources: bool,
) -> dict[str, object]:
    language = str(route["language"])
    ordinary_texts = {
        str(row["text"])
        for row in entries
        if row["category"] in ORDINARY_CATEGORIES
    }
    attempted = sum(record.size for record in records)
    submitted = sum(record.submitted_size or 0 for record in records)
    suppressed = sum(len(record.suppressed) for record in records)
    valid_glosses = sum(len(record.outputs) for record in records)
    ordinary_valid = sum(
        word in ordinary_texts for record in records for word in record.outputs
    )
    nine_candidate_ms = [
        record.elapsed_ms
        for record in records
        if record.submitted_size == 9 and record.valid
    ]
    return {
        "schema_version": 1,
        "prompt_version": PROMPT_VERSION,
        "model_id": route["route_id"],
        "language": language,
        "translations_attempted": attempted,
        "translations_submitted": submitted,
        "translations_suppressed": suppressed,
        "batches": len(records),
        "valid_batches": sum(record.valid for record in records),
        "valid_glosses": valid_glosses,
        "valid_gloss_coverage_all": valid_glosses / attempted if attempted else 0.0,
        "eligible_ordinary_attempts": len(ordinary_texts),
        "eligible_ordinary_valid": ordinary_valid,
        "valid_gloss_coverage_ordinary": ordinary_valid / len(ordinary_texts),
        "cold_start_ms": cold_start_ms,
        "peak_working_set_bytes": peak_working_set_bytes,
        "warm_nine_candidate_latency_ms": {
            "samples": len(nine_candidate_ms),
            "p50": (
                sorted(nine_candidate_ms)[len(nine_candidate_ms) // 2]
                if nine_candidate_ms
                else None
            ),
            "p95": nearest_rank_percentile(nine_candidate_ms, 0.95),
            "maximum": max(nine_candidate_ms) if nine_candidate_ms else None,
        },
        "script_status": {
            status: sum(
                value == status
                for record in records
                for value in record.script_status.values()
            )
            for status in sorted(
                {
                    value
                    for record in records
                    for value in record.script_status.values()
                }
            )
        },
        "settings": {
            "batch_size": max(record.size for record in records),
            "beam_size": beam_size,
            "num_hypotheses": num_hypotheses,
            "max_glosses": max_glosses,
            "threads": threads,
            "seed": DEFAULT_SEED,
            "max_gloss_characters": MAX_GLOSS_CHARACTERS,
            "suppress_unsafe_sources": suppress_unsafe_sources,
            "stage_names": [stage["name"] for stage in route["stages"]],
            "stage_kinds": [stage["kind"] for stage in route["stages"]],
            "tokenizer_revisions": [stage["revision"] for stage in route["stages"]],
        },
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--route", required=True, type=Path)
    parser.add_argument("--raw-output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=9)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--beam-size", type=int, default=4)
    parser.add_argument("--num-hypotheses", type=int, default=1)
    parser.add_argument("--max-glosses", type=int, default=1)
    parser.add_argument("--suppress-unsafe-sources", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not 1 <= args.batch_size <= 9:
        raise ValueError("--batch-size must be between 1 and 9")
    if (
        args.threads < 1
        or args.beam_size < 1
        or args.num_hypotheses < 1
        or args.max_glosses < 1
    ):
        raise ValueError(
            "--threads, --beam-size, --num-hypotheses, and --max-glosses "
            "must be positive"
        )
    if args.num_hypotheses > args.beam_size:
        raise ValueError("--num-hypotheses cannot exceed --beam-size")
    if args.max_glosses > args.num_hypotheses:
        raise ValueError("--max-glosses cannot exceed --num-hypotheses")
    route = load_route_config(args.route)
    entries = load_corpus(args.corpus)
    memory = MemorySampler(os.getpid())
    memory.start()
    started = time.perf_counter()
    pipeline = TranslationPipeline(
        route["stages"],
        threads=args.threads,
        beam_size=args.beam_size,
    )
    cold_start_ms = (time.perf_counter() - started) * 1000
    records = run_benchmark(
        pipeline=pipeline,
        entries=entries,
        model_id=str(route["route_id"]),
        language=str(route["language"]),
        batch_size=args.batch_size,
        raw_output_path=args.raw_output,
        suppress_unsafe_sources=args.suppress_unsafe_sources,
        num_hypotheses=args.num_hypotheses,
        max_glosses=args.max_glosses,
    )
    del pipeline
    gc.collect()
    peak = memory.stop()
    summary = summarize_single_language(
        records,
        entries,
        route=route,
        cold_start_ms=cold_start_ms,
        peak_working_set_bytes=peak,
        threads=args.threads,
        beam_size=args.beam_size,
        num_hypotheses=args.num_hypotheses,
        max_glosses=args.max_glosses,
        suppress_unsafe_sources=args.suppress_unsafe_sources,
    )
    try:
        import ctranslate2
        import sentencepiece
        import transformers

        summary["runtime"] = {
            "ctranslate2": ctranslate2.__version__,
            "sentencepiece": sentencepiece.__version__,
            "transformers": transformers.__version__,
        }
    except ImportError:
        summary["runtime"] = None
    atomic_write_json(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
