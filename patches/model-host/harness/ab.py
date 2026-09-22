#!/usr/bin/env python3
"""A/B latency + correctness harness for LanguageInputModelHost.

Runs the *currently installed* host and the *patched* host sequentially with the
same flags WeaselServer uses (plus a short ``--idle-seconds``), and reports:

* ``/health`` latency for both;
* warm zh->en translation latency (>=5 samples) for both;
* byte-level parity of the zh->en / zh->ja / zh->es JSON responses.

Every host is launched against the live ``%APPDATA%\\Rime`` model root in
read-only mode; this harness never writes to it.  Hosts are always terminated in
a ``finally`` block so none is left running.

Stdlib only.  Run with any Python 3.11+.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

DEFAULT_CATALOG = Path(
    r"C:\Program Files\Rime\weasel-0.1.0\data\language_input\models\packs-v2.json"
)
DEFAULT_M2M100_CATALOG = Path(
    r"C:\Program Files\Rime\weasel-0.1.0\data\language_input\models\m2m100-packs-v1.json"
)
DEFAULT_MODELS = Path.home() / "AppData" / "Roaming" / "Rime" / "language_input" / "models"
DEFAULT_CURRENT = Path(r"C:\Program Files\Rime\weasel-0.1.0\LanguageInputModelHost.exe")
DEFAULT_PATCHED = (
    Path(__file__).resolve().parent.parent / "dist" / "LanguageInputModelHost" / "LanguageInputModelHost.exe"
)
WORD_SETS = {
    "zh-en": ("en", ["打字", "搭子", "大"]),
    "zh-ja": ("ja", ["打字", "搭子", "大"]),
    "zh-es": ("es", ["打字", "搭子", "大"]),
}


def build_request(language: str, words: Sequence[str]) -> bytes:
    payload = json.dumps(
        {"target_language": language, "words": list(words), "model": "quickmt-gloss-route-v2"},
        ensure_ascii=False,
    )
    body = {
        "model": "quickmt-gloss-route-v2",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate each Chinese word supplied as JSON data into one concise "
                    "dictionary gloss in the requested language. Return only one JSON "
                    "object whose keys exactly match the supplied words."
                ),
            },
            {"role": "user", "content": payload},
        ],
        "temperature": 0,
        "max_tokens": 256,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def http_get(url: str, token: str, timeout: float = 120.0) -> tuple[int, bytes, float]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        elapsed = time.perf_counter() - start
        return response.status, body, elapsed


def http_post(url: str, token: str, body: bytes, timeout: float = 180.0) -> tuple[int, bytes, float]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        elapsed = time.perf_counter() - start
        return response.status, data, elapsed


def wait_ready(base: str, token: str, process: subprocess.Popen, timeout: float = 90.0) -> float:
    deadline = time.perf_counter() + timeout
    last_error: object = None
    while time.perf_counter() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"host exited early with code {process.returncode}")
        try:
            status, _, _ = http_get(base + "/health", token, timeout=30.0)
            if status == 200:
                return time.perf_counter()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = error
        time.sleep(0.1)
    raise RuntimeError(f"host did not become ready: {last_error}")


def run_host(
    *,
    name: str,
    exe: Path,
    port: int,
    token: str,
    catalog: Path,
    m2m100_catalog: Path,
    models: Path,
    idle_seconds: int,
    samples: int,
) -> dict[str, Any]:
    command = [
        str(exe),
        "--serve",
        "--catalog",
        str(catalog),
        "--models",
        str(models),
        "--port",
        str(port),
        "--token",
        token,
        "--idle-seconds",
        str(idle_seconds),
    ]
    if m2m100_catalog.is_file():
        command += ["--m2m100-catalog", str(m2m100_catalog)]

    print(f"[{name}] launching: {' '.join(command)}", flush=True)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    result: dict[str, Any] = {"name": name, "exe": str(exe), "port": port}
    try:
        launch_start = time.perf_counter()
        wait_ready(base, token, process)
        result["startup_s"] = time.perf_counter() - launch_start
        print(f"[{name}] ready in {result['startup_s']:.3f}s", flush=True)

        # First /health pays the one-time verification hashing (cold process).
        _, _, first_health = http_get(base + "/health", token)
        result["health_first_s"] = first_health

        health_samples = []
        for _ in range(samples):
            _, _, elapsed = http_get(base + "/health", token)
            health_samples.append(elapsed)
        result["health_samples_s"] = health_samples

        # Warm-up translation (loads the model into memory), then timed samples.
        responses: dict[str, Any] = {}
        warm_samples: list[float] = []
        for key, (language, words) in WORD_SETS.items():
            body = build_request(language, words)
            status, data, elapsed = http_post(base + "/v1/chat/completions", token, body)
            responses[key] = {
                "status": status,
                "json": json.loads(data.decode("utf-8")),
            }
            if key == "zh-en":
                result["zh_en_first_s"] = elapsed
                for _ in range(samples):
                    status, data, elapsed = http_post(base + "/v1/chat/completions", token, body)
                    responses[key] = {
                        "status": status,
                        "json": json.loads(data.decode("utf-8")),
                    }
                    warm_samples.append(elapsed)
        result["warm_samples_s"] = warm_samples
        result["responses"] = responses
        result["health_running"] = True
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
        result["exit_code"] = process.returncode
        # Confirm the port is closed.
        time.sleep(0.3)
        try:
            http_get(base + "/health", token, timeout=2.0)
            result["port_still_open"] = True
        except Exception:
            result["port_still_open"] = False
    return result


def summarize(values: Sequence[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-exe", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--patched-exe", type=Path, default=DEFAULT_PATCHED)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--m2m100-catalog", type=Path, default=DEFAULT_M2M100_CATALOG)
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--token", default="a" * 64)
    parser.add_argument("--idle-seconds", type=int, default=20)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--results", type=Path, default=Path(__file__).resolve().parent / "results")
    parser.add_argument("--only", choices=["current", "patched"], default=None)
    args = parser.parse_args(argv)

    args.results.mkdir(parents=True, exist_ok=True)
    plan = []
    if args.only != "patched":
        plan.append(("current", args.current_exe, 51234))
    if args.only != "current":
        plan.append(("patched", args.patched_exe, 51235))

    results = []
    for name, exe, port in plan:
        if not exe.is_file():
            raise SystemExit(f"host executable not found: {exe}")
        record = run_host(
            name=name,
            exe=exe,
            port=port,
            token=args.token,
            catalog=args.catalog,
            m2m100_catalog=args.m2m100_catalog,
            models=args.models,
            idle_seconds=args.idle_seconds,
            samples=args.samples,
        )
        results.append(record)
        (args.results / f"{name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    print("\n=== latency ===")
    print(
        f"{'host':<8} {'startup_s':>10} {'health_first':>12} "
        f"{'health med':>11} {'health min':>11} {'health max':>11} "
        f"{'warm med':>9} {'warm min':>9} {'warm max':>9}"
    )
    for record in results:
        health = summarize(record["health_samples_s"])
        warm = summarize(record["warm_samples_s"]) if record["warm_samples_s"] else None
        warm_text = (
            f"{warm['median']*1000:9.1f} {warm['min']*1000:9.1f} {warm['max']*1000:9.1f}"
            if warm
            else f"{'-':>9} {'-':>9} {'-':>9}"
        )
        print(
            f"{record['name']:<8} {record['startup_s']:10.3f} "
            f"{record['health_first_s']*1000:10.1f}ms "
            f"{health['median']*1000:9.1f}ms {health['min']*1000:9.1f}ms {health['max']*1000:9.1f}ms "
            f"{warm_text}"
        )

    if len(results) == 2:
        current, patched = results
        print("\n=== correctness parity (byte-identical JSON responses) ===")
        for key in WORD_SETS:
            current_response = current["responses"].get(key)
            patched_response = patched["responses"].get(key)
            same = current_response == patched_response
            print(f"{key}: {'IDENTICAL' if same else 'DIFFERENT'}")
            if not same:
                print(f"  current: {json.dumps(current_response, ensure_ascii=False)}")
                print(f"  patched: {json.dumps(patched_response, ensure_ascii=False)}")
        (args.results / "parity.json").write_text(
            json.dumps(
                {
                    key: {
                        "identical": current["responses"].get(key) == patched["responses"].get(key)
                    }
                    for key in WORD_SETS
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
