#!/usr/bin/env python3
"""Exercise the product M2M100 localhost host on the 120-item blind set."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping, Sequence

# Permit both `python -m scripts.benchmark_m2m100_host` and the more natural
# `python scripts/benchmark_m2m100_host.py` invocation from the repository root.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_local_model import ORDINARY_CATEGORIES, load_corpus
from scripts.language_input_model_host import is_unsafe_source


MODEL_ID = "m2m100-418m-int8"
MAX_GLOSS_CHARACTERS = 40


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nearest_percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    rank = max(1, math.ceil(len(values) * percentile))
    return sorted(values)[rank - 1]


def is_legal_gloss(value: object) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return bool(value) and len(value) <= MAX_GLOSS_CHARACTERS and not any(
        character in value
        for character in "\r\n\t"
    ) and not any(ord(character) < 32 for character in value)


class WorkingSetSampler:
    """Sample one Windows process without adding a third-party dependency."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        if os.name != "nt":
            return
        try:
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            handle = kernel32.OpenProcess(0x1000 | 0x0010, False, self.pid)
            if not handle:
                return
            try:
                counters = ProcessMemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(
                    handle,
                    ctypes.byref(counters),
                    counters.cb,
                ):
                    self.peak_bytes = max(
                        self.peak_bytes,
                        int(counters.PeakWorkingSetSize),
                        int(counters.WorkingSetSize),
                    )
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(0.05)
        self._sample()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return self.peak_bytes or None


class LocalHost:
    def __init__(
        self,
        executable: Path,
        quick_catalog: Path,
        m2m100_catalog: Path,
        model_root: Path,
        *,
        threads: int,
    ) -> None:
        token = secrets.token_urlsafe(48)
        self.process = subprocess.Popen(
            [
                str(executable),
                "--serve",
                "--catalog",
                str(quick_catalog),
                "--m2m100-catalog",
                str(m2m100_catalog),
                "--models",
                str(model_root),
                "--port",
                "0",
                "--token",
                token,
                "--idle-seconds",
                "900",
                "--threads",
                str(threads),
            ],
            cwd=str(executable.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.token = token
        self._ready_queue: queue.Queue[str] = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            daemon=True,
        )
        self._stdout_thread.start()
        self.memory = WorkingSetSampler(self.process.pid)
        self.memory.start()
        self.peak_working_set_bytes: int | None = None
        try:
            line = self._ready_queue.get(timeout=60).strip()
        except queue.Empty as error:
            self.close()
            raise RuntimeError("local host did not become ready") from error
        fields = line.split()
        if len(fields) != 2 or fields[0] != "READY":
            self.close()
            raise RuntimeError("local host returned an invalid ready line")
        try:
            self.port = int(fields[1])
        except ValueError as error:
            self.close()
            raise RuntimeError("local host returned an invalid port") from error

    def _read_stdout(self) -> None:
        if self.process.stdout is None:
            return
        for line in self.process.stdout:
            self._ready_queue.put(line)

    def post(
        self,
        language: str,
        words: Sequence[str],
    ) -> tuple[int, Mapping[str, object], float]:
        payload = {
            "model": "local",
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "target_language": language,
                            "words": list(words),
                            "model": MODEL_ID,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            ],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                document = json.loads(response.read().decode("utf-8"))
                status = int(response.status)
        except urllib.error.HTTPError as error:
            try:
                document = json.loads(error.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                document = {}
            status = int(error.code)
        except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            document = {}
            status = 0
        elapsed_ms = (time.perf_counter() - started) * 1000
        return status, document if isinstance(document, dict) else {}, elapsed_ms

    @staticmethod
    def _extract_content(document: Mapping[str, object]) -> dict[str, object] | None:
        try:
            choices = document["choices"]
            first = choices[0]  # type: ignore[index]
            message = first["message"]  # type: ignore[index]
            content = message["content"]  # type: ignore[index]
            result = json.loads(content)
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return result if isinstance(result, dict) else None

    def translate_map(
        self,
        language: str,
        words: Sequence[str],
    ) -> tuple[int, dict[str, object] | None, float]:
        status, document, elapsed_ms = self.post(language, words)
        return status, self._extract_content(document) if status == 200 else None, elapsed_ms

    def close(self) -> None:
        peak = self.memory.stop()
        self.peak_working_set_bytes = peak
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()


def check_batch(
    entries: Sequence[Mapping[str, object]],
    result: dict[str, object] | None,
) -> tuple[int, int, int, int, str | None]:
    """Return safe count, legal count, suppressed count, ordinary legal count, error."""
    if result is None:
        return 0, 0, 0, 0, "invalid-response"
    requested = [str(row["text"]) for row in entries]
    unsafe = {word for word in requested if is_unsafe_source(word)}
    safe = set(requested) - unsafe
    if any(not isinstance(key, str) or key not in set(requested) for key in result):
        return len(safe), 0, 0, 0, "unexpected-output-key"
    suppressed = sum(word not in result for word in unsafe)
    if suppressed != len(unsafe):
        return len(safe), 0, suppressed, 0, "unsafe-source-not-suppressed"
    legal = 0
    ordinary_legal = 0
    ordinary = ORDINARY_CATEGORIES
    for row in entries:
        word = str(row["text"])
        if word in safe and is_legal_gloss(result.get(word)):
            legal += 1
            if row["category"] in ordinary:
                ordinary_legal += 1
        elif word in safe:
            return len(safe), legal, suppressed, ordinary_legal, "missing-or-illegal-output"
    return len(safe), legal, suppressed, ordinary_legal, None


def run_corpus(
    host: LocalHost,
    entries: Sequence[Mapping[str, object]],
    language: str,
    *,
    batch_size: int,
) -> dict[str, object]:
    ordinary_count = sum(row["category"] in ORDINARY_CATEGORIES for row in entries)
    safe_count = 0
    legal_count = 0
    suppressed_count = 0
    ordinary_legal = 0
    errors: dict[str, int] = {}
    latencies: list[float] = []
    warm_nine: list[float] = []
    cold_ms: float | None = None
    batches = 0
    for batch_index in range(0, len(entries), batch_size):
        batch = entries[batch_index : batch_index + batch_size]
        status, result, elapsed_ms = host.translate_map(
            language,
            [str(row["text"]) for row in batch],
        )
        batches += 1
        if cold_ms is None:
            cold_ms = elapsed_ms
        if status == 200:
            latencies.append(elapsed_ms)
            if len(batch) == 9 and batches > 1:
                warm_nine.append(elapsed_ms)
        else:
            reason = f"http-{status}"
            errors[reason] = errors.get(reason, 0) + 1
            continue
        safe, legal, suppressed, ordinary, error = check_batch(batch, result)
        safe_count += safe
        legal_count += legal
        suppressed_count += suppressed
        ordinary_legal += ordinary
        if error is not None:
            errors[error] = errors.get(error, 0) + 1
    return {
        "language": language,
        "requested": len(entries),
        "safe_submitted": safe_count,
        "legal_outputs": legal_count,
        "legal_output_rate": legal_count / safe_count if safe_count else 0.0,
        "unsafe_suppressed": suppressed_count,
        "ordinary_requested": ordinary_count,
        "ordinary_legal_outputs": ordinary_legal,
        "ordinary_coverage": ordinary_legal / ordinary_count if ordinary_count else 0.0,
        "batches": batches,
        "errors": errors,
        "cold_request_ms": cold_ms,
        "warm_nine_candidate_latency_ms": {
            "samples": len(warm_nine),
            "p50": nearest_percentile(warm_nine, 0.50),
            "p95": nearest_percentile(warm_nine, 0.95),
            "maximum": max(warm_nine) if warm_nine else None,
        },
        "all_successful_request_latency_ms": {
            "samples": len(latencies),
            "p50": nearest_percentile(latencies, 0.50),
            "p95": nearest_percentile(latencies, 0.95),
            "maximum": max(latencies) if latencies else None,
        },
        "peak_working_set_bytes": None,
    }


def load_security_fixture(path: Path) -> list[dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("security fixture has no entries")
    checked: list[dict[str, object]] = []
    for row in entries:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("text"), str)
            or row.get("expected") not in {"suppress", "translate"}
        ):
            raise ValueError("security fixture entry is invalid")
        checked.append(row)
    return checked


def run_security(
    host: LocalHost,
    entries: Sequence[Mapping[str, object]],
    *,
    batch_size: int,
) -> dict[str, object]:
    expected_suppress = sum(row["expected"] == "suppress" for row in entries)
    expected_allow = sum(row["expected"] == "translate" for row in entries)
    actual_suppress = 0
    allowed_legal = 0
    errors: dict[str, int] = {}
    for start in range(0, len(entries), batch_size):
        batch = entries[start : start + batch_size]
        status, result, _ = host.translate_map(
            "en",
            [str(row["text"]) for row in batch],
        )
        if status != 200 or result is None:
            reason = f"http-{status}" if status != 200 else "invalid-response"
            errors[reason] = errors.get(reason, 0) + len(batch)
            continue
        for row in batch:
            word = str(row["text"])
            present = word in result
            legal = present and is_legal_gloss(result[word])
            if row["expected"] == "suppress":
                actual_suppress += not present
                if present:
                    errors["unsafe-output-present"] = errors.get("unsafe-output-present", 0) + 1
            elif legal:
                allowed_legal += 1
            else:
                errors["safe-output-missing-or-illegal"] = errors.get(
                    "safe-output-missing-or-illegal", 0
                ) + 1
    return {
        "requested": len(entries),
        "expected_suppress": expected_suppress,
        "suppressed": actual_suppress,
        "suppression_recall": (
            actual_suppress / expected_suppress if expected_suppress else 0.0
        ),
        "expected_allow": expected_allow,
        "legal_allowed": allowed_legal,
        "allow_rate": allowed_legal / expected_allow if expected_allow else 0.0,
        "errors": errors,
        "peak_working_set_bytes": None,
    }


def run_missing_model(
    executable: Path,
    quick_catalog: Path,
    m2m100_catalog: Path,
    *,
    work_root: Path,
    threads: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="m2m100-missing-", dir=work_root) as temporary:
        model_root = Path(temporary)
        host = LocalHost(
            executable,
            quick_catalog,
            m2m100_catalog,
            model_root,
            threads=threads,
        )
        try:
            status, document, _ = host.post("en", ["未导入模型"])
        finally:
            host.close()
    error = document.get("error") if isinstance(document, dict) else None
    return {
        "http_status": status,
        "error": error,
        "passed": status == 409 and error == "missing-model-component",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-executable", required=True, type=Path)
    parser.add_argument("--quick-catalog", required=True, type=Path)
    parser.add_argument("--m2m100-catalog", required=True, type=Path)
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--security-fixture", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=9)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.batch_size <= 9 or args.threads < 1:
        raise ValueError("invalid batch size or thread count")
    entries = load_corpus(args.corpus)
    if len(entries) != 120:
        raise ValueError(f"expected the existing 120-item blind set, got {len(entries)}")
    security_entries = load_security_fixture(args.security_fixture)
    for path in (
        args.host_executable,
        args.quick_catalog,
        args.m2m100_catalog,
        args.models,
        args.corpus,
        args.security_fixture,
        args.work_root,
    ):
        if not path.exists():
            raise FileNotFoundError(path)

    languages: dict[str, dict[str, object]] = {}
    for language in ("en", "ja", "es"):
        host = LocalHost(
            args.host_executable,
            args.quick_catalog,
            args.m2m100_catalog,
            args.models,
            threads=args.threads,
        )
        try:
            result = run_corpus(
                host,
                entries,
                language,
                batch_size=args.batch_size,
            )
        finally:
            host.close()
        result["peak_working_set_bytes"] = host.peak_working_set_bytes
        languages[language] = result

    security_host = LocalHost(
        args.host_executable,
        args.quick_catalog,
        args.m2m100_catalog,
        args.models,
        threads=args.threads,
    )
    try:
        security = run_security(
            security_host,
            security_entries,
            batch_size=args.batch_size,
        )
    finally:
        security_host.close()
    security["peak_working_set_bytes"] = security_host.peak_working_set_bytes

    missing = run_missing_model(
        args.host_executable,
        args.quick_catalog,
        args.m2m100_catalog,
        work_root=args.work_root,
        threads=args.threads,
    )
    host_stat = args.host_executable.stat()
    summary = {
        "schema_version": 1,
        "model_id": MODEL_ID,
        "host": {
            "path": str(args.host_executable),
            "size": host_stat.st_size,
            "sha256": sha256_file(args.host_executable),
        },
        "corpus": {
            "path": str(args.corpus),
            "source_count": len(entries),
            "sha256": sha256_file(args.corpus),
        },
        "settings": {
            "batch_size": args.batch_size,
            "threads": args.threads,
            "target_languages": list(languages),
        },
        "languages": languages,
        "security": security,
        "missing_model": missing,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.summary.with_name(f".{args.summary.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, args.summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if missing["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
