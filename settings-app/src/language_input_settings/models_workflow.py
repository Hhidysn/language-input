"""One-step verified model installation, including required components."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import models_catalog, models_download, models_install


def install_plan(
    component_id: str,
    components: dict,
    installed: set[str],
    *,
    replace: bool = False,
) -> list[str]:
    """Return missing dependencies first and the requested component last."""
    if component_id not in components:
        raise ValueError(f"unknown model component: {component_id}")
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(current: str) -> None:
        if current in visiting:
            raise ValueError(f"cyclic model dependency: {current}")
        if current in visited:
            return
        component = components.get(current)
        if component is None:
            raise ValueError(f"missing model dependency: {current}")
        visiting.add(current)
        for dependency in component.requires:
            visit(dependency)
        visiting.remove(current)
        visited.add(current)
        if current not in installed or (current == component_id and replace):
            order.append(current)

    visit(component_id)
    return order


def download_and_install_component(
    component_id: str,
    model_root: Path | str,
    *,
    sources: dict | None = None,
    replace: bool = False,
    progress: Callable | None = None,
) -> dict:
    """Download, verify and install an entire dependency plan in one UI job."""
    root = Path(model_root)
    components = models_catalog.load_components()
    installed = set(models_catalog.installed_ids(root))
    plan = install_plan(component_id, components, installed, replace=replace)
    total = sum(sum(item.size for item in components[cid].files) for cid in plan)
    completed = 0
    results: list[dict] = []
    downloaded_components: list[tuple[str, Path, dict]] = []
    for cid in plan:
        component = components[cid]
        destination = root / f".sources-{cid}"

        def report(written, _total, path, *, base=completed):
            if progress is not None:
                progress(base + written, total, path)

        downloaded = models_download.download_component(
            component, destination, sources=sources, progress=report
        )
        downloaded_components.append((cid, destination, downloaded))
        completed += downloaded["total_bytes"]
        if progress is not None:
            progress(completed, total, None)

    # Finish every network transfer before stopping and restarting the input
    # service for installation. A later download failure leaves it untouched.
    for cid, destination, downloaded in downloaded_components:
        component = components[cid]
        installed_result = models_install.install_from_dir(
            component,
            destination,
            root,
            replace=(cid in installed),
        )
        restart_error = (installed_result.get("server") or {}).get("restart_error")
        if restart_error:
            raise RuntimeError(
                f"{cid} installed but the input service did not restart: "
                f"{restart_error}"
            )
        results.append(
            {"component_id": cid, "download": downloaded, "install": installed_result}
        )
    return {"component_id": component_id, "plan": plan, "total_bytes": total, "results": results}
