#!/usr/bin/env python3
"""Deterministic patcher for the frozen ``LanguageInputModelHost`` source.

This tool reads the frozen engine source
(``<engine-root>/scripts/language_input_model_host.py``) and writes a patched
copy that memoises component integrity verification per process and removes the
``/health`` double-hash.  It never edits the frozen tree: the source is only
read, and the patched copy is written to ``--output``.

The patcher is anchor based.  Every anchor must match the frozen source exactly
once; otherwise the tool exits non-zero and writes nothing.  It prints a
unified diff of the applied change so the transformation is reviewable.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import sys
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
DEFAULT_ENGINE_ROOT = Path(r"F:\documents\software\languageInput")
DEFAULT_SOURCE = DEFAULT_ENGINE_ROOT / "scripts" / "language_input_model_host.py"
DEFAULT_OUTPUT = HERE / "build" / "src" / "language_input_model_host.py"

# SHA-256 of the frozen source this patch was authored against.  Recorded for
# provenance; the anchors below are the actual enforcement.
KNOWN_SOURCE_SHA256 = "6d4b2ad20abce3a4a95a5d355bad8640517a79486cf6c69e007989ce84c0c7f0"


# ---------------------------------------------------------------------------
# Patch pieces
# ---------------------------------------------------------------------------

# 1. Install the per-process verification cache and rename the QuickMT loader
#    to an uncached core.  The cached public wrapper is added next to
#    ``installed_components`` (piece 2) so it exists before its callers run.
_CACHE_INFRA = '''_VERIFIED_COMPONENT_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}


def _component_stat_token(component_root: Path) -> tuple[object, ...]:
    """Cheap, content-free fingerprint used to invalidate the verified cache.

    The token combines the root directory mtime with a recursive listing of
    ``(relative path, size, mtime_ns)`` for every entry below the component.
    Building it only stats the tree; it never reads file contents, so it is
    orders of magnitude cheaper than re-hashing ~1.2 GB of model data.
    """
    entries: list[tuple[str, int, int]] = []
    total = 0
    try:
        for current, dir_names, file_names in os.walk(component_root):
            current_path = Path(current)
            for name in dir_names + file_names:
                child = current_path / name
                stat = child.lstat()
                entries.append(
                    (
                        str(child.relative_to(component_root)),
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
                )
                total += 1
        root_stat = component_root.stat()
    except OSError:
        return ("unreadable", str(component_root))
    return (root_stat.st_mtime_ns, total, tuple(sorted(entries)))


def _invalidate_component_cache(component_root: Path) -> None:
    """Explicitly drop a cached verification result for one component."""
    try:
        key = str(component_root.resolve(strict=True))
    except OSError:
        key = str(component_root)
    _VERIFIED_COMPONENT_CACHE.pop(key, None)


def _cached_load(component_root: Path, loader):
    """Return a verified component record, re-hashing at most once per process.

    The first call for a component performs the full size + SHA-256 check.  The
    result is memoised against a cheap stat token; later calls re-stat the tree
    and only re-verify when the token changed (edit/rename/add/remove).
    """
    try:
        key = str(component_root.resolve(strict=True))
    except OSError:
        return loader(component_root)
    token = _component_stat_token(component_root)
    cached = _VERIFIED_COMPONENT_CACHE.get(key)
    if cached is not None and cached[0] == token:
        return cached[1]
    record = loader(component_root)
    _VERIFIED_COMPONENT_CACHE[key] = (token, record)
    return record


def _load_installed_component_uncached(component_root: Path) -> dict[str, object]:
    component_root = component_root.resolve(strict=True)
    if not component_root.is_dir() or component_root.is_symlink():
        raise ValueError("installed component root is invalid")
'''

_QUICKMT_LOADER_ANCHOR = '''def load_installed_component(component_root: Path) -> dict[str, object]:
    component_root = component_root.resolve(strict=True)
    if not component_root.is_dir() or component_root.is_symlink():
        raise ValueError("installed component root is invalid")
'''

_QUICKMT_WRAPPER_ANCHOR = '''def installed_components(model_root: Path) -> dict[str, dict[str, object]]:
'''

_QUICKMT_WRAPPER = '''def load_installed_component(component_root: Path) -> dict[str, object]:
    return _cached_load(component_root, _load_installed_component_uncached)


def installed_components(model_root: Path) -> dict[str, dict[str, object]]:
'''

_M2M_LOADER_ANCHOR = '''def load_installed_m2m100_component(component_root: Path) -> dict[str, object]:
    component_root = component_root.resolve(strict=True)
    if not component_root.is_dir() or component_root.is_symlink():
        raise ValueError("installed M2M100 component root is invalid")
'''

_M2M_LOADER_REWRITE = '''def _load_installed_m2m100_component_uncached(component_root: Path) -> dict[str, object]:
    component_root = component_root.resolve(strict=True)
    if not component_root.is_dir() or component_root.is_symlink():
        raise ValueError("installed M2M100 component root is invalid")
'''

_M2M_WRAPPER_ANCHOR = '''def installed_m2m100_components(model_root: Path) -> dict[str, dict[str, object]]:
'''

_M2M_WRAPPER = '''def load_installed_m2m100_component(component_root: Path) -> dict[str, object]:
    return _cached_load(component_root, _load_installed_m2m100_component_uncached)


def installed_m2m100_components(model_root: Path) -> dict[str, dict[str, object]]:
'''

# 2. Fix the /health double-hash: call QuickMT available_languages() once.
_HEALTH_ANCHOR = '''        self.server.last_request = time.monotonic()
        m2m100_languages = (
            self.server.m2m100.available_languages() if self.server.m2m100 else []
        )
        self._json(
            200,
            {
                "status": "ok",
                "languages": self.server.quickmt.available_languages(),
                "models": {
                    QUICKMT_MODEL_ID: {"languages": self.server.quickmt.available_languages()},
                    M2M100_MODEL_ID: {"languages": m2m100_languages},
                },
            },
        )
'''

_HEALTH_REWRITE = '''        self.server.last_request = time.monotonic()
        quickmt_languages = self.server.quickmt.available_languages()
        m2m100_languages = (
            self.server.m2m100.available_languages() if self.server.m2m100 else []
        )
        self._json(
            200,
            {
                "status": "ok",
                "languages": quickmt_languages,
                "models": {
                    QUICKMT_MODEL_ID: {"languages": quickmt_languages},
                    M2M100_MODEL_ID: {"languages": m2m100_languages},
                },
            },
        )
'''


PATCHES: tuple[tuple[str, str, str], ...] = (
    ("install verification cache + rename QuickMT loader", _QUICKMT_LOADER_ANCHOR, _CACHE_INFRA),
    ("add cached QuickMT loader wrapper", _QUICKMT_WRAPPER_ANCHOR, _QUICKMT_WRAPPER),
    ("rename M2M100 loader to uncached core", _M2M_LOADER_ANCHOR, _M2M_LOADER_REWRITE),
    ("add cached M2M100 loader wrapper", _M2M_WRAPPER_ANCHOR, _M2M_WRAPPER),
    ("fix /health double-hash", _HEALTH_ANCHOR, _HEALTH_REWRITE),
)


def apply_patches(source_text: str) -> str:
    """Apply every patch, requiring each anchor to occur exactly once."""
    text = source_text
    for name, anchor, replacement in PATCHES:
        found = text.count(anchor)
        if found != 1:
            raise SystemExit(
                f"error: anchor for '{name}' matched {found} times (expected exactly 1); "
                "the frozen source does not match the expected revision, refusing to "
                "produce a wrong patched file."
            )
        text = text.replace(anchor, replacement, 1)
    return text


def unified_diff(before: str, after: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label} (patched)",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report the diff and exit without writing the patched file",
    )
    args = parser.parse_args(argv)

    source = args.source
    if not source.is_file():
        raise SystemExit(f"error: frozen source not found: {source}")
    raw = source.read_bytes()
    before = raw.decode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    print(f"source: {source}")
    print(f"source sha256: {digest}")
    if digest != KNOWN_SOURCE_SHA256:
        print(
            "warning: source sha256 differs from the revision this patch was authored "
            f"against ({KNOWN_SOURCE_SHA256}); anchor checks still apply."
        )

    after = apply_patches(before)
    diff = unified_diff(before, after, source.name)
    print(diff if diff else "(no changes)")

    if args.check:
        print("check only: no file written")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(after, encoding="utf-8", newline="\n")
    out_digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"wrote: {args.output}")
    print(f"patched sha256: {out_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
