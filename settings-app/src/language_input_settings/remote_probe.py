"""Minimal external-API connectivity probe (new capability, Part 2 item 7).

Sends exactly **one** small request to a configured OpenAI-compatible
``chat/completions`` endpoint and reports success, latency and, on failure, the
error text.  Design rules it follows:

* **No retry storm** -- a single attempt, a short timeout
  (:data:`PROBE_TIMEOUT`); no loops, no back-off.
* **``allow_http``** -- an ``http://`` endpoint is refused unless the app's
  network policy explicitly allows plaintext HTTP (the same ``allow_http`` flag
  :mod:`models_download` uses); an unsupported scheme is refused outright.
* **No API key leakage** -- the key is only ever sent in the ``Authorization``
  request header.  It is never put in a URL, logged, or returned; every string
  in the result is passed through :func:`redact`, which removes any occurrence
  of the key.

The probe is deliberately transport-only: it does not read or write any Rime
configuration and does not touch the app config.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["PROBE_TIMEOUT", "redact", "test_endpoint"]

#: Hard upper bound for the connectivity probe (seconds).
PROBE_TIMEOUT = 8.0

_MAX_BODY = 2048
_USER_AGENT = "language-input-settings/0.1.0 (+connectivity-probe)"
_DEFAULT_MODEL = "gpt-4.1-mini"
_KEY_PLACEHOLDER = "***"


def redact(text: object, api_key: str | None) -> str:
    """Return ``text`` with the API key (if any) replaced by ``***``."""
    if text is None:
        return ""
    value = str(text)
    if api_key:
        value = value.replace(api_key, _KEY_PLACEHOLDER)
    return value


def _decode(raw: object) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    return "" if raw is None else str(raw)


def _scheme_check(url: str, allow_http: bool) -> str | None:
    """Return an error message when the scheme is not allowed, else ``None``."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme == "https":
        return None
    if scheme == "http":
        if allow_http:
            return None
        return "端点使用明文 http://，但未允许 HTTP（allow_http=false）。"
    return f"端点协议不受支持：{scheme or '（未指定）'}。"


def test_endpoint(
    url: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    language: str | None = None,
    allow_http: bool = False,
    use_system_proxy: bool = False,
    timeout: float = PROBE_TIMEOUT,
) -> dict:
    """Perform one minimal request and return a structured result.

    Returns ``{"ok", "status", "latency_ms", "error", "detail"}``.  ``ok`` is
    True only for a 2xx response with no provider-level ``error`` object.  On
    failure ``error`` always carries a human message (key redacted) and
    ``detail`` may carry a bounded, redacted response snippet.
    """
    result: dict = {
        "ok": False,
        "status": None,
        "latency_ms": None,
        "error": None,
        "detail": None,
    }

    url = (url or "").strip()
    if not url:
        result["error"] = "未配置端点 URL。"
        return result
    scheme_error = _scheme_check(url, allow_http)
    if scheme_error is not None:
        result["error"] = scheme_error
        return result
    if not urllib.parse.urlsplit(url).netloc:
        result["error"] = "端点 URL 缺少主机名。"
        return result

    payload = {
        "model": (model or _DEFAULT_MODEL),
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", _USER_AGENT)
    if api_key:
        # Header only: the key never appears in the URL, the logs or the result.
        request.add_header("Authorization", f"Bearer {api_key}")

    handlers: list = []
    if not use_system_proxy:
        # Same measured policy as the model downloader: bypass the throttling
        # env proxy unless explicitly requested.
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)

    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        result["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
        result["status"] = exc.code
        try:
            raw = exc.read(_MAX_BODY)
        except Exception:  # noqa: BLE001 - best-effort body
            raw = b""
        result["error"] = redact(f"HTTP {exc.code} {exc.reason}".strip(), api_key)
        result["detail"] = redact(_decode(raw), api_key)
        return result
    except (
        urllib.error.URLError,
        socket.timeout,
        TimeoutError,
        ConnectionError,
        OSError,
    ) as exc:
        result["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
        result["error"] = redact(f"{type(exc).__name__}: {exc}", api_key)
        return result

    result["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    result["status"] = status
    text = _decode(body)

    if not (200 <= int(status) < 300):
        result["error"] = f"HTTP {status}"
        result["detail"] = redact(text, api_key)
        return result

    try:
        document = json.loads(text) if text else None
    except ValueError:
        document = None
    if isinstance(document, dict) and document.get("error"):
        result["error"] = redact(document["error"], api_key)
        return result

    result["ok"] = True
    usage = document.get("usage") if isinstance(document, dict) else None
    result["detail"] = "响应正常。" + (f"用量：{usage}" if usage else "")
    return result
