"""恢复默认：revert every change this application wrote (Part 2 item 9).

One confirmed maintenance action reverts, per item:

* ``<schema>.custom.yaml`` reset patches (:func:`schema_patch.revert_resets`),
  for every Language Input schema;
* the ``translator/enable_user_dict`` override the app wrote
  (:func:`rime_settings.set_learning` with ``on=True``);
* the ``weasel.custom.yaml`` style keys the app manages
  (:data:`rime_settings.MANAGED_STYLE_KEYS`, removed via
  :func:`rime_settings.apply_style` with ``None`` values);
* the plain-gloss shadow copy (:func:`gloss_badge.revert_plain_gloss`);
* the ``var/option/*`` switches the app wrote in ``user.yaml``
  (:func:`yaml_io.remove_user_yaml_option`, with the server stopped).

Rules:

* **Installed models are never touched** -- nothing here references the model
  root.
* A piece that cannot be reverted safely (e.g. a flow-style ``option:``
  mapping) is reported as such (``unsafe``/``ok=False``) rather than silently
  skipped.
* One ``/deploy`` runs at the end; success requires exit code ``0`` **and** an
  empty stderr (:func:`rime_settings.run_deploy`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import gloss_badge, paths, rime_settings, schema_patch, server, yaml_io

__all__ = ["restore_defaults"]


def _schema_custom_snapshot(schema_id: str, user_dir: Path) -> "bytes | None":
    path = schema_patch.custom_schema_path(schema_id, user_dir)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def restore_defaults(
    *,
    user_dir: Path | str | None = None,
    weasel_root: Path | str | None = None,
) -> dict[str, Any]:
    """Revert every change this app wrote; return a per-item report."""
    resolved_user = Path(user_dir) if user_dir is not None else paths.rime_user_dir()
    items: list[dict[str, Any]] = []

    # --- 1. schema reset patches -------------------------------------------
    for schema_id in rime_settings.LANGUAGE_SCHEMAS:
        before = _schema_custom_snapshot(schema_id, resolved_user)
        try:
            path = schema_patch.revert_resets(schema_id, resolved_user)
            after = _schema_custom_snapshot(schema_id, resolved_user)
            changed = before != after
            items.append(
                {
                    "key": "schema_resets",
                    "schema_id": schema_id,
                    "ok": True,
                    "changed": changed,
                    "path": str(path) if path else None,
                    "note": None if changed else "无需更改",
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported per item
            items.append(
                {
                    "key": "schema_resets",
                    "schema_id": schema_id,
                    "ok": False,
                    "changed": False,
                    "path": None,
                    "note": f"{type(exc).__name__}: {exc}",
                    "unsafe": True,
                }
            )

    # --- 2. user-dictionary learning override ------------------------------
    try:
        learning = rime_settings.set_learning(
            True, deploy=False, user_dir=resolved_user
        )
        learning_changed = any(
            info.get("removed_override") or info.get("custom_deleted")
            for info in (learning.get("schemas") or {}).values()
        )
        items.append(
            {
                "key": "learning_override",
                "ok": True,
                "changed": learning_changed,
                "note": None if learning_changed else "无需更改",
            }
        )
    except Exception as exc:  # noqa: BLE001 - reported per item
        items.append(
            {
                "key": "learning_override",
                "ok": False,
                "changed": False,
                "note": f"{type(exc).__name__}: {exc}",
                "unsafe": True,
            }
        )

    # --- 3. weasel.custom.yaml style keys ----------------------------------
    try:
        style = rime_settings.apply_style(
            {key: None for key in rime_settings.MANAGED_STYLE_KEYS},
            deploy=False,
            user_dir=resolved_user,
            weasel_root=weasel_root,
        )
        style_changed = bool(style.get("changed"))
        items.append(
            {
                "key": "weasel_style",
                "ok": True,
                "changed": style_changed,
                "note": None if style_changed else "无需更改",
            }
        )
    except Exception as exc:  # noqa: BLE001 - reported per item
        items.append(
            {
                "key": "weasel_style",
                "ok": False,
                "changed": False,
                "note": f"{type(exc).__name__}: {exc}",
                "unsafe": True,
            }
        )

    # --- 4. plain-gloss shadow copy ----------------------------------------
    try:
        gloss = gloss_badge.revert_plain_gloss(deploy=False)
        gloss_changed = bool(gloss.get("removed"))
        items.append(
            {
                "key": "plain_gloss",
                "ok": bool(gloss.get("ok")),
                "changed": gloss_changed,
                "note": gloss.get("error")
                or (None if gloss_changed else "无需更改"),
            }
        )
    except Exception as exc:  # noqa: BLE001 - reported per item
        items.append(
            {
                "key": "plain_gloss",
                "ok": False,
                "changed": False,
                "note": f"{type(exc).__name__}: {exc}",
                "unsafe": True,
            }
        )

    # --- 5. user.yaml options (must be edited with the server stopped) -----
    user_yaml = resolved_user / "user.yaml"
    removed: list[str] = []
    unsafe: list[str] = []
    options_error: str | None = None
    try:
        with server.server_stopped() as _tx:
            for name in rime_settings.SWITCH_ORDER:
                outcome = yaml_io.remove_user_yaml_option(user_yaml, name)
                if outcome.get("unsafe"):
                    unsafe.append(name)
                elif outcome.get("removed"):
                    removed.append(name)
    except Exception as exc:  # noqa: BLE001 - reported per item
        options_error = f"{type(exc).__name__}: {exc}"

    options_ok = options_error is None and not unsafe
    options_note: str | None
    if options_error is not None:
        options_note = options_error
    elif unsafe:
        options_note = "部分选项为流式映射，无法安全还原：" + "、".join(unsafe)
    elif not removed:
        options_note = "无需更改"
    else:
        options_note = None
    items.append(
        {
            "key": "user_yaml_options",
            "ok": options_ok,
            "changed": bool(removed),
            "removed": removed,
            "unsafe": unsafe,
            "note": options_note,
        }
    )

    changed_any = any(item.get("changed") for item in items)

    # --- one deploy at the end ---------------------------------------------
    deploy: dict[str, Any] | None = None
    if changed_any:
        deploy = rime_settings.run_deploy()

    all_items_ok = all(item.get("ok") for item in items)
    if changed_any:
        deploy_clean = bool(deploy and deploy.get("clean"))
        ok = all_items_ok and deploy_clean
    else:
        deploy_clean = None
        ok = all_items_ok

    return {
        "action": "restore_defaults",
        "items": items,
        "changed_any": changed_any,
        "deploy": deploy,
        "deploy_clean": deploy_clean,
        "ok": bool(ok),
    }
