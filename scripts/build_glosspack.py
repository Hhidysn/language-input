#!/usr/bin/env python3
"""Build a compact, deterministic Language Input GlossPack from CC-CEDICT."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Iterable, Iterator, TextIO


FORMAT_VERSION = "language-input-glosspack-v1"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
CEDICT_LINE = re.compile(
    r"^(?P<traditional>\S+)\s+(?P<simplified>\S+)\s+"
    r"\[(?P<pinyin>[^]]*)\]\s+/(?P<definitions>.*)/\s*$"
)
LEADING_LABEL = re.compile(
    r"^\((?:abbr\.|ACG|arch\.|coll\.|dialect|euphemism|fig\.|honorific|"
    r"Internet slang|Japanese|literary|loanword|old|onom\.|slang|Tw|vulgar)\)\s*",
    re.IGNORECASE,
)
TRADITIONAL_SIMPLIFIED_REFERENCE = re.compile(
    r"([^\s|/;,()]+)\|([^\s\[/;,()]+)\[[^]]+\]"
)
PINYIN_REFERENCE = re.compile(r"\[[^]]+\]")
WHITESPACE = re.compile(r"\s+")
LOW_VALUE_PREFIXES = (
    "also written ",
    "abbr. for ",
    "old variant of ",
    "variant of ",
    "see also ",
    "see ",
)


@dataclass(frozen=True)
class Sense:
    text: str
    source_order: int

    @property
    def rank(self) -> tuple[int, int, int]:
        lowered = self.text.casefold()
        penalty = 0
        if lowered.startswith(LOW_VALUE_PREFIXES):
            penalty += 100
        if lowered.startswith("surname "):
            penalty += 60
        if lowered.startswith("classifier for "):
            penalty += 30
        if lowered.startswith("used in "):
            penalty += 20
        return penalty, self.source_order, len(self.text)


@contextlib.contextmanager
def open_cedict(path: Path) -> Iterator[TextIO]:
    """Open a plain CC-CEDICT file or the official single-file ZIP."""
    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        candidates = [
            entry
            for entry in archive.infolist()
            if not entry.is_dir() and entry.filename.lower().endswith(".u8")
        ]
        if len(candidates) != 1:
            archive.close()
            raise ValueError(
                f"expected exactly one .u8 file in {path}, found {len(candidates)}"
            )
        raw = archive.open(candidates[0], "r")
        stream = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        try:
            yield stream
        finally:
            stream.close()
            archive.close()
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            yield stream


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def clean_sense(raw: str) -> str:
    text = html.unescape(raw).strip()
    while True:
        cleaned = LEADING_LABEL.sub("", text, count=1)
        if cleaned == text:
            break
        text = cleaned.strip()
    text = TRADITIONAL_SIMPLIFIED_REFERENCE.sub(r"\2", text)
    text = PINYIN_REFERENCE.sub("", text)
    text = text.replace("|", "/")
    text = WHITESPACE.sub(" ", text).strip(" ;,.\t\r\n")
    return text


def atomic_senses(definitions: str, start_order: int) -> Iterator[Sense]:
    order = start_order
    for definition in definitions.split("/"):
        for part in definition.split(";"):
            if part.strip().startswith("CL:"):
                continue
            cleaned = clean_sense(part)
            if cleaned:
                yield Sense(cleaned, order)
                order += 1


def choose_gloss(
    senses: Iterable[Sense], max_chars: int = 72, max_senses: int = 2
) -> str:
    unique: dict[str, Sense] = {}
    for sense in senses:
        key = sense.text.casefold()
        current = unique.get(key)
        if current is None or sense.rank < current.rank:
            unique[key] = sense
    ranked = sorted(unique.values(), key=lambda item: item.rank)
    selected: list[str] = []
    for sense in ranked:
        candidate = "; ".join((*selected, sense.text))
        if len(candidate) <= max_chars:
            selected.append(sense.text)
        elif not selected:
            shortened = sense.text[: max_chars - 3].rsplit(" ", 1)[0].rstrip(" ;,.")
            selected.append((shortened or sense.text[: max_chars - 3]) + "...")
        if len(selected) >= max_senses:
            break
    return "; ".join(selected)


def build_pack(
    input_path: Path,
    output_path: Path,
    manifest_path: Path,
    language: str = "en",
) -> dict[str, object]:
    senses_by_word: DefaultDict[str, list[Sense]] = defaultdict(list)
    metadata: dict[str, str] = {}
    parsed_lines = 0
    skipped_lines = 0
    source_order = 0

    with open_cedict(input_path) as stream:
        for raw_line in stream:
            line = raw_line.rstrip("\r\n")
            if line.startswith("#! ") and "=" in line:
                key, value = line[3:].split("=", 1)
                metadata[key.strip()] = value.strip()
                continue
            if not line or line.startswith("#"):
                continue
            match = CEDICT_LINE.match(line)
            if not match:
                skipped_lines += 1
                continue
            parsed_lines += 1
            senses = list(atomic_senses(match.group("definitions"), source_order))
            source_order += max(1, len(senses))
            if not senses:
                continue
            for word in {match.group("traditional"), match.group("simplified")}:
                if word and len(word) <= 32 and "\t" not in word:
                    senses_by_word[word].extend(senses)

    rows: list[tuple[str, str]] = []
    for word in sorted(senses_by_word):
        gloss = choose_gloss(senses_by_word[word])
        if gloss:
            rows.append((word, gloss))

    input_hash = source_sha256(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# {FORMAT_VERSION}\n"
        f"# language={language}\n"
        "# source=CC-CEDICT (MDBG)\n"
        f"# source_sha256={input_hash}\n"
        f"# license=CC-BY-SA-4.0 {LICENSE_URL}\n"
    )
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(header)
        for word, gloss in rows:
            stream.write(f"{word}\t{gloss}\n")

    output_hash = source_sha256(output_path)
    manifest: dict[str, object] = {
        "format": FORMAT_VERSION,
        "language": language,
        "entries": len(rows),
        "parsed_source_entries": parsed_lines,
        "skipped_source_lines": skipped_lines,
        "source": {
            "name": "CC-CEDICT",
            "publisher": "MDBG",
            "version": metadata.get("version", "unknown"),
            "subversion": metadata.get("subversion", "unknown"),
            "date": metadata.get("date", "unknown"),
            "declared_entries": metadata.get("entries", "unknown"),
            "sha256": input_hash,
            "license": "CC-BY-SA-4.0",
            "license_url": LICENSE_URL,
        },
        "output": {
            "file": output_path.name,
            "sha256": output_hash,
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="CC-CEDICT .u8 or ZIP")
    parser.add_argument("--output", required=True, type=Path, help="output TSV path")
    parser.add_argument("--manifest", required=True, type=Path, help="output manifest JSON")
    parser.add_argument("--language", default="en", help="GlossPack language code")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    manifest = build_pack(args.input, args.output, args.manifest, args.language)
    print(
        f"built {manifest['entries']} {args.language} glosses: "
        f"{args.output} ({manifest['output']['sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
