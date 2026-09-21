"""Model file downloading (M3) — pure standard library.

No ``requests`` / ``huggingface_hub`` dependency.  Uses ``urllib.request`` with:

* HTTPS enforcement (never downgrades to ``http``, including on redirects,
  unless ``allow_http=True`` is explicitly requested);
* resumable downloads via HTTP ``Range`` on an existing ``<dest>.part`` file;
* atomic completion (``os.replace``), size + SHA-256 verification, and
  ``.part`` cleanup on integrity failure;
* a free-disk-space precheck and a process-wide "one download at a time" guard;
* proxy bypass by default.

**Proxy bypass (measured reason).**  This machine configures
``HTTP(S)_PROXY = ALL_PROXY = http://127.0.0.1:7898`` in the environment.
``urllib`` honours those variables by default and the proxy throttles
Hugging Face traffic to ~43 KB/s, while a direct connection measured
~8.8 MB/s (design doc §7.3 / M0, decision #17).  Downloads therefore install
``ProxyHandler({})`` unless ``use_system_proxy`` is explicitly enabled.

Downloads write to a caller-provided staging directory; nothing here touches
``%APPDATA%\\Rime``.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .models_catalog import ModelComponent, download_metadata

__all__ = [
    "DownloadError",
    "DownloadInProgress",
    "IntegrityError",
    "SizeMismatch",
    "ChecksumMismatch",
    "ProgressCallback",
    "sha256_file",
    "resolve_url",
    "download_file",
    "download_component",
]

ProgressCallback = Callable[[int, "int | None", str], None]

_CHUNK = 256 * 1024
_USER_AGENT = "language-input-settings/0.1.0 (+model-download)"
_DEFAULT_TIMEOUT = 30
_DISK_SLACK = 16 * 1024 * 1024  # keep at least this much free after download

#: Process-wide guard: refuse concurrent downloads.
download_lock = threading.Lock()


class DownloadError(RuntimeError):
    """Generic download failure."""


class DownloadInProgress(DownloadError):
    """Another download is already running in this process."""


class IntegrityError(DownloadError):
    """Downloaded bytes failed verification."""


class SizeMismatch(IntegrityError):
    """Downloaded size did not match the expected size."""


class ChecksumMismatch(IntegrityError):
    """Downloaded SHA-256 did not match the expected digest."""


def sha256_file(path: Path | str) -> str:
    """SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- URL / TLS helpers ------------------------------------------------------

class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects that would downgrade away from HTTPS."""

    def __init__(self, allow_http: bool) -> None:
        self.allow_http = allow_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if scheme != "https" and not self.allow_http:
            raise urllib.error.HTTPError(
                newurl, code, "refusing non-https redirect", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(
    allow_http: bool, use_system_proxy: bool = False
) -> urllib.request.OpenerDirector:
    """Build an opener, bypassing env proxies unless explicitly requested."""
    handlers: list = []
    if not use_system_proxy:
        # An empty ProxyHandler disables urllib's default env-proxy discovery
        # (HTTP_PROXY / HTTPS_PROXY / ALL_PROXY).  See the module docstring for
        # the measured ~200x throttle this avoids.
        handlers.append(urllib.request.ProxyHandler({}))
    handlers.append(_HttpsOnlyRedirect(allow_http))
    return urllib.request.build_opener(*handlers)


def _check_scheme(url: str, allow_http: bool) -> None:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme == "https":
        return
    if scheme == "http" and allow_http:
        return
    raise DownloadError(
        f"refusing to download over {scheme or 'unknown'} URL (allow_http={allow_http}): {url}"
    )


_DEFAULT_BASE_URL = "https://huggingface.co"


def resolve_url(
    base_or_override: str | None,
    repo: str,
    revision: str,
    path: str,
    *,
    base_url: str | None = None,
) -> str:
    """Build the download URL for one repository file.

    * A template containing ``{base_url}`` / ``{repository}`` / ``{repo}`` /
      ``{revision}`` / ``{path}`` is formatted (``{base_url}`` comes from the
      ``base_url`` keyword, e.g. the app-owned metadata).
    * An absolute URL that already ends with ``path`` is returned unchanged
      (per-file override).
    * Otherwise it is treated as a base and appended with the standard
      Hugging Face ``resolve`` form.
    """
    resolved_base = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    if base_or_override:
        if any(
            token in base_or_override
            for token in ("{path}", "{revision}", "{repo", "{repository", "{base_url")
        ):
            return base_or_override.format(
                base_url=resolved_base,
                repository=repo,
                repo=repo,
                revision=revision,
                path=path,
            )
        if base_or_override.startswith(("http://", "https://")):
            if base_or_override.rstrip("/").endswith(path):
                return base_or_override
            base = base_or_override.rstrip("/")
            return f"{base}/{repo}/resolve/{revision}/{path}?download=true"
    return f"{resolved_base}/{repo}/resolve/{revision}/{path}?download=true"


# --- single-file download ---------------------------------------------------

def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def download_file(
    url: str,
    dest: Path | str,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    progress: ProgressCallback | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    resume: bool = True,
    allow_http: bool = False,
    use_system_proxy: bool = False,
) -> dict:
    """Download ``url`` to ``dest`` with verification.

    Resumes from ``<dest>.part`` when present (``Range``).  On integrity
    failure the ``.part`` file is deleted; on a transient network error it is
    kept so a later call can resume.  Returns a small stats dict.

    ``use_system_proxy`` defaults to ``False`` so the environment's throttling
    proxy is bypassed (see the module docstring).
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _check_scheme(url, allow_http)

    part = dest.with_name(dest.name + ".part")
    resume_from = part.stat().st_size if (resume and part.is_file()) else 0
    started = time.monotonic()

    try:
        written, resumed = _download_once(
            url,
            part,
            resume_from,
            timeout,
            allow_http,
            progress,
            expected_size,
            use_system_proxy,
        )
    except IntegrityError:
        _discard(part)
        raise
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        socket.timeout,
        TimeoutError,
        ConnectionError,
        http.client.HTTPException,
    ) as exc:
        # Keep the partial file so the next call can resume.
        raise DownloadError(f"download failed ({url}): {exc}") from exc

    actual_size = part.stat().st_size
    digest = sha256_file(part)

    if expected_size is not None and actual_size != expected_size:
        _discard(part)
        raise SizeMismatch(
            f"{dest.name}: size mismatch (expected {expected_size}, got {actual_size})"
        )
    if expected_sha256 is not None and digest.lower() != expected_sha256.lower():
        _discard(part)
        raise ChecksumMismatch(
            f"{dest.name}: sha256 mismatch (expected {expected_sha256}, got {digest})"
        )

    os.replace(part, dest)
    elapsed = max(time.monotonic() - started, 1e-9)
    return {
        "path": str(dest),
        "size": actual_size,
        "sha256": digest,
        "resumed_from": resumed,
        "elapsed_seconds": round(elapsed, 3),
        "bytes_per_second": round(actual_size / elapsed, 1),
    }


def _download_once(
    url: str,
    part: Path,
    resume_from: int,
    timeout: float,
    allow_http: bool,
    progress: ProgressCallback | None,
    expected_size: int | None,
    use_system_proxy: bool,
) -> tuple[int, int]:
    """Stream one URL into ``part``; return ``(total_written, resumed_bytes)``."""
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
    )
    if resume_from:
        request.add_header("Range", f"bytes={resume_from}-")

    opener = _opener(allow_http, use_system_proxy)
    with opener.open(request, timeout=timeout) as response:
        status = getattr(response, "status", None) or response.getcode()
        resumed = resume_from
        if resume_from and status != 206:
            # Server ignored the Range request; restart from scratch.
            resumed = 0
        mode = "ab" if resumed else "wb"

        length_header = response.headers.get("Content-Length")
        remaining = int(length_header) if length_header and length_header.isdigit() else None
        if expected_size is not None:
            total: int | None = expected_size
        elif remaining is not None:
            total = resumed + remaining
        else:
            total = None

        written = resumed
        with open(part, mode) as output:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                output.write(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(written, total, str(part))

        if remaining is not None and (written - resumed) != remaining:
            raise SizeMismatch(
                f"short read for {part.name}: got {written - resumed} of {remaining} bytes"
            )
        return written, resumed


# --- component download -----------------------------------------------------

def _component_sources(sources: dict, component_id: str) -> dict:
    overrides = sources.get("components")
    if isinstance(overrides, dict):
        value = overrides.get(component_id)
        if isinstance(value, dict):
            return value
    return {}


def _url_for(sources: dict, override: dict, component: ModelComponent, path: str) -> str:
    files = override.get("files")
    if isinstance(files, dict) and path in files:
        return str(files[path])
    base = (
        override.get("url_template")
        or override.get("base_url")
        or sources.get("url_template")
        or sources.get("base_url")
    )
    base_url = override.get("base_url") or sources.get("base_url") or _DEFAULT_BASE_URL
    return resolve_url(
        base,
        component.repository or "",
        component.revision or "",
        path,
        base_url=base_url,
    )


def download_component(
    component: ModelComponent,
    dest_dir: Path | str,
    sources: dict | None = None,
    progress: ProgressCallback | None = None,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    resume: bool = True,
) -> dict:
    """Download all files of ``component`` into ``dest_dir``.

    Raw files land at ``dest_dir/<descriptor path>`` (the manifest ``model/``
    prefix is applied later by :func:`models_install.synthesize_component`).
    Verifies each file against the vendored descriptor.
    """
    if not download_lock.acquire(blocking=False):
        raise DownloadInProgress("a model download is already in progress")
    try:
        if not component.files:
            raise DownloadError(
                f"no per-file trust data for component {component.component_id!r}"
            )
        if not component.repository or not component.revision:
            raise DownloadError(
                f"component {component.component_id!r} has no repository/revision"
            )

        sources = sources if sources is not None else download_metadata()
        override = _component_sources(sources, component.component_id)
        allow_http = bool(override.get("allow_http", sources.get("allow_http", False)))
        use_system_proxy = bool(
            override.get("use_system_proxy", sources.get("use_system_proxy", False))
        )

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        total_bytes = sum(item.size for item in component.files)
        already = 0
        for item in component.files:
            candidate = dest_dir / item.path
            if candidate.is_file():
                already += candidate.stat().st_size
        needed = max(total_bytes - already, 0)
        free = shutil.disk_usage(dest_dir).free
        if free < needed + _DISK_SLACK:
            raise DownloadError(
                f"insufficient free disk space in {dest_dir}: need ~{needed + _DISK_SLACK} "
                f"bytes, have {free}"
            )

        overall_base = 0
        results: list[dict] = []
        started = time.monotonic()
        for item in component.files:
            url = _url_for(sources, override, component, item.path)

            def file_progress(written, _total, _path, _base=overall_base):
                if progress is not None:
                    progress(_base + written, total_bytes, _path)

            destination = dest_dir / item.path
            try:
                info = download_file(
                    url,
                    destination,
                    expected_size=item.size,
                    expected_sha256=item.sha256,
                    progress=file_progress,
                    timeout=timeout,
                    resume=resume,
                    allow_http=allow_http,
                    use_system_proxy=use_system_proxy,
                )
            except DownloadError:
                info = _try_mirrors(
                    sources,
                    component,
                    item,
                    destination,
                    progress,
                    overall_base,
                    total_bytes,
                    timeout,
                    resume,
                    allow_http,
                    use_system_proxy,
                )
            results.append({"url": url, **info})
            overall_base += item.size

        elapsed = max(time.monotonic() - started, 1e-9)
        return {
            "component_id": component.component_id,
            "dest_dir": str(dest_dir),
            "total_bytes": total_bytes,
            "elapsed_seconds": round(elapsed, 3),
            "bytes_per_second": round(total_bytes / elapsed, 1),
            "files": results,
        }
    finally:
        download_lock.release()


def _try_mirrors(
    sources: dict,
    component: ModelComponent,
    item,
    destination: Path,
    progress: ProgressCallback | None,
    overall_base: int,
    total_bytes: int,
    timeout: float,
    resume: bool,
    allow_http: bool,
    use_system_proxy: bool = False,
) -> dict:
    mirrors = sources.get("mirrors")
    if not isinstance(mirrors, list):
        mirrors = []
    last_error: Exception | None = None
    for mirror in mirrors:
        if not isinstance(mirror, str):
            continue
        # A mirror replaces the base URL (it may itself be a {}-template).
        url = resolve_url(
            mirror,
            component.repository or "",
            component.revision or "",
            item.path,
            base_url=mirror,
        )

        def file_progress(written, _total, _path, _base=overall_base):
            if progress is not None:
                progress(_base + written, total_bytes, _path)

        try:
            return {"url": url, **download_file(
                url,
                destination,
                expected_size=item.size,
                expected_sha256=item.sha256,
                progress=file_progress,
                timeout=timeout,
                resume=resume,
                allow_http=allow_http,
                use_system_proxy=use_system_proxy,
            )}
        except DownloadError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise DownloadError(f"failed to download {item.path} from {component.repository}")
