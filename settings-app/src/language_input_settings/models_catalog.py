"""Model catalog / trust-root for the Language Input settings app (M3).

Two read-only sources are merged:

1. The catalogs **shipped with the installed Weasel** (read-only):
   ``<weasel_root>\\data\\language_input\\models\\packs-v2.json`` and
   ``m2m100-packs-v1.json``.  These carry whole-``.limodel`` size/hash and the
   route tables.
2. The **vendored per-file descriptors** in
   ``language_input_settings/data/components/quickmt-*.json``.  ``packs-v2.json``
   has no ``files[]`` array, so these descriptors are this app's per-file trust
   root (design doc §7.3 / R10).

Nothing here writes to the shipped catalogs: the host's ``load_pack_catalog``
requires an exact key set and would raise on any extra key.  Download metadata
lives in a separate app-owned file (:func:`download_metadata`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

__all__ = [
    "INSTALL_FORMAT",
    "M2M100_INSTALL_FORMAT",
    "PACK_FORMAT",
    "M2M100_PACK_FORMAT",
    "ComponentFile",
    "ModelComponent",
    "app_root",
    "config_dir",
    "download_sources_path",
    "vendored_components_dir",
    "vendored_descriptors",
    "load_components",
    "get_component",
    "load_routes",
    "required_components",
    "missing_requirements",
    "route_coverage",
    "installed_components",
    "installed_ids",
    "default_download_metadata",
    "download_metadata",
]

# Mirrors the frozen host's constants (scripts/language_input_model_host.py).
INSTALL_FORMAT = "language-input-installed-component-v1"
M2M100_INSTALL_FORMAT = "language-input-installed-m2m100-component-v1"
PACK_FORMAT = "language-input-model-pack-v1"
M2M100_PACK_FORMAT = "language-input-m2m100-model-pack-v1"
QUICKMT_MODEL_ID = "quickmt-gloss-route-v2"
M2M100_MODEL_ID = "m2m100-418m-int8"
MAX_COMPONENT_BYTES = 2 * 1024 * 1024 * 1024

DEFAULT_QUICKMT_ROUTES: dict[str, list[str]] = {
    "en": ["quickmt-zh-en"],
    "ja": ["quickmt-zh-en", "quickmt-en-ja"],
    "es": ["quickmt-zh-en", "quickmt-en-es"],
}
DEFAULT_M2M100_ROUTES: dict[str, list[str]] = {
    "en": ["m2m100-418m-int8"],
    "ja": ["m2m100-418m-int8"],
    "es": ["m2m100-418m-int8"],
}
SUPPORTED_LANGUAGES = ("en", "ja", "es")


# --- app-owned locations ----------------------------------------------------

def app_root() -> Path:
    """Repository root of the settings app (``settings-app``)."""
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    """App-owned config directory (``settings-app/config``)."""
    return app_root() / "config"


def download_sources_path() -> Path:
    """App-owned download-source metadata path."""
    return config_dir() / "download-sources.json"


def vendored_components_dir() -> Path:
    """Directory holding the vendored per-file trust descriptors."""
    return Path(__file__).resolve().parent / "data" / "components"


# --- descriptor / model types ----------------------------------------------

@dataclass(frozen=True)
class ComponentFile:
    """One payload file of a component (path relative to the HF repo root)."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelComponent:
    """A downloadable/installable model component.

    ``files`` is empty for components whose per-file trust data is unavailable
    (e.g. a catalog entry with no vendored descriptor).  ``backend`` is
    ``"quickmt"`` or ``"m2m100"``.
    """

    component_id: str
    display_name: str
    backend: str
    revision: str
    license: str
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    repository: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    file_sha256: str | None = None
    runtime_bytes: int | None = None
    files: tuple[ComponentFile, ...] = ()
    license_sha256: str | None = None
    notice_sha256: str | None = None

    @property
    def hf_repo(self) -> str | None:
        """Alias used by the downloader."""
        return self.repository

    @property
    def has_file_manifest(self) -> bool:
        return bool(self.files)

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "display_name": self.display_name,
            "backend": self.backend,
            "revision": self.revision,
            "license": self.license,
            "provides": list(self.provides),
            "requires": list(self.requires),
            "repository": self.repository,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "file_sha256": self.file_sha256,
            "runtime_bytes": self.runtime_bytes,
            "license_sha256": self.license_sha256,
            "notice_sha256": self.notice_sha256,
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in self.files
            ],
        }


# --- loading ----------------------------------------------------------------

def _read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def vendored_descriptors() -> dict[str, dict]:
    """Read the vendored ``quickmt-*.json`` descriptors keyed by component id."""
    directory = vendored_components_dir()
    result: dict[str, dict] = {}
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return result
    for entry in entries:
        document = _read_json(entry)
        if not document:
            continue
        component_id = document.get("component_id")
        if isinstance(component_id, str) and component_id:
            result[component_id] = document
    return result


def _files_from_rows(rows: object) -> tuple[ComponentFile, ...]:
    if not isinstance(rows, list):
        return ()
    files: list[ComponentFile] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        size = row.get("size")
        sha = row.get("sha256")
        if isinstance(path, str) and isinstance(size, int) and isinstance(sha, str):
            files.append(ComponentFile(path=path, size=size, sha256=sha))
    return tuple(files)


def _quickmt_component(catalog: dict | None, descriptor: dict | None) -> ModelComponent:
    descriptor = descriptor or {}
    catalog = catalog or {}
    component_id = str(catalog.get("id") or descriptor.get("component_id") or "")
    return ModelComponent(
        component_id=component_id,
        display_name=str(
            catalog.get("display_name") or descriptor.get("display_name") or component_id
        ),
        backend="quickmt",
        revision=str(catalog.get("revision") or descriptor.get("revision") or ""),
        license=str(catalog.get("license") or descriptor.get("license") or ""),
        provides=tuple(catalog.get("provides") or descriptor.get("provides") or ()),
        requires=tuple(catalog.get("requires") or descriptor.get("requires") or ()),
        repository=descriptor.get("repository"),
        file_name=catalog.get("file_name"),
        file_size=catalog.get("file_size"),
        file_sha256=catalog.get("file_sha256"),
        runtime_bytes=descriptor.get("runtime_bytes"),
        files=_files_from_rows(descriptor.get("files")),
    )


def _m2m100_component(catalog: dict) -> ModelComponent:
    return ModelComponent(
        component_id=str(catalog.get("id") or ""),
        display_name=str(catalog.get("display_name") or catalog.get("id") or ""),
        backend="m2m100",
        revision=str(catalog.get("revision") or ""),
        license=str(catalog.get("license") or ""),
        provides=tuple(catalog.get("provides") or ()),
        requires=tuple(catalog.get("requires") or ()),
        repository=catalog.get("repository"),
        file_name=catalog.get("file_name"),
        file_size=catalog.get("file_size"),
        file_sha256=catalog.get("file_sha256"),
        runtime_bytes=catalog.get("runtime_bytes"),
        files=_files_from_rows(catalog.get("files")),
        license_sha256=catalog.get("license_sha256"),
        notice_sha256=catalog.get("notice_sha256"),
    )


def load_components(root: Path | None = None) -> dict[str, ModelComponent]:
    """Merge the shipped catalogs with the vendored descriptors.

    ``root`` defaults to the resolved Weasel install root.  This function is
    read-only and never raises on missing/invalid catalogs; it degrades to the
    vendored descriptors.
    """
    if root is None:
        try:
            root = paths.weasel_root()
        except Exception:
            root = None

    components: dict[str, ModelComponent] = {}

    quickmt_catalog = _read_json(paths.packs_catalog(root) if root else None)
    descriptors = vendored_descriptors()
    catalog_components: dict[str, dict] = {}
    if quickmt_catalog and isinstance(quickmt_catalog.get("components"), list):
        for row in quickmt_catalog["components"]:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                catalog_components[row["id"]] = row

    for component_id, descriptor in descriptors.items():
        components[component_id] = _quickmt_component(
            catalog_components.get(component_id), descriptor
        )
    # Catalog entries without a vendored descriptor still get a display record.
    for component_id, row in catalog_components.items():
        if component_id not in components:
            components[component_id] = _quickmt_component(row, None)

    m2m100_catalog = _read_json(paths.m2m100_catalog(root) if root else None)
    if m2m100_catalog and isinstance(m2m100_catalog.get("components"), list):
        for row in m2m100_catalog["components"]:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                components[row["id"]] = _m2m100_component(row)

    return components


def get_component(component_id: str, root: Path | None = None) -> ModelComponent | None:
    """Return one component or ``None``."""
    return load_components(root).get(component_id)


def load_routes(root: Path | None = None, backend: str = "quickmt") -> dict[str, list[str]]:
    """Return the route table for ``backend`` (catalog first, defaults after)."""
    if root is None:
        try:
            root = paths.weasel_root()
        except Exception:
            root = None
    if backend == "m2m100":
        catalog = _read_json(paths.m2m100_catalog(root) if root else None)
        default = DEFAULT_M2M100_ROUTES
    else:
        catalog = _read_json(paths.packs_catalog(root) if root else None)
        default = DEFAULT_QUICKMT_ROUTES
    routes = catalog.get("routes") if catalog else None
    if isinstance(routes, dict) and routes:
        return {str(k): list(v) for k, v in routes.items() if isinstance(v, list)}
    return {key: list(value) for key, value in default.items()}


def required_components(
    language: str, *, backend: str = "quickmt", root: Path | None = None
) -> list[str]:
    """Component ids needed to translate ``language`` with ``backend``."""
    return list(load_routes(root, backend=backend).get(language, []))


def missing_requirements(component_id: str, installed: dict) -> list[str]:
    """Direct requires of ``component_id`` that are not present in ``installed``."""
    component = get_component(component_id)
    if component is None:
        return []
    return [dep for dep in component.requires if dep not in installed]


def route_coverage(
    installed: dict | list | set,
    *, root: Path | None = None, backend: str = "quickmt",
) -> dict[str, dict]:
    """Per-language availability given the installed component ids."""
    installed_ids = set(installed)
    routes = load_routes(root, backend=backend)
    coverage: dict[str, dict] = {}
    for language in SUPPORTED_LANGUAGES:
        required = list(routes.get(language, []))
        present = [cid for cid in required if cid in installed_ids]
        missing = [cid for cid in required if cid not in installed_ids]
        coverage[language] = {
            "available": bool(required) and not missing,
            "required": required,
            "installed": present,
            "missing": missing,
        }
    return coverage


# --- installed components (read-only) ---------------------------------------

def _valid_installed_record(component_root: Path) -> dict | None:
    """Structural validation mirroring the host's ``load_installed_component``.

    This does **not** re-hash files (see ``models_install.verify_installed_component``)
    — it is a fast, read-only listing check.
    """
    record_path = component_root / "installed.json"
    try:
        if component_root.is_symlink() or not component_root.is_dir():
            return None
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    required = {"format", "component_id", "pack_size", "pack_sha256", "manifest"}
    if not isinstance(record, dict) or set(record) != required:
        return None
    if record.get("format") not in (INSTALL_FORMAT, M2M100_INSTALL_FORMAT):
        return None
    manifest = record.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("component_id") != record.get(
        "component_id"
    ):
        return None
    if record["format"] == M2M100_INSTALL_FORMAT and manifest.get("format") != M2M100_PACK_FORMAT:
        return None
    if record.get("component_id") != component_root.name:
        return None
    return record


def installed_components(model_root: Path | None) -> dict[str, dict]:
    """Return ``{component_id: installed.json}`` for structurally valid components."""
    if model_root is None:
        return {}
    root = Path(model_root)
    try:
        if not root.is_dir():
            return {}
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return {}
    result: dict[str, dict] = {}
    for entry in entries:
        if entry.name.startswith("."):
            continue
        record = _valid_installed_record(entry)
        if record is not None:
            result[entry.name] = record
    return result


def installed_ids(model_root: Path | None) -> list[str]:
    """Sorted ids of structurally valid installed components."""
    return sorted(installed_components(model_root).keys())


# --- app-owned download metadata --------------------------------------------

def default_download_metadata() -> dict:
    """Built-in download metadata used when the app-owned file is absent.

    ``use_system_proxy`` defaults to **False**: this machine sets
    ``HTTP(S)_PROXY = ALL_PROXY = http://127.0.0.1:7898`` and that proxy
    throttles Hugging Face traffic to ~43 KB/s, while a direct connection
    measured ~8.8 MB/s (design doc §7.3 / decision #17).  ``urllib`` honours
    those env proxies by default, so the default is to bypass them.
    """
    return {
        "schema_version": 1,
        "base_url": "https://huggingface.co",
        "url_template": "{base_url}/{repository}/resolve/{revision}/{path}?download=true",
        "allow_http": False,
        "use_system_proxy": False,
        "mirrors": [],
        "components": {},
    }


def download_metadata(path: Path | None = None) -> dict:
    """Read/merge the app-owned ``config/download-sources.json``.

    Shape::

        {
          "schema_version": 1,
          "base_url": "https://huggingface.co",
          "url_template": "{base_url}/{repository}/resolve/{revision}/{path}?download=true",
          "allow_http": false,
          "use_system_proxy": false,
          "mirrors": ["https://mirror.example/hf"],
          "components": {
            "quickmt-zh-en": {
              "base_url": "...",            # optional override
              "url_template": "...",        # optional override
              "files": {"model.bin": "https://mirror/.../model.bin"}
            }
          }
        }

    Never writes; missing/invalid files fall back to defaults.
    """
    metadata = default_download_metadata()
    document = _read_json(path or download_sources_path())
    if not document:
        return metadata
    for key in ("base_url", "url_template", "allow_http", "use_system_proxy"):
        if key in document:
            metadata[key] = document[key]
    for key in ("mirrors", "components"):
        value = document.get(key)
        if isinstance(value, (list, dict)):
            metadata[key] = value
    return metadata
