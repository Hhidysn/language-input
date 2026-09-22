"""Command-line model management for the Language Input settings app (M3).

Dispatched from :func:`language_input_settings.app.main` when one of the
``--models-*`` flags is present.  Results are JSON on stdout; download progress
goes to stderr.

All commands accept ``--model-root DIR`` to override the resolved model root;
this is required for safe, side-effect-free verification because the real
model root lives under the user's live ``%APPDATA%\\Rime``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import __version__, paths
from .models_catalog import (
    SUPPORTED_LANGUAGES,
    download_metadata,
    installed_components,
    load_components,
    route_coverage,
)

__all__ = ["add_arguments", "dispatch"]


def add_arguments(parser) -> None:
    """Register the ``--models-*`` flags on ``parser``."""
    parser.add_argument(
        "--models-list",
        action="store_true",
        help="list known model components, installed state and route coverage (JSON)",
    )
    parser.add_argument(
        "--models-verify",
        metavar="COMPONENT_ID",
        help="re-hash an installed component against its manifest (JSON)",
    )
    parser.add_argument(
        "--models-download",
        metavar="COMPONENT_ID",
        help="download a component's files into a staging directory (network)",
    )
    parser.add_argument(
        "--models-install-from-dir",
        metavar="COMPONENT_ID",
        help="synthesize + install a component from verified source files",
    )
    parser.add_argument(
        "--models-install-limodel",
        metavar="PACK_PATH",
        type=Path,
        help="import an exact catalog .limodel via LanguageInputModelHost.exe",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        help="override the model root (default: resolved Rime user model root)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        help="staging directory for --models-download",
    )
    parser.add_argument(
        "--src",
        type=Path,
        help="source directory for --models-install-from-dir",
    )
    parser.add_argument(
        "--m2m100",
        action="store_true",
        help="use the M2M100 catalog (for --models-install-limodel)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="replace an already-installed component",
    )

    # Translation backend (M2).  These do not touch the model files.
    parser.add_argument(
        "--backend-status",
        action="store_true",
        help="report the live WeaselServer backend/env as JSON",
    )
    parser.add_argument(
        "--set-backend",
        choices=["local", "remote", "off"],
        help="apply a translation backend (restarts WeaselServer)",
    )
    parser.add_argument("--url", help="remote API endpoint (for --set-backend remote)")
    parser.add_argument("--api-key", help="remote API key (for --set-backend remote)")
    parser.add_argument("--model", help="remote model name (for --set-backend remote)")
    parser.add_argument(
        "--language",
        choices=["en", "ja", "es"],
        help="remote initial language code (for --set-backend remote)",
    )
    parser.add_argument(
        "--neutralize-resets",
        metavar="SCHEMA_ID",
        help="write+deploy a custom patch that neutralizes schema resets",
    )
    parser.add_argument(
        "--revert-resets",
        metavar="SCHEMA_ID",
        help="remove our reset patch from <schema>.custom.yaml and redeploy",
    )
    parser.add_argument(
        "--reset-state",
        metavar="SCHEMA_ID",
        help="report which Language Input switches still carry reset: (JSON)",
    )
    parser.add_argument(
        "--gloss-badge",
        choices=["status", "plain", "fancy"],
        help=(
            "gloss badge: 'status' reports the plain-gloss state (JSON); "
            "'plain' hides the 〔lang·词〕 badge via a user-dir shadow copy + "
            "redeploy; 'fancy' reverts to the shipped badge"
        ),
    )

    # Appearance / switches / memory (M4/M5).  These write real Rime config.
    parser.add_argument(
        "--appearance-status",
        action="store_true",
        help="report the installed style keys, colour schemes and effective style (JSON)",
    )
    parser.add_argument(
        "--set-style",
        action="append",
        metavar="KEY=VALUE",
        help=(
            "merge one style key into weasel.custom.yaml then redeploy "
            "(repeatable); KEY may be omitted prefix, e.g. style/font_point=15; "
            "an empty VALUE removes the key"
        ),
    )
    parser.add_argument(
        "--switches-status",
        action="store_true",
        help="report the Language Input switches, saved options and hotkeys (JSON)",
    )
    parser.add_argument(
        "--set-switch",
        metavar="NAME=on|off",
        help="set a Language Input var/option switch, then redeploy",
    )
    parser.add_argument(
        "--learning",
        choices=["status", "on", "off"],
        help="user-dictionary learning: status, enable or fully disable",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="run WeaselDeployer.exe /sync and report the result",
    )


def _model_root(args) -> Path:
    override = getattr(args, "model_root", None)
    if override:
        return Path(override)
    resolved = paths.model_root(paths.rime_user_dir())
    if resolved is None:  # pragma: no cover - rime_user_dir never returns None
        raise RuntimeError("could not resolve the model root")
    return Path(resolved)


def _print(document: dict) -> None:
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=False))


def _progress_printer(component_id: str):
    state = {"last": 0.0, "previous": -1}

    def callback(downloaded: int, total: int | None, path: str) -> None:
        now = time.monotonic()
        finished = total is not None and downloaded >= total
        if not finished and now - state["last"] < 0.5:
            return
        state["last"] = now
        if finished and state["previous"] == total:
            return
        state["previous"] = total if finished else -1
        percent = f"{downloaded * 100 // total}%" if total else "?"
        sys.stderr.write(
            f"[download] {component_id} {path}: {downloaded}/{total or '?'} ({percent})\n"
        )
        sys.stderr.flush()

    return callback


# --- commands ---------------------------------------------------------------

def cmd_models_list(args) -> int:
    model_root = _model_root(args)
    components = load_components()
    installed = installed_components(model_root)
    installed_ids = set(installed)

    rows = []
    for component_id in sorted(components):
        component = components[component_id]
        record = component.to_dict()
        record["installed"] = component_id in installed_ids
        record["missing_requirements"] = [
            dep for dep in component.requires if dep not in installed_ids
        ]
        rows.append(record)

    coverage: dict[str, dict] = {}
    for language in SUPPORTED_LANGUAGES:
        quickmt = route_coverage(installed_ids, backend="quickmt")[language]
        m2m100 = route_coverage(installed_ids, backend="m2m100")[language]
        coverage[language] = {
            "available": bool(quickmt["available"] or m2m100["available"]),
            "quickmt": quickmt,
            "m2m100": m2m100,
        }

    _print(
        {
            "app": "language-input-settings",
            "version": __version__,
            "model_root": str(model_root),
            "model_root_exists": model_root.is_dir(),
            "download_sources": download_metadata(),
            "installed_ids": sorted(installed_ids),
            "components": rows,
            "languages": coverage,
        }
    )
    return 0


def cmd_models_verify(args) -> int:
    from .models_install import verify_installed_component

    model_root = _model_root(args)
    component_root = model_root / args.models_verify
    if not component_root.is_dir():
        _print(
            {
                "component_id": args.models_verify,
                "model_root": str(model_root),
                "ok": False,
                "error": "component is not installed",
            }
        )
        return 1
    report = verify_installed_component(component_root)
    _print(report)
    return 0 if report["ok"] else 1


def cmd_models_download(args) -> int:
    from .models_download import download_component

    components = load_components()
    component = components.get(args.models_download)
    if component is None:
        _print({"error": f"unknown component: {args.models_download}"})
        return 2
    model_root = _model_root(args)
    dest = Path(args.dest) if args.dest else model_root / f".sources-{component.component_id}"
    try:
        result = download_component(
            component,
            dest,
            sources=download_metadata(),
            progress=_progress_printer(component.component_id),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _print({"component_id": component.component_id, "dest_dir": str(dest), "error": str(exc)})
        return 1
    _print(result)
    return 0


def cmd_models_install_from_dir(args) -> int:
    from .models_install import install_from_dir

    if not args.src:
        _print({"error": "--src DIR is required for --models-install-from-dir"})
        return 2
    components = load_components()
    component = components.get(args.models_install_from_dir)
    if component is None:
        _print({"error": f"unknown component: {args.models_install_from_dir}"})
        return 2
    source = Path(args.src)
    if not source.is_dir():
        _print({"error": f"source directory not found: {source}"})
        return 2
    model_root = _model_root(args)
    try:
        result = install_from_dir(
            component, source, model_root, replace=bool(args.replace)
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _print({"component_id": component.component_id, "error": str(exc)})
        return 1
    _print(
        {
            "component_id": component.component_id,
            "target": result["target"],
            "replaced": bool(args.replace),
            "verification_ok": result["verification"]["ok"],
            "recovered": result["recovered"],
        }
    )
    return 0


def cmd_models_install_limodel(args) -> int:
    from .models_install import install_from_limodel

    pack = Path(args.models_install_limodel)
    model_root = _model_root(args)
    try:
        result = install_from_limodel(
            pack, model_root, replace=bool(args.replace), m2m100=bool(args.m2m100)
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _print({"pack": str(pack), "error": str(exc)})
        return 1
    _print(result)
    return 0


# --- translation backend / schema reset (M2) --------------------------------

def cmd_backend_status(args) -> int:
    from . import env_config, server
    from .models_catalog import installed_ids

    models_root = _model_root(args)
    running = server.running_pids()
    server_env = server.read_server_env() if running else None
    cache = paths.ai_cache_path(paths.rime_user_dir())

    _print(
        {
            "app": "language-input-settings",
            "version": __version__,
            "server_exe": str(paths.weasel_server_exe(paths.weasel_root()) or ""),
            "server_running": bool(running),
            "server_pids": running,
            "server_backend": (
                env_config.describe_backend(server_env).value
                if server_env is not None
                else None
            ),
            "server_remote": (
                env_config.effective_remote_config(server_env)
                if server_env is not None
                else None
            ),
            "current_process_backend": env_config.current_process_backend().value,
            "model_root": str(models_root),
            "models_installed": installed_ids(models_root),
            "ai_cache": str(cache) if cache else None,
            "ai_cache_exists": bool(cache and cache.is_file()),
        }
    )
    return 0


def cmd_set_backend(args) -> int:
    from . import appconfig, server

    config = appconfig.load_config()
    backend = args.set_backend

    if args.url is not None:
        config.remote_url = args.url
    if args.model is not None:
        config.remote_model = args.model
    if args.language is not None:
        config.language = args.language
        config.remote_language = args.language
    config.backend = backend

    if args.api_key is not None:
        config.api_key_dpapi = (
            appconfig.encrypt_secret(args.api_key) if args.api_key else None
        )
        api_key = args.api_key or None
    else:
        api_key = appconfig.decrypt_secret(config.api_key_dpapi)

    appconfig.save_config(config)

    result = server.apply_backend(
        backend,
        url=config.remote_url or None,
        api_key=api_key,
        model=config.remote_model or None,
        language=config.language or None,
    )
    _print(result)
    return 0 if result.get("ok") else 1


def _cmd_schema_sync(args, *, neutralize: bool) -> int:
    from . import deploy, schema_patch

    schema_id = args.neutralize_resets if neutralize else args.revert_resets
    deployer = paths.weasel_deployer_exe(paths.weasel_root())

    document: dict = {"schema_id": schema_id, "action": "neutralize" if neutralize else "revert"}
    try:
        if neutralize:
            path = schema_patch.neutralize_resets(schema_id)
            document["custom_path"] = str(path)
            document["custom_exists"] = Path(path).is_file()
        else:
            path = schema_patch.revert_resets(schema_id)
            document["custom_path"] = str(path) if path else None
            document["custom_exists"] = bool(path and Path(path).is_file())
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        document["error"] = str(exc)
        _print(document)
        return 1

    result = deploy.run_deploy(deployer, timeout=120)
    document["deploy"] = {
        "ran": result.ran,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "busy": result.busy,
        "stderr": result.stderr,
        "note": result.note,
    }
    document["reset_state"] = schema_patch.compiled_reset_state(schema_id)
    _print(document)
    return 0


def cmd_neutralize_resets(args) -> int:
    return _cmd_schema_sync(args, neutralize=True)


def cmd_revert_resets(args) -> int:
    return _cmd_schema_sync(args, neutralize=False)


def cmd_reset_state(args) -> int:
    from . import schema_patch

    schema_id = args.reset_state
    try:
        state = schema_patch.compiled_reset_state(schema_id)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _print({"schema_id": schema_id, "error": str(exc)})
        return 1
    _print(
        {
            "schema_id": schema_id,
            "language_input_resets": state,
            "any_reset": any(state.values()),
        }
    )
    return 0


def cmd_gloss_badge(args) -> int:
    from . import gloss_badge

    mode = args.gloss_badge
    if mode == "status":
        _print(gloss_badge.plain_gloss_state(paths.rime_user_dir()))
        return 0

    try:
        if mode == "plain":
            result = gloss_badge.apply_plain_gloss(deploy=True)
        else:  # fancy
            result = gloss_badge.revert_plain_gloss(deploy=True)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _print({"action": mode, "error": str(exc)})
        return 1

    _print(result)
    return 0 if result.get("ok") else 1


# --- appearance / switches / memory (M4/M5) ---------------------------------

_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}


def cmd_appearance_status(args) -> int:
    from . import rime_settings

    style = rime_settings.read_weasel_style()
    _print(
        {
            "app": "language-input-settings",
            "version": __version__,
            "weasel_yaml": style["weasel_yaml"],
            "style_keys_found": style["available_keys"],
            "requested_keys": style["requested"],
            "available_color_schemes": rime_settings.available_color_schemes(),
            "current_style": rime_settings.current_style(),
        }
    )
    return 0


def cmd_set_style(args) -> int:
    from . import rime_settings

    patch: dict[str, object] = {}
    for item in args.set_style or []:
        if "=" not in item:
            _print({"error": f"--set-style expects KEY=VALUE, got {item!r}"})
            return 2
        key, _, raw = item.partition("=")
        patch[key.strip()] = None if raw == "" else raw
    result = rime_settings.apply_style(patch)
    _print(result)
    if not result.get("changed"):
        return 0
    # A change that could not be deployed cleanly is a failure (N1).
    return 0 if result.get("clean") else 1


def cmd_switches_status(args) -> int:
    from . import rime_settings, schema_patch

    resets = {}
    for schema_id in rime_settings.LANGUAGE_SCHEMAS:
        try:
            resets[schema_id] = {
                "custom_path": str(schema_patch.custom_schema_path(schema_id)),
                "compiled_reset_state": schema_patch.compiled_reset_state(schema_id),
            }
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            resets[schema_id] = {"error": str(exc)}
    _print(
        {
            "app": "language-input-settings",
            "version": __version__,
            "switches": rime_settings.language_input_switches(),
            "saved_options": rime_settings.read_switches(),
            "hotkeys": rime_settings.hotkeys(),
            "schema_resets": resets,
        }
    )
    return 0


def cmd_set_switch(args) -> int:
    from . import rime_settings

    text = args.set_switch
    if "=" not in text:
        _print({"error": f"--set-switch expects NAME=on|off, got {text!r}"})
        return 2
    name, _, raw = text.partition("=")
    token = raw.strip().lower()
    if token in _TRUE_WORDS:
        on = True
    elif token in _FALSE_WORDS:
        on = False
    else:
        _print({"error": f"invalid switch value {raw!r} (use on/off)"})
        return 2
    try:
        result = rime_settings.set_switch(name.strip(), on)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _print({"error": str(exc), "name": name.strip(), "on": on})
        return 1
    _print(result)
    return 0


def cmd_learning(args) -> int:
    from . import rime_settings

    if args.learning == "status":
        _print(rime_settings.learning_state())
        return 0
    result = rime_settings.set_learning(args.learning == "on")
    _print(result)
    return 0


def cmd_sync(args) -> int:
    from . import rime_settings

    result = rime_settings.sync_user_data()
    _print(result)
    return 0 if result.get("clean") else 1


def dispatch(args) -> int | None:
    """Handle a ``--models-*`` / M2 request; return None if none was given."""
    if getattr(args, "appearance_status", False):
        return cmd_appearance_status(args)
    if getattr(args, "set_style", None):
        return cmd_set_style(args)
    if getattr(args, "switches_status", False):
        return cmd_switches_status(args)
    if getattr(args, "set_switch", None):
        return cmd_set_switch(args)
    if getattr(args, "learning", None):
        return cmd_learning(args)
    if getattr(args, "sync", False):
        return cmd_sync(args)
    if getattr(args, "gloss_badge", None):
        return cmd_gloss_badge(args)
    if getattr(args, "backend_status", False):
        return cmd_backend_status(args)
    if getattr(args, "set_backend", None):
        return cmd_set_backend(args)
    if getattr(args, "neutralize_resets", None):
        return cmd_neutralize_resets(args)
    if getattr(args, "revert_resets", None):
        return cmd_revert_resets(args)
    if getattr(args, "reset_state", None):
        return cmd_reset_state(args)
    if getattr(args, "models_list", False):
        return cmd_models_list(args)
    if getattr(args, "models_verify", None):
        return cmd_models_verify(args)
    if getattr(args, "models_download", None):
        return cmd_models_download(args)
    if getattr(args, "models_install_from_dir", None):
        return cmd_models_install_from_dir(args)
    if getattr(args, "models_install_limodel", None):
        return cmd_models_install_limodel(args)
    return None
