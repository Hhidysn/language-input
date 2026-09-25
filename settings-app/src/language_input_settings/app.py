"""Language Input settings application.

Entry point (:func:`main`) supports:

* ``--selftest``: strictly read-only with respect to the user's Rime data; it
  only creates/reads/deletes files inside a private temp directory.  It never
  creates a ``QApplication``.
* ``--gui-smoke``: create a ``QApplication``, build the main window (all seven
  pages) and the system-tray icon, process pending events, then tear everything
  down without entering the event loop.  A construction check.
* ``--gui-shot <out_dir>``: build the window, show it, and save one PNG per page
  (``01-overview.png`` … ``07-about.png``) with ``QWidget.grab()``; then exit.
* ``--version``: print the app version.
* ``--start-minimized``: start hidden in the system tray (no window).  The
  persisted ``config.start_minimized`` setting is honoured too, so the
  autostart entry can pass the flag.
* no arguments: launch the PySide6 tray application.

GUI stack is **PySide6 (Qt for Python)** (design doc §6 / decision #13): the
earlier non-Qt pivot was caused by an environment / network artifact — a proxy
throttle made PyPI look unreachable and "PySide6 cannot be installed" was a
misdiagnosis.  PySide6 installs fine once the proxy is bypassed, so the GUI
layer is back on Qt and the interim stack is unused.

Threading model: Qt owns the main thread.  The window, the tray icon and every
callback live on the main thread, so no cross-thread marshalling is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import __version__, appconfig, paths, rime_settings, yaml_io

__all__ = ["main", "run_selftest", "run_gui_smoke", "run_gui_shot"]

_SINGLE_INSTANCE_MUTEX = "Global\\LanguageInputSettings"
_ERROR_ALREADY_EXISTS = 183
_TRAY_TITLE = "Language Input 设置"

# Shown by every ``应用`` action that goes through a stop/start transaction.
# The wording is deliberately plain: the whole input method service is restarted
# and typing is unavailable for the duration, so the user knows what to expect.
_RESTART_WARNING = "此操作会重启输入法服务，期间无法打字，是否继续？"

# Kept alive for the process lifetime so the single-instance mutex is held.
_instance_handle = None

# (key, title, english, description shown on the page)
PAGE_SPECS = [
    (
        "overview",
        "概览",
        "Overview",
        "显示当前翻译后端、本地模型、译注、学习库与服务状态（只读）。",
    ),
    (
        "translation",
        "翻译",
        "Translation",
        "选择本地模型或外接 API，配置端点、密钥、外接模型与语言。",
    ),
    (
        "models",
        "模型",
        "Models",
        "查看模型包、大小、安装状态与适用语言；支持下载 / 安装 / 校验所选组件。",
    ),
    (
        "dictionary",
        "词库与记忆",
        "Dictionary & Memory",
        "用户词典学习、其他输入法词库导入与维护。",
    ),
    (
        "appearance",
        "外观",
        "Appearance",
        "候选窗配色与字号（写入 weasel.custom.yaml 并重新部署）。",
    ),
    (
        "keys",
        "按键与开关",
        "Keys & Switches",
        "配置 F4 开关、搜狗模糊音与快捷键。",
    ),
    (
        "about",
        "关于",
        "About",
        "版本信息、设计文档入口与“重新部署”操作。",
    ),
]

# Small, visually simple icons are drawn with QPainter; no external icon files.
_PAGE_ICONS = {
    "overview": "overview",
    "translation": "translation",
    "models": "models",
    "dictionary": "dictionary",
    "appearance": "appearance",
    "keys": "keys",
    "about": "about",
}

# --- user-facing label mapping ----------------------------------------------
# Raw identifiers are never shown as primary text; the raw spelling only ever
# appears in a tooltip or a 详情 section.

LANGUAGE_LABELS = {"en": "英语", "ja": "日语", "es": "西班牙语"}
BACKEND_LABELS = {"local": "本地模型", "remote": "外接 API", "off": "关闭 AI 传输"}
COMPONENT_LABELS = {
    "quickmt-zh-en": "英语·基础包",
    "quickmt-en-ja": "日语·增量包",
    "quickmt-en-es": "西班牙语·增量包",
    "m2m100-418m-int8": "多语直译·M2M100",
}
SCHEMA_LABELS = {
    "language_input_flypy": "小鹤双拼方案",
    "language_input_pinyin": "全拼方案",
}
# Switch labels are NOT duplicated here: ``rime_settings.SWITCH_LABELS`` is the
# single source of truth so the same switch never appears under two names on
# different surfaces (see ``switch_label`` below).


def backend_label(value: str | None) -> str:
    """Human label for a backend code, without ever leaking the code."""
    if not value:
        return "未知"
    return BACKEND_LABELS.get(str(value), "未知")


def language_label(code: str | None) -> str:
    """Human label for a language code."""
    if not code:
        return "未知"
    return LANGUAGE_LABELS.get(str(code), "其他语言")


def component_label(component_id: str, display_name: str | None = None) -> str:
    """Human label for a component id (falls back to a human display name)."""
    if component_id in COMPONENT_LABELS:
        return COMPONENT_LABELS[component_id]
    if display_name and display_name != component_id:
        return display_name
    return "未命名模型包"


def switch_label(name: str) -> str:
    """Human label for a schema switch key.

    Delegates to :data:`rime_settings.SWITCH_LABELS` — the single source of
    truth shared by every surface — so a switch is never rendered under two
    different names.
    """
    return rime_settings.SWITCH_LABELS.get(name, "选项")


def format_size(value: int | None) -> str:
    """Format a byte count as MB/GB (or ``-`` when unknown)."""
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number >= 1024**3:
        return f"{number / 1024**3:.2f} GB"
    if number >= 1024**2:
        return f"{number / 1024**2:.1f} MB"
    if number >= 1024:
        return f"{number / 1024:.0f} KB"
    return f"{int(number)} B"


# --- assets -----------------------------------------------------------------

def _project_root() -> Path:
    """Repository root of the app (``settings-app``)."""
    return Path(__file__).resolve().parents[2]


def assets_dir() -> Path:
    """App-owned assets directory (``settings-app/assets``)."""
    return _project_root() / "assets"


def icon_ico_path() -> Path:
    """Path of the committed tray/installer icon (``assets/icon.ico``)."""
    return assets_dir() / "icon.ico"


def theme_qss_path() -> Path:
    """Path of the light QSS theme loaded at startup (``assets/theme.qss``)."""
    return assets_dir() / "theme.qss"


# --- optional PySide6 import -------------------------------------------------
# Deferred (never imported at module level for ``--selftest``) but still guarded
# so the diagnostic report can run even where PySide6 is unavailable.

try:  # pragma: no cover - exercised by the smoke test
    from PySide6.QtCore import QObject, QPointF, QSize, Qt, QThread, Signal, Slot
    from PySide6.QtGui import (
        QAction,
        QBrush,
        QColor,
        QIcon,
        QPainter,
        QPen,
        QPixmap,
        QPolygonF,
    )
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSpinBox,
        QStackedWidget,
        QSystemTrayIcon,
        QTableWidget,
        QTableWidgetItem,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

    _HAS_QT = True
    _QT_IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # pragma: no cover - failure path
    _HAS_QT = False
    _QT_IMPORT_ERROR = _exc


# --- selftest ---------------------------------------------------------------

def _installed_components(model_root: Path | None) -> list[str]:
    """List directories under ``model_root`` that contain ``installed.json``."""
    if model_root is None:
        return []
    try:
        entries = sorted(model_root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    found: list[str] = []
    for entry in entries:
        try:
            if entry.is_dir() and (entry / "installed.json").is_file():
                found.append(entry.name)
        except OSError:
            continue
    return found


def _atomic_write_probe() -> dict:
    """Prove atomic_write_text emits UTF-8 no-BOM + LF in a temp dir."""
    tmp = Path(tempfile.mkdtemp(prefix="language_input_settings_selftest_"))
    result: dict = {
        "temp_dir_created": True,
        "no_bom": False,
        "lf_only": False,
        "content_roundtrip_ok": False,
    }
    try:
        probe = tmp / "probe.txt"
        payload = "第一行\nsecond line\n"
        yaml_io.atomic_write_text(probe, payload)
        raw = probe.read_bytes()
        result["no_bom"] = not raw.startswith(b"\xef\xbb\xbf")
        result["lf_only"] = b"\r" not in raw
        result["content_roundtrip_ok"] = probe.read_text(encoding="utf-8") == payload
        result["first_bytes_hex"] = raw[:4].hex()

        if yaml_io.HAS_RUAMEL:
            yaml_probe = tmp / "app-owned.yaml"
            yaml_io.atomic_write_yaml(yaml_probe, {"greeting": "中文", "n": 1})
            yraw = yaml_probe.read_bytes()
            result["yaml_no_bom"] = not yraw.startswith(b"\xef\xbb\xbf")
            result["yaml_lf_only"] = b"\r" not in yraw
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        result["temp_dir_cleaned"] = not tmp.exists()
    return result


def _pyside6_info() -> tuple[bool, str | None, str | None]:
    """Return ``(import_ok, PySide6.__version__, Qt runtime version)``.

    Never creates a ``QApplication``; importing Qt bindings is side-effect free.
    """
    try:
        import PySide6
    except Exception:
        return False, None, None
    version = getattr(PySide6, "__version__", None)
    qt_version = None
    try:
        from PySide6 import QtCore

        qt_version = QtCore.qVersion()
    except Exception:
        qt_version = None
    return True, version, qt_version


def run_selftest() -> int:
    """Print a JSON diagnostic report; return 0 when critical checks pass.

    Strictly read-only with respect to the user's Rime directory: the only
    files touched live in a temp directory that is deleted before returning.
    No GUI is opened and no ``QApplication`` is created (PySide6 is imported
    only to report its version).
    """
    resolved = paths.resolve_paths()

    try:
        from .deploy import is_deployer_running

        deployer_running = is_deployer_running()
    except Exception:
        deployer_running = None

    write_probe = _atomic_write_probe()
    pyside6_import_ok, pyside6_version, qt_version = _pyside6_info()

    critical = {
        "paths_resolved": True,
        "atomic_write_no_bom": bool(write_probe.get("no_bom")),
        "atomic_write_lf_only": bool(write_probe.get("lf_only")),
        "atomic_write_roundtrip": bool(write_probe.get("content_roundtrip_ok")),
    }

    report = {
        "app": "language-input-settings",
        "version": __version__,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": sys.platform,
        "pyside6_import_ok": pyside6_import_ok,
        "pyside6_version": pyside6_version,
        "qt_version": qt_version,
        "ruamel_import_ok": yaml_io.HAS_RUAMEL,
        "rime_user_dir": str(resolved.rime_user_dir),
        "rime_user_dir_exists": resolved.rime_user_dir_exists,
        "weasel_root": str(resolved.weasel_root) if resolved.weasel_root else None,
        "weasel_root_exists": resolved.weasel_root_exists,
        "deployer_exe": str(resolved.deployer_exe) if resolved.deployer_exe else None,
        "deployer_exists": resolved.deployer_exists,
        "server_exe": str(resolved.server_exe) if resolved.server_exe else None,
        "server_exists": resolved.server_exists,
        "model_host_exe": (
            str(resolved.model_host_exe) if resolved.model_host_exe else None
        ),
        "model_host_exists": resolved.model_host_exists,
        "model_root": str(resolved.model_root) if resolved.model_root else None,
        "model_root_exists": resolved.model_root_exists,
        "installed_components": _installed_components(resolved.model_root),
        "packs_catalog": (
            str(resolved.packs_catalog) if resolved.packs_catalog else None
        ),
        "packs_catalog_exists": resolved.packs_catalog_exists,
        "m2m100_catalog": (
            str(resolved.m2m100_catalog) if resolved.m2m100_catalog else None
        ),
        "m2m100_catalog_exists": resolved.m2m100_catalog_exists,
        "deployer_running": deployer_running,
        "atomic_write_probe": write_probe,
        "critical_checks": critical,
        "passed": all(critical.values()),
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


# --- single instance --------------------------------------------------------

def acquire_single_instance(name: str = _SINGLE_INSTANCE_MUTEX) -> bool:
    """Create the named mutex; return False if another instance owns it.

    The handle is intentionally never closed (held for process lifetime).  On
    non-Windows this always succeeds.
    """
    global _instance_handle
    if sys.platform != "win32":
        return True

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.restype = wintypes.HANDLE
    create_mutex.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]

    ctypes.set_last_error(0)
    handle = create_mutex(None, False, name)
    if not handle:
        return False
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _instance_handle = handle
    return True


def show_existing_instance() -> bool:
    """Bring the already-running settings window forward on a second launch."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    title = f"Language Input 设置  v{__version__}"
    for _ in range(20):
        handle = user32.FindWindowW(None, title)
        if handle:
            user32.ShowWindow(handle, 9)  # SW_RESTORE also shows a tray-hidden window.
            user32.SetForegroundWindow(handle)
            return True
        time.sleep(0.1)
    return False


# --- read-only overview data ------------------------------------------------

def _overview_rows(resolved: paths.PathResolution) -> list[tuple[str, str]]:
    def mark(exists: bool) -> str:
        return "✓" if exists else "✗"

    components = _installed_components(resolved.model_root)
    components_text = (
        f"{len(components)} 个：" + "、".join(component_label(cid) for cid in components)
        if components
        else "0 个"
    )

    rows = [
        (
            "Rime 用户目录",
            f"{resolved.rime_user_dir}  [{mark(resolved.rime_user_dir_exists)}]",
        ),
        (
            "Weasel 根目录",
            f"{resolved.weasel_root}  [{mark(resolved.weasel_root_exists)}]"
            if resolved.weasel_root
            else "未解析（HKLM WeaselRoot / InstallDir 缺失）",
        ),
        (
            "部署器 WeaselDeployer.exe",
            f"{resolved.deployer_exe}  [{mark(resolved.deployer_exists)}]"
            if resolved.deployer_exe
            else "未解析",
        ),
        (
            "服务器 WeaselServer.exe",
            f"{resolved.server_exe}  [{mark(resolved.server_exists)}]"
            if resolved.server_exe
            else "未解析",
        ),
        (
            "模型宿主 LanguageInputModelHost.exe",
            f"{resolved.model_host_exe}  [{mark(resolved.model_host_exists)}]"
            if resolved.model_host_exe
            else "未解析",
        ),
        (
            "模型根目录",
            f"{resolved.model_root}  [{mark(resolved.model_root_exists)}]"
            if resolved.model_root
            else "未解析",
        ),
        ("已安装模型组件数", str(len(components))),
        ("已安装模型组件", components_text),
    ]
    rows.extend(_running_rows())
    return rows


def _running_rows() -> list[tuple[str, str]]:
    """Read-only running-state rows for the deployer and server/host processes."""
    try:
        from .deploy import is_deployer_running

        deployer = is_deployer_running()
    except Exception:
        deployer = None

    try:
        from .models_install import running_processes

        processes = running_processes()
    except Exception:
        processes = None

    if deployer is None:
        deployer_text = "未知"
    else:
        deployer_text = "运行中" if deployer else "未运行"

    if processes is None:
        processes_text = "未知"
    elif processes:
        processes_text = "运行中：" + "、".join(processes)
    else:
        processes_text = "未运行"

    return [
        ("部署器运行状态", deployer_text),
        ("服务 / 宿主进程", processes_text),
    ]


# --- GUI (PySide6, main thread) ---------------------------------------------

def _apply_theme(app) -> None:
    """Load ``assets/theme.qss`` onto ``app`` (silently skipped if missing)."""
    try:
        css = theme_qss_path().read_text(encoding="utf-8")
    except OSError:
        return
    css = css.replace("@ASSETS@", assets_dir().as_posix())
    app.setStyleSheet(css)


if _HAS_QT:

    # --- design-system helpers ---------------------------------------------

    def _title_label(text: str) -> "QLabel":
        label = QLabel(text)
        label.setObjectName("PageTitle")
        return label

    def _description_label(text: str) -> "QLabel":
        label = QLabel(text)
        label.setObjectName("PageDescription")
        label.setWordWrap(True)
        return label

    def _caption_label(text: str) -> "QLabel":
        label = QLabel(text)
        label.setObjectName("Caption")
        label.setWordWrap(True)
        return label

    def _page_header(title: str, description: str) -> "QWidget":
        box = QWidget()
        box.setObjectName("CardInner")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(_title_label(title))
        layout.addWidget(_description_label(description))
        return box

    def _update_strip(label: "QLabel", state: str, text: str) -> None:
        """Set an inline status strip's text + semantic state (re-polishes)."""
        label.setProperty("state", state)
        label.setText(text)
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.update()

    def _scrollable(page: "QWidget") -> "QScrollArea":
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setWidget(page)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return area

    class StateDot(QLabel):
        """A small coloured state dot (success / warning / error / neutral)."""

        _COLORS = {
            "success": "#1E8E3E",
            "warning": "#B26A00",
            "error": "#C62828",
            "neutral": "#9AA4AF",
        }

        def __init__(self, state: str = "neutral", size: int = 8, parent=None) -> None:
            super().__init__(parent)
            self._size = size
            self.setFixedSize(size, size)
            self.set_state(state)

        def set_state(self, state: str) -> None:
            color = self._COLORS.get(state, self._COLORS["neutral"])
            self.setStyleSheet(
                f"background-color: {color}; border-radius: 999px;"
            )

    class StateChip(QLabel):
        """A pill-shaped state chip (semantic tint + contrast-safe text)."""

        _STYLES = {
            "success": ("#146C2E", "#F0F8F1"),
            "neutral": ("#5A6672", "#F2F4F7"),
            "warning": ("#8A5200", "#FBF4E9"),
            "error": ("#C62828", "#FCF0F0"),
            "accent": ("#2456B8", "#EAF1FE"),
        }

        def __init__(self, text: str, state: str = "neutral", parent=None) -> None:
            super().__init__(text, parent)
            self.setAlignment(Qt.AlignCenter)
            fg, bg = self._STYLES.get(state, self._STYLES["neutral"])
            self.setStyleSheet(
                f"color: {fg}; background-color: {bg}; border-radius: 999px;"
                " padding: 2px 8px; font-size: 12px; font-weight: 600;"
            )

    class Card(QFrame):
        """A white surface card: 1px border, 8px radius, 16px padding."""

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setObjectName("Card")
            self.setFrameShape(QFrame.NoFrame)
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.body = QVBoxLayout(self)
            self.body.setContentsMargins(16, 16, 16, 16)
            self.body.setSpacing(8)

    class CollapsibleSection(QWidget):
        """A card that expands / collapses from a header button."""

        def __init__(self, title: str, *, expanded: bool = False, parent=None) -> None:
            super().__init__(parent)
            self.setObjectName("CardInner")
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(8)

            self.header = QToolButton()
            self.header.setObjectName("CollapsibleHeader")
            self.header.setText(title)
            self.header.setCheckable(True)
            self.header.setChecked(expanded)
            self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
            self.header.setSizePolicy(self.header.sizePolicy())
            self.header.clicked.connect(self._toggle)
            outer.addWidget(self.header)

            self.content = QWidget()
            self.content.setObjectName("CollapsibleContent")
            self.content.setAttribute(Qt.WA_StyledBackground, True)
            self.content_layout = QVBoxLayout(self.content)
            self.content_layout.setContentsMargins(16, 12, 16, 16)
            self.content_layout.setSpacing(8)
            self.content.setVisible(expanded)
            outer.addWidget(self.content)

        def _toggle(self) -> None:
            visible = self.header.isChecked()
            self.content.setVisible(visible)
            self.header.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)

        def addWidget(self, widget: "QWidget") -> None:
            self.content_layout.addWidget(widget)

        def addLayout(self, layout) -> None:
            self.content_layout.addLayout(layout)

    # --- off-thread transactions + busy presentation ------------------------

    class TransactionWorker(QObject):
        """Runs one callable off the Qt main thread; emits, never touches widgets.

        Created fresh for every run.  ``run`` is invoked by the owning
        ``QThread``'s event loop; it only emits signals.  The receiving slots
        live on a main-thread object, so Qt delivers the payload with a queued
        connection and every widget mutation stays on the GUI thread.
        """

        started = Signal(str)
        finished = Signal(object)
        failed = Signal(str)

        def __init__(self, func, status: str, kwargs: dict | None = None) -> None:
            super().__init__()
            self._func = func
            self._status = status
            self._kwargs = dict(kwargs or {})
            self._ran = False

        @Slot()
        def run(self) -> None:
            if self._ran:  # guard against a double-start
                return
            self._ran = True
            self.started.emit(self._status)
            try:
                result = self._func(**self._kwargs)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                self.failed.emit(f"{type(exc).__name__}: {exc}")
            else:
                self.finished.emit(result)

    class ProgressBridge(QObject):
        """Marshals a worker-thread progress callback onto the GUI thread.

        Model downloads report progress from the worker thread; ``Signal.emit``
        is thread-safe, so the callback only emits and the connected slot
        (running on the main thread) is the only thing that touches a widget.
        """

        updated = Signal(object, object)  # (current, total)

        def report(self, current, total=None, _path=None) -> None:
            self.updated.emit(current, total)

    class BusyStrip(QWidget):
        """Shared progress bar + status line for a transaction.

        Hidden while idle.  Stop/start transactions have no meaningful progress
        fraction, so they use an indeterminate bar plus a human status line.
        A download has a real fraction, so it switches the bar to a determinate
        0–100 range via :meth:`set_progress`.
        """

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setObjectName("BusyStrip")
            row = QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            self.bar = QProgressBar()
            self.bar.setObjectName("BusyBar")
            self.bar.setRange(0, 0)  # indeterminate (0,0)
            self.bar.setTextVisible(False)
            self.bar.setFixedWidth(160)
            self.label = QLabel("")
            self.label.setObjectName("BusyText")
            self.label.setWordWrap(True)
            row.addWidget(self.bar)
            row.addWidget(self.label, 1)
            self.setVisible(False)

        def start(self, text: str, determinate: bool = False) -> None:
            self.label.setText(text)
            if determinate:
                self.bar.setRange(0, 100)
                self.bar.setValue(0)
            else:
                self.bar.setRange(0, 0)
            self.setVisible(True)

        def set_progress(self, current, total=None) -> None:
            """Switch to a determinate bar and show ``current / total``."""
            if total is None or total <= 0:
                if self.bar.maximum() != 0:
                    self.bar.setRange(0, 0)
                return
            if self.bar.maximum() != 100:
                self.bar.setRange(0, 100)
            value = int(float(current) * 100.0 / float(total))
            self.bar.setValue(max(0, min(100, value)))

        def stop(self) -> None:
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.setVisible(False)

    class TransactionController(QObject):
        """Runs a blocking callable on a worker thread and presents a busy state.

        There is deliberately **no Cancel button**.  A transaction is a
        stop -> poll -> write -> deploy -> start sequence; aborting it halfway
        could leave WeaselServer stopped (the user cannot type at all) or the
        configuration half-written.  The correct affordance is a visible
        "the app is working" state that blocks re-entry, not a kill switch.
        """

        # Emitted on the main thread after the busy state is cleared and the
        # per-run success/error callback has run.  Useful for tests.
        run_finished = Signal(object)

        def __init__(self, busy: "BusyStrip", controls, parent=None) -> None:
            super().__init__(parent)
            self._busy = busy
            self._controls = controls  # callable -> iterable[QWidget]
            self._thread: QThread | None = None
            self._worker: TransactionWorker | None = None
            self._active = False
            self._callbacks: tuple | None = None
            self._active_busy = busy
            self._active_determinate = False

        @property
        def active(self) -> bool:
            return self._active

        def run(
            self,
            status: str,
            func,
            *,
            on_success,
            on_error,
            busy: "BusyStrip | None" = None,
            determinate: bool = False,
            **kwargs,
        ) -> bool:
            """Start ``func(**kwargs)`` off-thread; return False if already busy.

            ``determinate=True`` starts the busy bar as a determinate 0–100 bar
            so a progress callback can drive it.
            """
            if self._active:
                return False
            self._active = True
            self._callbacks = (on_success, on_error)
            self._active_busy = busy or self._busy
            self._active_determinate = bool(determinate)
            self._active_busy.start(status, self._active_determinate)
            self._set_controls(False)

            thread = QThread()
            worker = TransactionWorker(func, status, kwargs)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.started.connect(self._on_started)
            worker.finished.connect(self._on_finished)
            worker.failed.connect(self._on_failed)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._on_thread_finished)
            self._thread = thread
            self._worker = worker
            thread.start()
            return True

        # -- main-thread slots -------------------------------------------------

        @Slot(str)
        def _on_started(self, status: str) -> None:
            self._active_busy.start(status, self._active_determinate)

        @Slot(object)
        def _on_finished(self, result: object) -> None:
            on_success, _on_error = self._callbacks or (None, None)
            self._callbacks = None
            self._active_busy.stop()
            self._set_controls(True)
            if callable(on_success):
                on_success(result)
            self.run_finished.emit(result)

        @Slot(str)
        def _on_failed(self, message: str) -> None:
            _on_success, on_error = self._callbacks or (None, None)
            self._callbacks = None
            self._active_busy.stop()
            self._set_controls(True)
            if callable(on_error):
                on_error(message)
            self.run_finished.emit({"_failed": True, "error": message})

        @Slot()
        def _on_thread_finished(self) -> None:
            # ``_active`` stays True until here, so a new run cannot start
            # while a thread is still cleaning up; exactly one thread is ever
            # in flight, so clearing unconditionally is safe.
            self._thread = None
            self._worker = None
            self._active = False

        def _set_controls(self, enabled: bool) -> None:
            for widget in list(self._controls()):
                if widget is not None:
                    widget.setEnabled(enabled)

    # --- icons (drawn with QPainter) ---------------------------------------

    def _make_nav_icon(kind: str, color: str) -> "QPixmap":
        # Render at 2x physical resolution and tag the pixmap with a device
        # pixel ratio of 2.0, so the icon stays crisp on Windows 125%–200%
        # scaling while its logical size remains 16x16.
        scale = 2
        pixmap = QPixmap(16 * scale, 16 * scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(scale, scale)
        pen = QPen(QColor(color))
        pen.setWidthF(1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if kind == "overview":
            for x, y in ((2, 2), (9, 2), (2, 9), (9, 9)):
                painter.drawRoundedRect(x, y, 5, 5, 1.2, 1.2)
        elif kind == "translation":
            painter.drawLine(QPointF(2, 5.5), QPointF(12, 5.5))
            painter.drawPolyline(
                QPolygonF([QPointF(9.5, 3), QPointF(12, 5.5), QPointF(9.5, 8)])
            )
            painter.drawLine(QPointF(14, 10.5), QPointF(4, 10.5))
            painter.drawPolyline(
                QPolygonF([QPointF(6.5, 8), QPointF(4, 10.5), QPointF(6.5, 13)])
            )
        elif kind == "models":
            painter.drawRoundedRect(2.5, 4.5, 11, 9, 1.2, 1.2)
            painter.drawLine(QPointF(2.5, 7.5), QPointF(13.5, 7.5))
            painter.drawLine(QPointF(6, 2.5), QPointF(6, 4.5))
            painter.drawLine(QPointF(10, 2.5), QPointF(10, 4.5))
        elif kind == "dictionary":
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(2, 3.5),
                        QPointF(8, 2),
                        QPointF(8, 13),
                        QPointF(2, 12),
                        QPointF(2, 3.5),
                    ]
                )
            )
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(14, 3.5),
                        QPointF(8, 2),
                        QPointF(8, 13),
                        QPointF(14, 12),
                        QPointF(14, 3.5),
                    ]
                )
            )
        elif kind == "appearance":
            painter.drawEllipse(QPointF(8, 8), 5.5, 5.5)
            painter.setBrush(QBrush(QColor(color)))
            for x, y in ((6, 5.5), (10, 5.5), (5.5, 9.2)):
                painter.drawEllipse(QPointF(x, y), 0.9, 0.9)
            painter.setBrush(Qt.NoBrush)
        elif kind == "keys":
            painter.drawRoundedRect(1.5, 4, 13, 8, 1.5, 1.5)
            painter.setBrush(QBrush(QColor(color)))
            for x in (4, 7, 10):
                painter.drawEllipse(QPointF(x, 6.5), 0.8, 0.8)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(5, 9.8), QPointF(11, 9.8))
        elif kind == "about":
            painter.drawEllipse(QPointF(8, 8), 6, 6)
            painter.setBrush(QBrush(QColor(color)))
            painter.drawEllipse(QPointF(8, 4.8), 0.9, 0.9)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(8, 7), QPointF(8, 11))

        painter.end()
        pixmap.setDevicePixelRatio(float(scale))
        return pixmap

    def _nav_icon(kind: str) -> "QIcon":
        icon = QIcon()
        icon.addPixmap(_make_nav_icon(kind, "#5A6672"), QIcon.Normal)
        icon.addPixmap(_make_nav_icon(kind, "#2F6FEB"), QIcon.Selected)
        return icon

    # --- pages --------------------------------------------------------------

    class OverviewPage(QWidget):
        """Read-only summary cards: backend, models, gloss, learning, service."""

        def __init__(self, title: str, description: str) -> None:
            super().__init__()
            self.setObjectName("PageRoot")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.addWidget(_page_header(title, description))

            resolved = paths.resolve_paths()
            self._status_cards: dict[str, Card] = {}
            for label, value, state, hint in self._card_values(resolved):
                card = _status_card(label, value, state, hint)
                self._status_cards[label] = card
                layout.addWidget(card)

            details = CollapsibleSection("详情（路径与技术信息）")
            grid = QGridLayout()
            grid.setColumnStretch(1, 1)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(8)
            for row, (key, value) in enumerate(_overview_rows(resolved)):
                key_label = QLabel(key)
                key_label.setObjectName("DetailLabel")
                value_label = QLabel(value)
                value_label.setObjectName("DetailValue")
                value_label.setWordWrap(True)
                value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(key_label, row, 0, Qt.AlignTop)
                grid.addWidget(value_label, row, 1, Qt.AlignTop)
            details.addLayout(grid)
            layout.addWidget(details)
            layout.addStretch(1)

        def refresh(self) -> None:
            for label, value, state, hint in self._card_values(paths.resolve_paths()):
                card = self._status_cards[label]
                card.value_label.setText(value)
                card.hint_label.setText(hint)
                card.state_dot.set_state(state)

        @classmethod
        def _card_values(cls, resolved: paths.PathResolution):
            backend, backend_source = cls._backend_info()
            models_installed, models_hint = cls._models_info(resolved)
            gloss_value, gloss_state, gloss_hint = cls._gloss_info(resolved)
            learn_value, learn_state, learn_hint = cls._learning_info(resolved)
            service_value, service_state, service_hint = cls._service_info()

            return [
                (
                    "AI 后端",
                    backend,
                    "success" if backend_source[1] else "neutral",
                    f"{backend_source[0]}；AI 译注还需在「按键与开关」页启用。",
                ),
                (
                    "本地模型",
                    models_installed,
                    "success" if models_installed != "未安装" else "warning",
                    models_hint,
                ),
                ("译注显示", gloss_value, gloss_state, gloss_hint),
                ("学习库", learn_value, learn_state, learn_hint),
                ("服务状态", service_value, service_state, service_hint),
            ]

        # -- data helpers --

        @staticmethod
        def _backend_info() -> tuple[str, tuple[str, bool]]:
            try:
                from . import env_config, server

                running = server.running_pids()
                server_env = server.read_server_env() if running else None
                if server_env is not None:
                    backend = env_config.describe_effective_backend(
                        server_env, appconfig.saved_backend()
                    ).value
                    source = (
                        "来自运行中的服务"
                        if env_config.effective_remote_config(server_env)["enabled"] is not None
                        else "服务运行中，来自已保存配置",
                        True,
                    )
                else:
                    backend = appconfig.saved_backend() or "local"
                    source = ("服务未运行，显示已保存配置", False)
            except Exception:
                backend = None
                source = ("状态不可用", False)
            return backend_label(backend), source

        @staticmethod
        def _models_info(resolved: paths.PathResolution) -> tuple[str, str]:
            try:
                from . import models_catalog

                ids = models_catalog.installed_ids(resolved.model_root)
            except Exception:
                ids = []
            if not ids:
                return "未安装", "尚未安装任何本地模型包；安装后可离线译注。"
            names = "、".join(component_label(cid) for cid in ids)
            return f"已安装 {len(ids)} 个", f"已安装：{names}"

        @staticmethod
        def _gloss_info(resolved: paths.PathResolution) -> tuple[str, str, str]:
            try:
                from . import gloss_badge

                state = gloss_badge.plain_gloss_state(resolved.rime_user_dir)
                active = bool(state.get("active"))
                needs_update = bool(state.get("needs_update"))
            except Exception:
                active = False
                needs_update = False
            if needs_update:
                return "简洁译注", "warning", "旧版过滤器副本需要更新；到「翻译」页点击更新。"
            if active:
                return "简洁译注", "success", "已隐藏候选窗中的语言 / 词性标签。"
            return "标准译注", "neutral", "候选窗保留语言标签；可在「翻译」页开启简洁译注。"

        @staticmethod
        def _learning_info(resolved: paths.PathResolution) -> tuple[str, str, str]:
            userdb = resolved.rime_user_dir / "luna_pinyin.userdb"
            try:
                exists = userdb.is_dir()
            except OSError:
                exists = False
            if exists:
                return "学习已开启", "success", "常用词会随输入自动前移（用户词典已启用）。"
            return "未检测到学习库", "warning", "未发现用户词典；首次输入并部署后会创建。"

        @staticmethod
        def _service_info() -> tuple[str, str, str]:
            try:
                from . import server

                running = server.running_pids()
            except Exception:
                running = []
            try:
                from .deploy import is_deployer_running

                deployer = is_deployer_running()
            except Exception:
                deployer = False
            if running:
                hint = f"服务运行中（{len(running)} 个进程）"
                if deployer:
                    hint += "；部署器正在运行"
                return "运行中", "success", hint
            return "未运行", "neutral", "输入法服务当前未运行，启动后会自动拉起。"

    def _status_card(label: str, value: str, state: str, hint: str) -> "Card":
        card = Card()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        name = QLabel(label)
        name.setObjectName("CardLabel")
        top.addWidget(name)
        top.addStretch(1)
        card.state_dot = StateDot(state)
        top.addWidget(card.state_dot, 0, Qt.AlignTop)
        card.body.addLayout(top)

        value_label = QLabel(value)
        value_label.setObjectName("CardValue")
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.value_label = value_label
        card.body.addWidget(value_label)

        hint_label = QLabel(hint)
        hint_label.setObjectName("CardHint")
        hint_label.setWordWrap(True)
        card.hint_label = hint_label
        card.body.addWidget(hint_label)
        return card

    class ModelsPage(QWidget):
        """The 模型 page: component table, status chips and real actions.

        Every action that touches the network or the model root (download,
        install, verify) runs through the shared :class:`TransactionController`
        (off the GUI thread); a download drives the determinate busy bar.
        """

        def __init__(self, title: str, description: str) -> None:
            super().__init__()
            self.setObjectName("PageRoot")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.addWidget(_page_header(title, description))

            self._rows: list[str] = []  # table row index -> component id

            actions = QHBoxLayout()
            actions.setSpacing(8)
            self.refresh_button = QPushButton("刷新列表")
            self.refresh_button.setObjectName("PrimaryButton")
            self.refresh_button.clicked.connect(self._reload)
            actions.addWidget(self.refresh_button)
            actions.addStretch(1)
            self.open_button = QPushButton("打开模型目录")
            self.open_button.clicked.connect(self._open_model_root)
            actions.addWidget(self.open_button)
            layout.addLayout(actions)

            action_card = Card()
            action_heading = QLabel("组件操作")
            action_heading.setObjectName("SectionHeading")
            action_card.body.addWidget(action_heading)
            action_card.body.addWidget(
                _caption_label(
                    "先在下方表格选择一行，再执行操作。下载并安装会自动处理依赖；"
                    "安装会重启输入法服务（期间无法打字）。"
                )
            )
            self.selection_label = QLabel("未选择组件")
            self.selection_label.setObjectName("CardHint")
            self.selection_label.setWordWrap(True)
            action_card.body.addWidget(self.selection_label)

            button_row = QHBoxLayout()
            button_row.setSpacing(8)
            self.download_button = QPushButton("下载并安装")
            self.download_button.setObjectName("PrimaryButton")
            self.download_button.clicked.connect(self._on_download)
            button_row.addWidget(self.download_button)
            self.install_dir_button = QPushButton("从目录安装…")
            self.install_dir_button.clicked.connect(self._on_install_from_dir)
            button_row.addWidget(self.install_dir_button)
            self.install_pack_button = QPushButton("从模型包安装…")
            self.install_pack_button.setToolTip(
                "选择与本应用目录匹配的已验证模型包文件（.limodel），"
                "经冻结的模型宿主导入。"
            )
            self.install_pack_button.clicked.connect(self._on_install_from_limodel)
            button_row.addWidget(self.install_pack_button)
            self.verify_button = QPushButton("校验")
            self.verify_button.clicked.connect(self._on_verify)
            button_row.addWidget(self.verify_button)
            button_row.addStretch(1)
            action_card.body.addLayout(button_row)

            self.replace_check = QCheckBox("安装时替换已存在的组件")
            self.replace_check.setToolTip(
                "已安装组件支持替换（复用备份/交换/回滚安装事务）。"
                "本应用刻意不提供「删除已安装组件」：安装层没有经过验证的"
                "安全移除接口，替换是受支持的路径。"
            )
            action_card.body.addWidget(self.replace_check)

            self.action_strip = QLabel("")
            self.action_strip.setObjectName("StatusStrip")
            self.action_strip.setWordWrap(True)
            action_card.body.addWidget(self.action_strip)

            self.busy = BusyStrip()
            action_card.body.addWidget(self.busy)
            layout.addWidget(action_card)

            self.strip = QLabel("")
            self.strip.setObjectName("StatusStrip")
            self.strip.setWordWrap(True)
            layout.addWidget(self.strip)

            self.table = QTableWidget()
            self.table.setObjectName("ModelsTable")
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["名称", "大小", "状态", "适用语言"])
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setShowGrid(False)
            self.table.setAlternatingRowColors(False)
            self.table.verticalHeader().setVisible(False)
            self.table.verticalHeader().setDefaultSectionSize(32)
            self.table.setMinimumHeight(220)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.Stretch)
            self.table.itemSelectionChanged.connect(self._on_selection_changed)
            layout.addWidget(self.table, 1)

            routes_card = Card()
            heading = QLabel("语言可用性")
            heading.setObjectName("SectionHeading")
            routes_card.body.addWidget(heading)
            routes_card.body.addWidget(
                _caption_label("按已安装的模型包判断每种目标语言是否可用。")
            )
            self._routes_grid = QGridLayout()
            self._routes_grid.setHorizontalSpacing(12)
            self._routes_grid.setVerticalSpacing(8)
            routes_card.body.addLayout(self._routes_grid)
            layout.addWidget(routes_card)

            details = CollapsibleSection("详情（每个组件的大小与依赖）")
            self._detail_label = QLabel("")
            self._detail_label.setObjectName("DetailValue")
            self._detail_label.setWordWrap(True)
            self._detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details.addWidget(self._detail_label)
            layout.addWidget(details)
            layout.addStretch(1)

            self._progress = ProgressBridge(self)
            self._progress.updated.connect(self._on_download_progress)

            self._reload()

            self._tx = TransactionController(
                self.busy, self._interactive_controls, parent=self
            )

        # -- off-thread transaction support --

        def _interactive_controls(self):
            return [
                self.refresh_button,
                self.open_button,
                self.download_button,
                self.install_dir_button,
                self.install_pack_button,
                self.verify_button,
                self.replace_check,
                self.table,
            ]

        def transaction_active(self) -> bool:
            return self._tx.active

        # -- helpers --

        def _reload(self) -> None:
            from . import models_catalog

            model_root = paths.model_root(paths.rime_user_dir())
            try:
                components = models_catalog.load_components()
                installed = set(models_catalog.installed_ids(model_root))
            except Exception as exc:  # pragma: no cover - defensive
                components = {}
                installed = set()
                _update_strip(self.strip, "error", f"模型目录不可用：{exc}")
            else:
                _update_strip(
                    self.strip,
                    "neutral",
                    f"共 {len(components)} 个组件，已安装 {len(installed)} 个。"
                    "选择一行后使用上方的「下载 / 安装 / 校验」操作。",
                )

            self.open_button.setEnabled(bool(model_root and model_root.is_dir()))

            rows = sorted(
                components.items(),
                key=lambda item: component_label(item[0], item[1].display_name),
            )
            self.table.setRowCount(len(rows))
            self._rows = [component_id for component_id, _component in rows]
            detail_lines: list[str] = []
            for row, (component_id, component) in enumerate(rows):
                is_installed = component_id in installed

                name_item = QTableWidgetItem(
                    component_label(component_id, component.display_name)
                )
                name_item.setToolTip(f"组件标识：{component_id}")
                self.table.setItem(row, 0, name_item)

                size_value = (
                    component.file_size
                    if component.file_size is not None
                    else component.runtime_bytes
                )
                size_item = QTableWidgetItem(format_size(size_value))
                size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                size_item.setToolTip(f"{size_value if size_value is not None else '-'} 字节")
                self.table.setItem(row, 1, size_item)

                status_item = QTableWidgetItem("已安装" if is_installed else "未安装")
                status_item.setTextAlignment(Qt.AlignCenter)
                status_item.setForeground(
                    QBrush(QColor("#146C2E" if is_installed else "#5A6672"))
                )
                self.table.setItem(row, 2, status_item)

                languages = self._languages_for(component)
                lang_item = QTableWidgetItem(languages)
                lang_item.setToolTip(f"组件标识：{component_id}")
                self.table.setItem(row, 3, lang_item)

                deps = "、".join(
                    component_label(dep) for dep in component.requires
                ) or "无"
                detail_lines.append(
                    f"{component_label(component_id, component.display_name)}"
                    f"（标识 {component_id}）：大小 {format_size(size_value)}，"
                    f"依赖 {deps}，"
                    f"{'已安装' if is_installed else '未安装'}。"
                )
            self._detail_label.setText("\n".join(detail_lines) or "（无组件）")

            self._reload_routes(models_catalog, installed, model_root)
            self._on_selection_changed()

        @staticmethod
        def _languages_for(component) -> str:
            if component.provides:
                return "、".join(language_label(code) for code in component.provides)
            if component.backend == "m2m100":
                return "多语言直译"
            return "语言中转"

        def _reload_routes(self, models_catalog, installed, model_root) -> None:
            while self._routes_grid.count():
                item = self._routes_grid.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            try:
                for column, backend in enumerate(("quickmt", "m2m100")):
                    coverage = models_catalog.route_coverage(
                        installed, root=None, backend=backend
                    )
                    backend_name = (
                        "英语·基础包路线" if backend == "quickmt" else "多语直译路线"
                    )
                    label = QLabel(backend_name)
                    label.setObjectName("DetailLabel")
                    self._routes_grid.addWidget(label, 0, column, Qt.AlignTop)
                    for index, language in enumerate(models_catalog.SUPPORTED_LANGUAGES):
                        info = coverage[language]
                        text = (
                            f"{language_label(language)}："
                            + ("可用" if info["available"] else "缺少组件")
                        )
                        chip = StateChip(
                            text, "success" if info["available"] else "neutral"
                        )
                        self._routes_grid.addWidget(
                            chip, index + 1, column, Qt.AlignLeft
                        )
            except Exception:  # pragma: no cover - defensive
                return

        def _open_model_root(self) -> None:
            model_root = paths.model_root(paths.rime_user_dir())
            if model_root is None or not model_root.is_dir():
                _update_strip(self.strip, "warning", "模型目录尚不存在。")
                return
            try:
                os.startfile(str(model_root))  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover - defensive
                _update_strip(self.strip, "error", f"无法打开模型目录：{exc}")

        # -- selection-driven actions --

        def _selected_id(self) -> str | None:
            model = self.table.selectionModel()
            if model is None:
                return None
            indexes = model.selectedRows()
            if not indexes:
                return None
            row = indexes[0].row()
            if 0 <= row < len(self._rows):
                return self._rows[row]
            return None

        def _selected_component(self):
            from . import models_catalog

            component_id = self._selected_id()
            if component_id is None:
                return None
            return models_catalog.load_components().get(component_id)

        def _is_installed(self, component_id: str) -> bool:
            model_root = paths.model_root(paths.rime_user_dir())
            return bool(model_root and (model_root / component_id).is_dir())

        def _on_selection_changed(self, *_args) -> None:
            component_id = self._selected_id()
            has_selection = component_id is not None
            component = self._selected_component() if has_selection else None
            can_download = bool(component and component.has_file_manifest)
            self.download_button.setEnabled(can_download)
            self.download_button.setToolTip(
                "下载并安装会自动处理缺少的依赖。"
                if can_download
                else "此组件没有逐文件校验清单，请使用已验证的模型包安装。"
            )
            self.install_dir_button.setEnabled(can_download)
            self.install_pack_button.setEnabled(has_selection)
            self.verify_button.setEnabled(
                bool(component_id) and self._is_installed(component_id)
            )
            if not has_selection:
                self.selection_label.setText("未选择组件")
                return
            if component is None:
                self.selection_label.setText("已选择组件（信息不可用）")
                return
            deps = (
                "、".join(component_label(dep) for dep in component.requires) or "无"
            )
            self.selection_label.setText(
                f"已选择：{component_label(component_id, component.display_name)}"
                f"　依赖：{deps}"
            )

        def _on_download_progress(self, current, total) -> None:
            self.busy.set_progress(current, total)

        def _on_download(self) -> None:
            component = self._selected_component()
            if component is None:
                return
            model_root = paths.model_root(paths.rime_user_dir())
            if model_root is None:
                _update_strip(self.action_strip, "warning", "无法解析模型目录。")
                return
            from . import models_catalog, models_workflow

            sources = models_catalog.download_metadata()
            components = models_catalog.load_components()
            installed = set(models_catalog.installed_ids(model_root))
            try:
                plan = models_workflow.install_plan(
                    component.component_id,
                    components,
                    installed,
                    replace=self.replace_check.isChecked(),
                )
            except ValueError as exc:
                _update_strip(self.action_strip, "error", str(exc))
                return
            if not plan:
                _update_strip(self.action_strip, "neutral", "该组件已经安装；如需重装，请勾选替换选项。")
                return
            total_bytes = sum(
                sum(item.size for item in components[cid].files) for cid in plan
            )
            label = component_label(component.component_id, component.display_name)
            planned = "、".join(component_label(cid) for cid in plan)
            if (
                QMessageBox.question(
                    self,
                    "确认下载并安装",
                    f"将下载、校验并安装：{planned}\n"
                    f"合计约 {format_size(total_bytes)}。\n{_RESTART_WARNING}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return

            self._tx.run(
                f"正在下载并安装「{label}」…",
                models_workflow.download_and_install_component,
                component_id=component.component_id,
                model_root=model_root,
                sources=sources,
                replace=self.replace_check.isChecked(),
                progress=self._progress.report,
                determinate=True,
                on_success=lambda result: self._on_download_done(label, result),
                on_error=self._on_action_error,
            )

        def _on_download_done(self, label: str, result: dict) -> None:
            self._reload()
            if result.get("error"):
                _update_strip(
                    self.action_strip,
                    "error",
                    f"「{label}」安装未完成：{result['error']}",
                )
                return
            _update_strip(
                self.action_strip,
                "success",
                f"「{label}」及所需组件已下载、校验并安装（{format_size(result.get('total_bytes'))}）。",
            )

        def _confirm_install(self, component, action_label: str) -> bool:
            label = component_label(component.component_id, component.display_name)
            installed: set[str] = set()
            try:
                from . import models_catalog

                model_root = paths.model_root(paths.rime_user_dir())
                installed = set(models_catalog.installed_ids(model_root))
            except Exception:  # pragma: no cover - defensive
                installed = set()
            missing = [dep for dep in component.requires if dep not in installed]
            deps = "、".join(component_label(dep) for dep in component.requires) or "无"
            body = (
                f"{action_label}「{label}」。\n"
                f"依赖：{deps}\n"
                f"{_RESTART_WARNING}"
            )
            if missing:
                missing_text = "、".join(component_label(dep) for dep in missing)
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle("缺少依赖")
                box.setText(
                    f"缺少依赖组件：{missing_text}。\n继续安装可能失败。\n\n{body}"
                )
                box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                box.setDefaultButton(QMessageBox.No)
                return box.exec() == QMessageBox.Yes
            return (
                QMessageBox.question(
                    self,
                    "确认安装",
                    body,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes
            )

        def _on_install_from_dir(self) -> None:
            component = self._selected_component()
            if component is None:
                return
            source = QFileDialog.getExistingDirectory(
                self, "选择包含模型源文件的目录"
            )
            if not source:
                return
            model_root = paths.model_root(paths.rime_user_dir())
            if model_root is None:
                _update_strip(self.action_strip, "warning", "无法解析模型目录。")
                return
            if not self._confirm_install(component, "从目录安装"):
                return
            from . import models_install

            label = component_label(component.component_id, component.display_name)
            self._tx.run(
                f"正在安装「{label}」…",
                models_install.install_from_dir,
                component=component,
                files_dir=source,
                model_root=model_root,
                replace=self.replace_check.isChecked(),
                on_success=lambda result: self._on_install_done(label, result),
                on_error=self._on_action_error,
            )

        def _on_install_from_limodel(self) -> None:
            component = self._selected_component()
            if component is None:
                return
            pack, _selected_filter = QFileDialog.getOpenFileName(
                self,
                "选择模型包文件",
                "",
                "模型包 (*.limodel);;所有文件 (*)",
            )
            if not pack:
                return
            model_root = paths.model_root(paths.rime_user_dir())
            if model_root is None:
                _update_strip(self.action_strip, "warning", "无法解析模型目录。")
                return
            if not self._confirm_install(component, "导入"):
                return
            from . import models_install

            label = component_label(component.component_id, component.display_name)
            self._tx.run(
                f"正在导入「{label}」…",
                models_install.install_from_limodel,
                pack_path=pack,
                model_root=model_root,
                replace=self.replace_check.isChecked(),
                m2m100=(component.backend == "m2m100"),
                on_success=lambda result: self._on_install_done(label, result),
                on_error=self._on_action_error,
            )

        def _on_install_done(self, label: str, result: dict) -> None:
            self._reload()
            if result.get("error"):
                _update_strip(
                    self.action_strip,
                    "error",
                    f"「{label}」安装未完成：{result['error']}",
                )
                return
            verification = result.get("verification") or {}
            if verification and not verification.get("ok"):
                errors = "；".join(verification.get("errors") or []) or "校验不一致"
                _update_strip(
                    self.action_strip,
                    "error",
                    f"「{label}」安装后校验未通过：{errors}",
                )
                return
            _update_strip(self.action_strip, "success", f"「{label}」已安装。")

        def _on_verify(self) -> None:
            component = self._selected_component()
            if component is None:
                return
            model_root = paths.model_root(paths.rime_user_dir())
            if model_root is None:
                _update_strip(self.action_strip, "warning", "无法解析模型目录。")
                return
            component_root = model_root / component.component_id
            if not component_root.is_dir():
                _update_strip(
                    self.action_strip, "warning", "该组件尚未安装，无法校验。"
                )
                return
            from . import models_install

            label = component_label(component.component_id, component.display_name)
            self._tx.run(
                f"正在校验「{label}」…",
                models_install.verify_installed_component,
                component_root=component_root,
                on_success=lambda result: self._on_verify_done(label, result),
                on_error=self._on_action_error,
            )

        def _on_verify_done(self, label: str, result: dict) -> None:
            files = result.get("files") or []
            if result.get("ok"):
                _update_strip(
                    self.action_strip,
                    "success",
                    f"「{label}」校验通过（{len(files)} 个文件）。",
                )
            else:
                errors = "；".join(result.get("errors") or []) or "文件大小/哈希不一致"
                _update_strip(
                    self.action_strip, "error", f"「{label}」校验未通过：{errors}"
                )

        def _on_action_error(self, message: str) -> None:
            self._reload()
            _update_strip(self.action_strip, "error", f"操作失败：{message}")
            QMessageBox.critical(self, "操作失败", message)

    class TranslationPage(QWidget):
        """翻译 page (M2): pick and apply the translation backend.

        Applying a backend is a real ``WeaselServer`` stop/start transaction
        (env is read once at process start), so nothing happens until the user
        confirms.
        """

        _BACKENDS = (
            ("local", "本地模型"),
            ("remote", "外接 API"),
            ("off", "关闭 AI 传输"),
        )

        def __init__(self, title: str, description: str, open_switches=None) -> None:
            super().__init__()
            self._open_switches = open_switches
            self.setObjectName("PageRoot")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.addWidget(_page_header(title, description))

            # -- backend segmented control --
            backend_card = Card()
            backend_card.body.addWidget(_caption_label("翻译后端"))
            segmented = QWidget()
            segmented.setObjectName("Segmented")
            segmented.setAttribute(Qt.WA_StyledBackground, True)
            segmented_layout = QHBoxLayout(segmented)
            segmented_layout.setContentsMargins(4, 4, 4, 4)
            segmented_layout.setSpacing(4)
            self._backend_buttons = QButtonGroup(self)
            self._backend_buttons.setExclusive(True)
            for value, label in self._BACKENDS:
                button = QPushButton(label)
                button.setObjectName("SegmentedButton")
                button.setCheckable(True)
                button.setProperty("backend_value", value)
                self._backend_buttons.addButton(button)
                segmented_layout.addWidget(button)
            self._backend_buttons.buttonClicked.connect(self._on_backend_changed)
            backend_card.body.addWidget(segmented)
            backend_card.body.addWidget(
                _caption_label(
                    "本地模型离线译注；外接 API 需要端点与密钥；关闭 AI 传输不影响词典译注。"
                )
            )
            layout.addWidget(backend_card)

            backend_card.body.addWidget(_caption_label("方案译注开关（按已保存设置判断）"))
            switch_row = QHBoxLayout()
            switch_row.setSpacing(8)
            self.switch_status_label = QLabel("")
            self.switch_status_label.setObjectName("CardHint")
            self.switch_status_label.setWordWrap(True)
            switch_row.addWidget(self.switch_status_label, 1)
            self.open_switches_button = QPushButton("打开按键与开关")
            self.open_switches_button.clicked.connect(self._go_to_switches)
            switch_row.addWidget(self.open_switches_button, 0, Qt.AlignTop)
            backend_card.body.addLayout(switch_row)

            # -- remote API form + primary action --
            form_card = Card()
            heading = QLabel("外接 API 配置")
            heading.setObjectName("SectionHeading")
            form_card.body.addWidget(heading)
            form = QFormLayout()
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.url_edit = QLineEdit()
            self.url_edit.setPlaceholderText("https://api.example.com/v1/chat/completions")
            self.api_key_edit = QLineEdit()
            self.api_key_edit.setEchoMode(QLineEdit.Password)
            self.api_key_toggle = QPushButton("显示")
            self.api_key_toggle.setObjectName("TertiaryButton")
            self.api_key_toggle.setCheckable(True)
            self.api_key_toggle.setFixedWidth(56)
            self.api_key_toggle.clicked.connect(self._toggle_api_key)
            key_row = QHBoxLayout()
            key_row.setSpacing(8)
            key_row.addWidget(self.api_key_edit, 1)
            key_row.addWidget(self.api_key_toggle)
            self.model_edit = QLineEdit()
            self.model_edit.setPlaceholderText("gpt-4.1-mini")
            self.language_combo = QComboBox()
            for code in ("en", "ja", "es"):
                self.language_combo.addItem(language_label(code), code)
            form.addRow("端点 URL", self.url_edit)
            form.addRow("API 密钥", key_row)
            form.addRow("模型", self.model_edit)
            form.addRow("目标语言", self.language_combo)
            form_card.body.addLayout(form)

            test_row = QHBoxLayout()
            test_row.setSpacing(8)
            self.test_button = QPushButton("测试端点连通性")
            self.test_button.setToolTip(
                "向端点发送一次最小请求（8 秒超时、不重试），报告成功与否、"
                "延迟与错误文本。密钥仅随请求头发送，绝不显示或记录。"
            )
            self.test_button.clicked.connect(self._on_test_endpoint)
            test_row.addWidget(self.test_button)
            test_row.addStretch(1)
            form_card.body.addLayout(test_row)
            layout.addWidget(form_card)

            # -- page-level primary action (kept outside the API card so it is
            #    always enabled and clearly applies the whole page) --
            actions = QHBoxLayout()
            actions.addStretch(1)
            self.apply_button = QPushButton("应用")
            self.apply_button.setObjectName("PrimaryButton")
            self.apply_button.clicked.connect(self._on_apply)
            actions.addWidget(self.apply_button)
            layout.addLayout(actions)

            self.apply_strip = QLabel("")
            self.apply_strip.setObjectName("StatusStrip")
            self.apply_strip.setWordWrap(True)
            layout.addWidget(self.apply_strip)

            self.busy = BusyStrip()
            layout.addWidget(self.busy)

            # -- advanced (collapsed): badge + schema reset --
            advanced = CollapsibleSection("高级")
            self._badge_loading = False
            self.plain_badge_check = QCheckBox("简洁译注（隐藏候选窗语言标签）")
            self.plain_badge_check.setToolTip(
                "通过用户目录中的轻量 Lua 包装文件隐藏词典语言标签，"
                "并继续使用安装目录中的最新过滤器。"
            )
            self.plain_badge_check.toggled.connect(self._on_plain_badge_toggled)
            advanced.addWidget(self.plain_badge_check)
            self.badge_status_label = QLabel("")
            self.badge_status_label.setObjectName("Caption")
            self.badge_status_label.setWordWrap(True)
            advanced.addWidget(self.badge_status_label)
            self.badge_detail_label = QLabel("")
            self.badge_detail_label.setObjectName("DetailValue")
            self.badge_detail_label.setWordWrap(True)
            self.badge_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            advanced.addWidget(self.badge_detail_label)
            self.badge_refresh_button = QPushButton("刷新译注状态")
            self.badge_refresh_button.clicked.connect(self._on_badge_refresh)
            advanced.addWidget(self.badge_refresh_button)
            layout.addWidget(advanced)

            details = CollapsibleSection("当前状态与详情")
            self.status_label = QLabel("")
            self.status_label.setObjectName("DetailValue")
            self.status_label.setWordWrap(True)
            self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details.addWidget(self.status_label)
            layout.addWidget(details)
            layout.addStretch(1)

            # -- tab order: backend -> url -> key -> toggle -> model -> language -> apply --
            segmented_buttons = self._backend_buttons.buttons()
            for earlier, later in zip(segmented_buttons, segmented_buttons[1:]):
                self.setTabOrder(earlier, later)
            self.setTabOrder(segmented_buttons[-1], self.open_switches_button)
            self.setTabOrder(self.open_switches_button, self.url_edit)
            self.setTabOrder(self.url_edit, self.api_key_edit)
            self.setTabOrder(self.api_key_edit, self.api_key_toggle)
            self.setTabOrder(self.api_key_toggle, self.model_edit)
            self.setTabOrder(self.model_edit, self.language_combo)
            self.setTabOrder(self.language_combo, self.apply_button)

            self._config = appconfig.load_config()
            self._load_config()
            self._refresh_status()
            self._refresh_plain_badge()
            self._on_backend_changed()

            self._tx = TransactionController(
                self.busy, self._interactive_controls, parent=self
            )

        # -- off-thread transaction support --

        def _interactive_controls(self):
            controls = [
                self.apply_button,
                self.test_button,
                self.plain_badge_check,
                self.badge_refresh_button,
                self.open_switches_button,
                self.url_edit,
                self.api_key_edit,
                self.api_key_toggle,
                self.model_edit,
                self.language_combo,
            ]
            controls.extend(self._backend_buttons.buttons())
            return controls

        def transaction_active(self) -> bool:
            return self._tx.active

        # -- helpers --

        def _load_config(self) -> None:
            config = self._config
            for button in self._backend_buttons.buttons():
                if button.property("backend_value") == config.backend:
                    button.setChecked(True)
            if not any(b.isChecked() for b in self._backend_buttons.buttons()):
                for button in self._backend_buttons.buttons():
                    if button.property("backend_value") == "local":
                        button.setChecked(True)
            self.url_edit.setText(config.remote_url or "")
            self.model_edit.setText(config.remote_model or "")
            index = self.language_combo.findData(config.language or "en")
            if index >= 0:
                self.language_combo.setCurrentIndex(index)
            try:
                secret = appconfig.decrypt_secret(config.api_key_dpapi)
            except Exception:
                secret = None
            if secret:
                self.api_key_edit.setText(secret)

        def _selected_backend(self) -> str:
            for button in self._backend_buttons.buttons():
                if button.isChecked():
                    return str(button.property("backend_value") or "local")
            return "local"

        def _toggle_api_key(self) -> None:
            shown = self.api_key_toggle.isChecked()
            self.api_key_edit.setEchoMode(
                QLineEdit.Normal if shown else QLineEdit.Password
            )
            self.api_key_toggle.setText("隐藏" if shown else "显示")

        def _on_backend_changed(self, *_args) -> None:
            remote = self._selected_backend() == "remote"
            for widget in (
                self.url_edit,
                self.api_key_edit,
                self.api_key_toggle,
                self.model_edit,
                self.language_combo,
            ):
                widget.setEnabled(remote)

        def _set_status(self, text: str) -> None:
            self.status_label.setText(text)

        def _go_to_switches(self) -> None:
            if self._open_switches is not None:
                self._open_switches()

        def _refresh_switch_status(self, backend: str) -> None:
            try:
                rows = rime_settings.language_input_switches()
                switches = {row["name"]: bool(row["value"]) for row in rows}
                ai_enabled = (
                    switches.get("language_input_ai")
                    or switches.get("language_input_ja")
                    or switches.get("language_input_es")
                )
                if not switches.get("language_input_gloss"):
                    message = "译注总开关已关闭；选择后端不会让候选窗显示译注。"
                elif not ai_enabled:
                    message = "方案设为词典译注；如需使用本页 AI 后端，请在按键与开关中启用 AI 翻译。"
                elif backend == "off":
                    message = "方案已选择 AI，但 AI 传输已关闭；候选窗不会得到 AI 译注。"
                elif switches.get("language_input_model_m2m100") and backend == "local":
                    message = "方案选用 M2M100，冷请求实测超过 1 秒；请切换为 QuickMT。"
                else:
                    message = f"方案已启用 AI 译注，后端为{backend_label(backend)}。"
            except Exception as exc:
                message = f"无法读取方案开关：{exc}"
            self.switch_status_label.setText(message)

        def _refresh_status(self) -> None:
            try:
                from . import env_config, models_catalog, server

                running = server.running_pids()
                server_env = server.read_server_env() if running else None
                if server_env is not None:
                    backend = env_config.describe_effective_backend(
                        server_env, appconfig.saved_backend()
                    ).value
                    remote = env_config.effective_remote_config(server_env)
                    source = (
                        "来自运行中的服务"
                        if remote["enabled"] is not None
                        else "服务运行中，来自已保存配置"
                    )
                else:
                    backend = appconfig.saved_backend() or "local"
                    remote = {}
                    source = "服务未运行，显示已保存配置"
                    server_env = {}

                model_root = paths.model_root(paths.rime_user_dir())
                try:
                    installed = models_catalog.installed_ids(model_root)
                except Exception:
                    installed = []

                _update_strip(
                    self.apply_strip,
                    "success" if backend in ("local", "remote") else "neutral",
                    f"当前后端：{backend_label(backend)}（{source}）",
                )
                self._refresh_switch_status(backend)

                lines = [
                    f"后端状态码：{backend}",
                    f"服务进程：{running if running else '未运行'}",
                    f"已安装模型（{len(installed)}）："
                    + ("、".join(component_label(cid) for cid in installed) or "无"),
                ]
                if server_env and remote.get("enabled") is not None:
                    lines.append(
                        "服务环境："
                        f"ENABLED={remote.get('enabled')!r}，"
                        f"URL={remote.get('url')!r}，"
                        f"MODEL={remote.get('model')!r}，"
                        f"LANGUAGE={remote.get('language')!r}，"
                        f"API_KEY={'已设置' if remote.get('has_api_key') else '未设置'}"
                    )
                elif server_env and backend == "remote":
                    saved = appconfig.load_config()
                    lines.append(
                        "服务配置：已保存的外接 API；"
                        f"URL={saved.remote_url!r}，MODEL={saved.remote_model!r}，"
                        f"LANGUAGE={saved.remote_language or saved.language!r}，"
                        f"API_KEY={'已保存' if saved.api_key_dpapi else '未保存'}"
                    )
                self._set_status("\n".join(lines))
            except Exception as exc:  # pragma: no cover - defensive
                _update_strip(self.apply_strip, "error", f"状态不可用：{exc}")
                self._set_status(f"状态不可用：{exc}")

        def _refresh_plain_badge(self) -> None:
            from . import gloss_badge

            try:
                state = gloss_badge.plain_gloss_state(paths.rime_user_dir())
            except Exception as exc:  # pragma: no cover - defensive
                self._badge_needs_update = False
                self._badge_loading = True
                self.plain_badge_check.setChecked(False)
                self._badge_loading = False
                self.badge_status_label.setText(f"简洁译注状态不可用：{exc}")
                self.badge_detail_label.setText("")
                return

            active = bool(state["active"])
            self._badge_needs_update = bool(state.get("needs_update"))
            self._badge_loading = True
            self.plain_badge_check.setChecked(active)
            self._badge_loading = False
            self.badge_status_label.setText(
                "简洁译注：旧版副本需更新，才能跟随内置过滤器升级"
                if self._badge_needs_update
                else "简洁译注：已开启（隐藏语言标签）"
                if active else "简洁译注：已关闭（显示语言标签）"
            )
            self.badge_refresh_button.setText(
                "更新简洁译注" if self._badge_needs_update else "刷新译注状态"
            )
            self.badge_detail_label.setText(
                f"active={state['active']}  shadow_exists={state['shadow_exists']}  "
                f"marker_is_empty={state['marker_is_empty']}\n"
                f"shadow: {state['shadow_path']}\n"
                f"installed: {state['installed_path']}\n"
                f"shadow sha256: {state['sha256'] or '（无）'}\n"
                f"installed sha256: {state['installed_sha256'] or '（无）'}"
            )

        def _on_badge_refresh(self) -> None:
            if not getattr(self, "_badge_needs_update", False):
                self._refresh_plain_badge()
                return
            from . import gloss_badge

            self._tx.run(
                "正在更新简洁译注并重新部署…",
                gloss_badge.apply_plain_gloss,
                deploy=True,
                on_success=lambda result: self._on_plain_badge_done(True, result),
                on_error=self._on_badge_refresh_error,
            )

        def _on_badge_refresh_error(self, message: str) -> None:
            self._refresh_plain_badge()
            QMessageBox.critical(self, "更新简洁译注失败", message)

        def _on_plain_badge_toggled(self, checked: bool) -> None:
            if self._badge_loading:
                return

            from . import gloss_badge

            func = (
                gloss_badge.apply_plain_gloss
                if checked
                else gloss_badge.revert_plain_gloss
            )
            started = self._tx.run(
                "正在切换简洁译注并重新部署…",
                func,
                deploy=True,
                on_success=lambda result: self._on_plain_badge_done(checked, result),
                on_error=lambda message: self._on_plain_badge_failed(checked, message),
            )
            if not started:  # another transaction is running; undo the toggle
                self._badge_loading = True
                self.plain_badge_check.setChecked(not checked)
                self._badge_loading = False

        def _on_plain_badge_done(self, checked: bool, result: dict) -> None:
            self._on_backend_changed()
            self._refresh_plain_badge()

            deploy = result.get("deploy") or {}
            if result.get("ok"):
                QMessageBox.information(
                    self,
                    "结果",
                    "简洁译注已" + ("开启" if checked else "关闭")
                    + "，已重新部署。\n"
                    "候选窗的显示变化需要重新输入后才会看到。",
                )
            else:
                QMessageBox.warning(
                    self,
                    "结果",
                    f"操作未完成：{result.get('error') or '未知错误'}\n"
                    f"部署退出码：{deploy.get('exit_code')}  "
                    f"错误输出：{deploy.get('stderr') or '（空）'}",
                )

        def _on_plain_badge_failed(self, checked: bool, message: str) -> None:
            self._on_backend_changed()
            self._badge_loading = True
            self.plain_badge_check.setChecked(not checked)
            self._badge_loading = False
            QMessageBox.critical(self, "简洁译注失败", message)

        # -- actions --

        def _on_test_endpoint(self) -> None:
            """One minimal request to the configured endpoint (off-thread).

            The request never retries, honours the app's ``allow_http`` /
            ``use_system_proxy`` network policy, uses a short timeout, and the
            API key only ever travels in the ``Authorization`` header (it is
            redacted from anything the result reports).
            """
            url = self.url_edit.text().strip()
            if not url:
                _update_strip(self.apply_strip, "error", "请先填写端点 URL。")
                return
            api_key = self.api_key_edit.text() or None
            model = self.model_edit.text().strip() or None
            language = self.language_combo.currentData() or self.language_combo.currentText()

            from . import models_catalog, remote_probe

            metadata = models_catalog.download_metadata()
            allow_http = bool(metadata.get("allow_http", False))
            use_system_proxy = bool(metadata.get("use_system_proxy", False))

            def _job():
                return remote_probe.test_endpoint(
                    url,
                    api_key=api_key,
                    model=model,
                    language=language,
                    allow_http=allow_http,
                    use_system_proxy=use_system_proxy,
                )

            self._tx.run(
                "正在测试端点连通性…",
                _job,
                on_success=self._on_test_done,
                on_error=self._on_test_error,
            )

        def _on_test_done(self, result: dict) -> None:
            latency = result.get("latency_ms")
            latency_text = f"{latency} ms" if latency is not None else "-"
            if result.get("ok"):
                text = f"端点连通正常（HTTP {result.get('status')}，用时 {latency_text}）。"
                if isinstance(latency, (int, float)) and latency > 1000:
                    text += "本次已超过 1 秒，不适合即时打字；九候选请求可能更慢。"
                    _update_strip(self.apply_strip, "warning", text)
                    QMessageBox.warning(self, "连通性测试", text)
                else:
                    _update_strip(self.apply_strip, "success", text)
                    QMessageBox.information(self, "连通性测试", text)
            else:
                error = result.get("error") or "未知错误"
                detail = (result.get("detail") or "").strip()
                _update_strip(
                    self.apply_strip,
                    "error",
                    f"端点测试失败：{error}（用时 {latency_text}）",
                )
                message = f"端点测试失败：{error}\n用时：{latency_text}"
                if detail:
                    message += f"\n\n响应摘要：\n{detail[:400]}"
                QMessageBox.warning(self, "连通性测试", message)

        def _on_test_error(self, message: str) -> None:
            _update_strip(self.apply_strip, "error", f"端点测试失败：{message}")
            QMessageBox.critical(self, "连通性测试", message)

        def _on_apply(self) -> None:
            backend = self._selected_backend()
            url = self.url_edit.text().strip()
            api_key = self.api_key_edit.text()
            model = self.model_edit.text().strip()
            language = self.language_combo.currentData() or self.language_combo.currentText()

            if backend == "remote" and (not url or not api_key or not model):
                _update_strip(self.apply_strip, "error", "外接 API 需要端点 URL、模型名与 API 密钥。")
                QMessageBox.warning(
                    self,
                    "参数不足",
                    "外接 API 需要端点 URL、模型名与 API 密钥。",
                )
                return

            summary = (
                f"后端：{backend_label(backend)}\n"
                f"端点：{url or '（不适用）'}\n"
                f"模型：{model or '（不适用）'}\n"
                f"目标语言：{language_label(language)}\n\n"
                f"{_RESTART_WARNING}\n"
                "并可能清除 AI 缓存。"
            )
            if (
                QMessageBox.question(
                    self,
                    "确认应用",
                    summary,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return

            try:
                config = appconfig.load_config()
                config.backend = backend
                config.remote_url = url
                config.remote_model = model
                config.remote_language = language
                config.language = language
                config.api_key_dpapi = (
                    appconfig.encrypt_secret(api_key) if api_key else None
                )
            except Exception as exc:
                _update_strip(self.apply_strip, "error", f"配置准备失败：{exc}")
                QMessageBox.critical(self, "配置准备失败", str(exc))
                return

            from . import server

            self._tx.run(
                "正在应用配置并重启输入法服务…",
                server.apply_backend,
                backend=backend,
                url=url or None,
                api_key=api_key or None,
                model=model or None,
                language=language or None,
                on_success=lambda result: self._on_apply_done(backend, config, result),
                on_error=self._on_apply_error,
            )

        def _on_apply_done(self, backend: str, config, result: dict) -> None:
            if result.get("ok"):
                try:
                    appconfig.save_config(config)
                except Exception as exc:
                    result["ok"] = False
                    result.setdefault("errors", []).append(
                        f"服务已切换，但持久配置保存失败：{exc}"
                    )
            self._on_backend_changed()
            self._refresh_status()
            if result.get("ok"):
                _update_strip(
                    self.apply_strip,
                    "success",
                    f"已应用：{backend_label(backend)}。环境已注入并校验通过。",
                )
            else:
                errors = "；".join(result.get("errors") or ["未知错误"])
                _update_strip(self.apply_strip, "error", f"应用未完成：{errors}")

            self._set_status(
                self.status_label.text()
                + "\n\n最近一次应用结果："
                + json.dumps(
                    {
                        "ok": result.get("ok"),
                        "env_verified": result.get("env_verified"),
                        "cache_cleared": result.get("cache_cleared"),
                        "errors": result.get("errors"),
                        "observed": result.get("observed"),
                    },
                    ensure_ascii=False,
                )
            )
            QMessageBox.information(
                self,
                "结果",
                ("应用成功。" if result.get("ok") else "应用未完成。")
                + "\n详细信息见「当前状态与详情」。",
            )

        def _on_apply_error(self, message: str) -> None:
            self._on_backend_changed()
            _update_strip(self.apply_strip, "error", f"应用失败：{message}")
            QMessageBox.critical(self, "应用失败", message)

    class AppearancePage(QWidget):
        """外观 page: candidate-window colour scheme, font size and layout.

        Effective values come from the installed ``weasel.yaml`` overlaid by
        ``weasel.custom.yaml``; 应用 writes a merged ``patch:`` and redeploys.
        """

        def __init__(self, title: str, description: str) -> None:
            super().__init__()
            self.setObjectName("PageRoot")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.addWidget(_page_header(title, description))

            card = Card()
            heading = QLabel("候选窗外观")
            heading.setObjectName("SectionHeading")
            card.body.addWidget(heading)
            card.body.addWidget(
                _caption_label(
                    "值读取自安装目录的 weasel.yaml，写入用户目录的 "
                    "weasel.custom.yaml（合并 patch），随后重新部署。"
                )
            )

            form = QFormLayout()
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.scheme_combo = QComboBox()
            try:
                schemes = rime_settings.available_color_schemes()
            except Exception as exc:  # pragma: no cover - defensive
                schemes = []
                self._scheme_error = str(exc)
            else:
                self._scheme_error = None
            for name in schemes:
                self.scheme_combo.addItem(name, name)

            self.font_spin = QSpinBox()
            self.font_spin.setRange(8, 48)
            self.font_spin.setSuffix(" pt")

            layout_row = QWidget()
            layout_row.setObjectName("CardInner")
            layout_layout = QHBoxLayout(layout_row)
            layout_layout.setContentsMargins(0, 0, 0, 0)
            layout_layout.setSpacing(16)
            self.horizontal_group = QButtonGroup(self)
            self.h_radio = QRadioButton("横排")
            self.v_radio = QRadioButton("竖排")
            self.horizontal_group.addButton(self.h_radio)
            self.horizontal_group.addButton(self.v_radio)
            layout_layout.addWidget(self.h_radio)
            layout_layout.addWidget(self.v_radio)
            layout_layout.addStretch(1)

            self.inline_check = QCheckBox("行内预编辑")
            self.inline_check.setToolTip(
                "inline_preedit：在候选窗内直接编辑编码（而非另开一行）。"
            )

            form.addRow("配色方案", self.scheme_combo)
            form.addRow("候选字号", self.font_spin)
            form.addRow("候选排列", layout_row)
            form.addRow("", self.inline_check)
            card.body.addLayout(form)

            actions = QHBoxLayout()
            self.refresh_button = QPushButton("刷新")
            self.refresh_button.clicked.connect(self._load)
            actions.addWidget(self.refresh_button)
            actions.addStretch(1)
            self.apply_button = QPushButton("应用")
            self.apply_button.setObjectName("PrimaryButton")
            self.apply_button.clicked.connect(self._on_apply)
            actions.addWidget(self.apply_button)
            card.body.addLayout(actions)

            self.strip = QLabel("")
            self.strip.setObjectName("StatusStrip")
            self.strip.setWordWrap(True)
            card.body.addWidget(self.strip)

            self.busy = BusyStrip()
            card.body.addWidget(self.busy)
            layout.addWidget(card)

            current_card = Card()
            current_heading = QLabel("当前生效值")
            current_heading.setObjectName("SectionHeading")
            current_card.body.addWidget(current_heading)
            self.current_label = QLabel("")
            self.current_label.setObjectName("CardValue")
            self.current_label.setWordWrap(True)
            self.current_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            current_card.body.addWidget(self.current_label)
            layout.addWidget(current_card)

            details = CollapsibleSection("详情（原始键 / 值）")
            self.detail_label = QLabel("")
            self.detail_label.setObjectName("DetailValue")
            self.detail_label.setWordWrap(True)
            self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details.addWidget(self.detail_label)
            layout.addWidget(details)
            layout.addStretch(1)

            self._load()

            self._tx = TransactionController(
                self.busy, self._interactive_controls, parent=self
            )

        # -- off-thread transaction support --

        def _interactive_controls(self):
            return [
                self.scheme_combo,
                self.font_spin,
                self.h_radio,
                self.v_radio,
                self.inline_check,
                self.refresh_button,
                self.apply_button,
            ]

        def transaction_active(self) -> bool:
            return self._tx.active

        # -- helpers --

        def _load(self) -> None:
            try:
                style = rime_settings.current_style()
            except Exception as exc:  # pragma: no cover - defensive
                _update_strip(self.strip, "error", f"外观配置不可用：{exc}")
                self.current_label.setText("外观配置不可用。")
                self.detail_label.setText("")
                return

            requested = style["requested"]

            def value(key, fallback=None):
                entry = requested.get(key) or {}
                return entry.get("value", fallback)

            scheme = value("style/color_scheme")
            index = self.scheme_combo.findData(scheme)
            if index < 0 and scheme is not None:
                self.scheme_combo.addItem(str(scheme), scheme)
                index = self.scheme_combo.count() - 1
            if index >= 0:
                self.scheme_combo.setCurrentIndex(index)

            font_point = value("style/font_point", 14)
            try:
                self.font_spin.setValue(int(font_point))
            except (TypeError, ValueError):
                self.font_spin.setValue(14)

            horizontal = bool(value("style/horizontal", False))
            self.h_radio.setChecked(horizontal)
            self.v_radio.setChecked(not horizontal)
            self.inline_check.setChecked(bool(value("style/inline_preedit", False)))

            self.current_label.setText(
                f"配色方案：{scheme or '（未知）'}　"
                f"字号：{value('style/font_point', '?')} pt　"
                f"排列：{'横排' if horizontal else '竖排'}　"
                f"行内预编辑：{'开' if self.inline_check.isChecked() else '关'}　"
                f"圆角：{value('style/corner_radius', '?')}　"
                f"注释字号：{value('style/comment_font_point', '?')} pt"
            )

            lines = [
                f"weasel.yaml：{style['weasel_yaml']}",
                f"自定义补丁：{style['custom_path']}"
                f"  [{'存在' if style['custom_exists'] else '不存在'}]",
                "",
                "已生效：",
            ]
            for key in sorted(style["values"]):
                lines.append(f"  {key} = {style['values'][key]}")
            if style["patch"]:
                lines.append("")
                lines.append("自定义 patch：")
                for key in sorted(style["patch"]):
                    lines.append(f"  {key} = {style['patch'][key]}")
            self.detail_label.setText("\n".join(lines))

            if self._scheme_error:
                _update_strip(self.strip, "warning", f"配色列表读取失败：{self._scheme_error}")
            else:
                _update_strip(self.strip, "neutral", "修改后点击「应用」写入并重新部署。")

        def _on_apply(self) -> None:
            patch = {
                "style/color_scheme": self.scheme_combo.currentData()
                or self.scheme_combo.currentText(),
                "style/font_point": self.font_spin.value(),
                "style/horizontal": self.h_radio.isChecked(),
                "style/inline_preedit": self.inline_check.isChecked(),
            }
            if (
                QMessageBox.question(
                    self,
                    "确认应用",
                    _RESTART_WARNING,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return

            self._tx.run(
                "正在应用外观并重新部署…",
                rime_settings.apply_style,
                patch=patch,
                on_success=self._on_apply_done,
                on_error=self._on_apply_error,
            )

        def _on_apply_done(self, result: dict) -> None:
            self._load()
            if result.get("changed"):
                if result.get("clean"):
                    _update_strip(self.strip, "success", "外观已应用并重新部署。")
                else:
                    deploy = result.get("deploy") or {}
                    _update_strip(
                        self.strip,
                        "warning",
                        "已写入，但部署可能未成功："
                        f"退出码 {deploy.get('exit_code')}，"
                        f"stderr：{deploy.get('stderr') or '（空）'}",
                    )
            else:
                _update_strip(self.strip, "neutral", "没有需要写入的更改。")

        def _on_apply_error(self, message: str) -> None:
            _update_strip(self.strip, "error", f"外观应用失败：{message}")
            QMessageBox.critical(self, "外观应用失败", message)

    class KeysPage(QWidget):
        """按键与开关 page: constrained Language Input switches + hotkeys."""

        def __init__(self, title: str, description: str) -> None:
            super().__init__()
            self.setObjectName("PageRoot")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.addWidget(_page_header(title, description))

            self._loading = False
            self._initial: dict[str, bool] = {}
            self._checkboxes: dict[str, QCheckBox] = {}
            self._radios: dict[str, QRadioButton] = {}

            switches_card = Card()
            heading = QLabel("语言输入开关（F4 菜单）")
            heading.setObjectName("SectionHeading")
            switches_card.body.addWidget(heading)
            switches_card.body.addWidget(
                _caption_label(
                    "「默认」来自当前编译方案中的 reset 值；未设置过的开关会显示默认值。"
                    "目标语言为单选：选择一种会清除其它两种。"
                )
            )

            self._switches_box = QVBoxLayout()
            self._switches_box.setSpacing(8)
            switches_card.body.addLayout(self._switches_box)

            actions = QHBoxLayout()
            self.refresh_button = QPushButton("刷新")
            self.refresh_button.clicked.connect(self._refresh)
            actions.addWidget(self.refresh_button)
            actions.addStretch(1)
            self.apply_button = QPushButton("应用")
            self.apply_button.setObjectName("PrimaryButton")
            self.apply_button.clicked.connect(self._on_apply)
            actions.addWidget(self.apply_button)
            switches_card.body.addLayout(actions)

            self.strip = QLabel("")
            self.strip.setObjectName("StatusStrip")
            self.strip.setWordWrap(True)
            switches_card.body.addWidget(self.strip)

            self.busy = BusyStrip()
            switches_card.body.addWidget(self.busy)
            layout.addWidget(switches_card)

            fuzzy_card = Card()
            fuzzy_heading = QLabel("小鹤双拼模糊音")
            fuzzy_heading.setObjectName("SectionHeading")
            fuzzy_card.body.addWidget(fuzzy_heading)
            fuzzy_card.body.addWidget(_caption_label(
                "读取搜狗本机 Fuzzy.dat 中已启用的模糊音，同步到小狼毫小鹤双拼。"
                "灰色候选规则不计入；全拼方案不受影响。"
            ))
            self.fuzzy_state = QLabel("")
            self.fuzzy_state.setObjectName("DetailValue")
            self.fuzzy_state.setWordWrap(True)
            fuzzy_card.body.addWidget(self.fuzzy_state)
            fuzzy_actions = QHBoxLayout()
            self.fuzzy_refresh = QPushButton("刷新搜狗设置")
            self.fuzzy_refresh.clicked.connect(self._refresh_fuzzy)
            fuzzy_actions.addWidget(self.fuzzy_refresh)
            fuzzy_actions.addStretch(1)
            self.fuzzy_apply = QPushButton("同步到小狼毫")
            self.fuzzy_apply.setObjectName("PrimaryButton")
            self.fuzzy_apply.clicked.connect(self._on_sync_fuzzy)
            fuzzy_actions.addWidget(self.fuzzy_apply)
            fuzzy_card.body.addLayout(fuzzy_actions)
            self.fuzzy_strip = QLabel("")
            self.fuzzy_strip.setObjectName("StatusStrip")
            self.fuzzy_strip.setWordWrap(True)
            fuzzy_card.body.addWidget(self.fuzzy_strip)
            self.fuzzy_busy = BusyStrip()
            fuzzy_card.body.addWidget(self.fuzzy_busy)
            layout.addWidget(fuzzy_card)

            hotkey_card = Card()
            hotkey_heading = QLabel("按键速查（只读）")
            hotkey_heading.setObjectName("SectionHeading")
            hotkey_card.body.addWidget(hotkey_heading)
            hotkey_card.body.addWidget(
                _caption_label("读取自已部署的 default.yaml / key_bindings.yaml。")
            )
            self.hotkey_table = QTableWidget()
            self.hotkey_table.setObjectName("HotkeyTable")
            self.hotkey_table.setColumnCount(4)
            self.hotkey_table.setHorizontalHeaderLabels(
                ["分组", "按键", "动作", "原始配置"]
            )
            self.hotkey_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.hotkey_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.hotkey_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.hotkey_table.setShowGrid(False)
            self.hotkey_table.verticalHeader().setVisible(False)
            self.hotkey_table.verticalHeader().setDefaultSectionSize(32)
            self.hotkey_table.setMinimumHeight(200)
            header = self.hotkey_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.Stretch)
            hotkey_card.body.addWidget(self.hotkey_table)
            layout.addWidget(hotkey_card)

            advanced = CollapsibleSection("高级：选项持久化（去除方案 reset）")
            advanced.addWidget(
                _caption_label(
                    "移除方案开关的显式重置值，使所选后端 / 语言在重开会话后仍能保持。"
                )
            )
            reset_row = QHBoxLayout()
            reset_row.setSpacing(8)
            reset_row.addWidget(QLabel("方案"))
            self.schema_combo = QComboBox()
            for schema_id in rime_settings.LANGUAGE_SCHEMAS:
                self.schema_combo.addItem(
                    SCHEMA_LABELS.get(schema_id, schema_id), schema_id
                )
            # The combo is the single refresh trigger for this read-only view:
            # switching schema re-reads its reset state, so a dedicated 刷新
            # button would duplicate exactly the same action.
            self.schema_combo.currentIndexChanged.connect(self._refresh_reset)
            reset_row.addWidget(self.schema_combo)
            self.neutralize_button = QPushButton("使选项可保持")
            self.neutralize_button.clicked.connect(self._on_neutralize)
            reset_row.addWidget(self.neutralize_button)
            self.revert_button = QPushButton("还原重置补丁")
            self.revert_button.clicked.connect(self._on_revert)
            reset_row.addWidget(self.revert_button)
            reset_row.addStretch(1)
            advanced.addLayout(reset_row)
            self.reset_label = QLabel("")
            self.reset_label.setWordWrap(True)
            advanced.addWidget(self.reset_label)
            self.reset_detail_label = QLabel("")
            self.reset_detail_label.setObjectName("DetailValue")
            self.reset_detail_label.setWordWrap(True)
            self.reset_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            advanced.addWidget(self.reset_detail_label)
            layout.addWidget(advanced)
            layout.addStretch(1)

            self._refresh()
            self._refresh_reset()

            self._tx = TransactionController(
                self.busy, self._interactive_controls, parent=self
            )
            self._refresh_fuzzy()

        # -- off-thread transaction support --

        def _interactive_controls(self):
            controls = [self.refresh_button, self.apply_button, self.schema_combo,
                        self.neutralize_button, self.revert_button,
                        self.fuzzy_refresh, self.fuzzy_apply]
            controls.extend(self._checkboxes.values())
            controls.extend(self._radios.values())
            return controls

        def transaction_active(self) -> bool:
            return self._tx.active

        # -- helpers --

        def _refresh_fuzzy(self) -> None:
            from . import fuzzy_pinyin

            try:
                state = fuzzy_pinyin.preview_sogou_fuzzy()
            except Exception as exc:  # pragma: no cover - malformed local config
                self.fuzzy_state.setText(str(exc))
                self.fuzzy_apply.setEnabled(False)
                _update_strip(self.fuzzy_strip, "warning", "无法读取可同步的搜狗模糊音。")
                return
            pairs = "、".join(f"{left}/{right}" for left, right in state["pairs"])
            self.fuzzy_state.setText("搜狗已启用：" + (pairs or "无"))
            self.fuzzy_apply.setEnabled(bool(state["pairs"]) or not state["synced"])
            _update_strip(
                self.fuzzy_strip,
                "success" if state["synced"] else "neutral",
                "小狼毫已同步。" if state["synced"] else "点击「同步到小狼毫」后重新部署生效。",
            )

        def _on_sync_fuzzy(self) -> None:
            from . import fuzzy_pinyin

            self._tx.run(
                "正在同步模糊音并重新部署…",
                fuzzy_pinyin.sync_sogou_fuzzy,
                busy=self.fuzzy_busy,
                on_success=self._on_sync_fuzzy_done,
                on_error=self._on_sync_fuzzy_error,
            )

        def _on_sync_fuzzy_done(self, result: dict) -> None:
            self._refresh_fuzzy()
            if result.get("clean"):
                _update_strip(self.fuzzy_strip, "success", "搜狗模糊音已同步并重新部署。")
            else:
                deployed = result.get("deploy") or {}
                service = result.get("server") or {}
                _update_strip(
                    self.fuzzy_strip,
                    "warning",
                    "规则已保存，但部署未验证成功："
                    + (service.get("restart_error") or deployed.get("stderr")
                       or deployed.get("note") or "请检查部署日志。"),
                )

        def _on_sync_fuzzy_error(self, message: str) -> None:
            _update_strip(self.fuzzy_strip, "error", f"模糊音同步失败：{message}")

        def _clear_switches(self) -> None:
            while self._switches_box.count():
                item = self._switches_box.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                child = item.layout()
                if child is not None:
                    child.deleteLater()
            self._checkboxes.clear()
            self._radios.clear()

        def _refresh(self) -> None:
            self._loading = True
            self._clear_switches()
            try:
                rows = rime_settings.language_input_switches()
            except Exception as exc:  # pragma: no cover - defensive
                _update_strip(self.strip, "error", f"开关状态不可用：{exc}")
                self._loading = False
                return

            language_rows = [row for row in rows if row["group"] == "language"]
            for row in rows:
                if row["group"] == "language":
                    continue
                line = QWidget()
                line.setObjectName("CardInner")
                line_layout = QHBoxLayout(line)
                line_layout.setContentsMargins(0, 0, 0, 0)
                line_layout.setSpacing(8)
                check = QCheckBox(row["label"])
                check.setChecked(bool(row["value"]))
                check.setToolTip("、".join(row["states"]) or row["name"])
                self._checkboxes[row["name"]] = check
                line_layout.addWidget(check)
                line_layout.addWidget(
                    StateChip(
                        "已保存" if row["saved"] is not None else "默认",
                        "success" if row["saved"] is not None else "neutral",
                    )
                )
                line_layout.addWidget(_caption_label(row["default_label"]))
                line_layout.addStretch(1)
                self._switches_box.addWidget(line)

            if language_rows:
                group_line = QWidget()
                group_line.setObjectName("CardInner")
                group_layout = QHBoxLayout(group_line)
                group_layout.setContentsMargins(0, 0, 0, 0)
                group_layout.setSpacing(12)
                label = QLabel("目标语言")
                label.setObjectName("DetailLabel")
                group_layout.addWidget(label)
                button_group = QButtonGroup(group_line)
                for row in language_rows:
                    radio = QRadioButton(row["label"].split("：")[-1])
                    radio.setChecked(bool(row["value"]))
                    radio.setToolTip(row["default_label"])
                    button_group.addButton(radio)
                    self._radios[row["name"]] = radio
                    group_layout.addWidget(radio)
                default_languages = "、".join(
                    row["label"].split("：")[-1]
                    for row in language_rows
                    if row["default"]
                )
                group_layout.addWidget(
                    _caption_label(
                        f"默认：{default_languages}" if default_languages else "默认：未知"
                    )
                )
                group_layout.addStretch(1)
                self._switches_box.addWidget(group_line)

            self._initial = {
                name: check.isChecked() for name, check in self._checkboxes.items()
            }
            self._initial.update(
                {name: radio.isChecked() for name, radio in self._radios.items()}
            )
            for radio in self._radios.values():
                radio.toggled.connect(self._sync_ai_requirement)
            self._sync_ai_requirement()
            _update_strip(self.strip, "neutral", "修改后点击「应用」写入并重新部署。")
            self._populate_hotkeys()
            self._loading = False

        def _sync_ai_requirement(self, *_args) -> None:
            ai_check = self._checkboxes.get("language_input_ai")
            if ai_check is None:
                return
            requires_ai = any(
                self._radios.get(name) is not None
                and self._radios[name].isChecked()
                for name in ("language_input_ja", "language_input_es")
            )
            if requires_ai:
                ai_check.setChecked(True)
                ai_check.setToolTip("日语和西班牙语需要 AI 翻译；切回英语后可关闭。")
            else:
                ai_check.setToolTip("英语可在词典与 AI 翻译之间选择。")
            ai_check.setText("AI 翻译（日/西必需）" if requires_ai else "AI 翻译")
            ai_check.setEnabled(not requires_ai)

        def _populate_hotkeys(self) -> None:
            try:
                rows = rime_settings.hotkeys()
            except Exception:  # pragma: no cover - defensive
                rows = []
            self.hotkey_table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                for column, key in enumerate(("group", "keys", "action", "raw")):
                    item = QTableWidgetItem(str(row.get(key, "")))
                    if key == "raw":
                        item.setToolTip(str(row.get(key, "")))
                    self.hotkey_table.setItem(index, column, item)

        def _current_values(self) -> dict[str, bool]:
            values = {name: check.isChecked() for name, check in self._checkboxes.items()}
            values.update({name: radio.isChecked() for name, radio in self._radios.items()})
            return values

        def _on_apply(self) -> None:
            current = self._current_values()
            if any(current.get(name) for name in rime_settings.LANGUAGE_GROUP):
                for name in rime_settings.LANGUAGE_GROUP:
                    current.setdefault(name, False)
            changes = {
                name: value
                for name, value in current.items()
                if self._initial.get(name) != value
            }
            if not changes:
                _update_strip(self.strip, "neutral", "没有需要写入的更改。")
                return

            if (
                QMessageBox.question(
                    self,
                    "确认应用",
                    _RESTART_WARNING,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return

            self._tx.run(
                "正在应用开关并重新部署…",
                rime_settings.set_switches,
                changes=changes,
                on_success=self._on_apply_done,
                on_error=self._on_apply_error,
            )

        def _on_apply_done(self, result: dict) -> None:
            self._refresh()
            if result.get("changed"):
                if result.get("clean"):
                    _update_strip(self.strip, "success", "开关已应用并重新部署。")
                else:
                    deploy = result.get("deploy") or {}
                    _update_strip(
                        self.strip,
                        "warning",
                        "已写入，但部署可能未成功："
                        f"退出码 {deploy.get('exit_code')}，"
                        f"stderr：{deploy.get('stderr') or '（空）'}",
                    )
            else:
                _update_strip(self.strip, "neutral", "没有需要写入的更改。")

        def _on_apply_error(self, message: str) -> None:
            _update_strip(self.strip, "error", f"开关应用失败：{message}")
            QMessageBox.critical(self, "开关应用失败", message)

        def _refresh_reset(self) -> None:
            from . import schema_patch

            schema_id = self.schema_combo.currentData() or self.schema_combo.currentText()
            display = SCHEMA_LABELS.get(schema_id, schema_id)
            try:
                state = schema_patch.compiled_reset_state(schema_id)
                custom = schema_patch.custom_schema_path(schema_id)
                rendered = "、".join(
                    f"{switch_label(name)}（{'会被重置' if value else '可保持'}）"
                    for name, value in state.items()
                )
                self.reset_label.setText(
                    f"{display}：{rendered or '（无 Language Input 开关）'}"
                )
                self.reset_detail_label.setText(
                    f"方案标识：{schema_id}\n"
                    f"自定义补丁：{custom}  [{'存在' if custom.is_file() else '不存在'}]\n"
                    "原始开关：" + "、".join(state.keys() or ["（无）"])
                )
            except Exception as exc:
                self.reset_label.setText(f"{display}：暂不可用（需要先部署一次）。")
                self.reset_detail_label.setText(f"方案标识：{schema_id}\n错误：{exc}")

        def _on_neutralize(self) -> None:
            schema_id = self.schema_combo.currentData() or self.schema_combo.currentText()
            display = SCHEMA_LABELS.get(schema_id, schema_id)
            if (
                QMessageBox.question(
                    self,
                    "确认",
                    f"将写入 {display} 的自定义补丁并重新部署，移除 Language Input "
                    "已保存开关的显式重置，使所选选项可在重开会话后保持；"
                    "未保存的开关继续使用方案默认值。\n\n"
                    f"{_RESTART_WARNING}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return

            def _job(*, schema_id: str) -> dict:
                from . import deploy, schema_patch

                path = schema_patch.neutralize_resets(schema_id)
                deployer = paths.weasel_deployer_exe(paths.weasel_root())
                result = deploy.run_deploy(deployer, timeout=120)
                return {
                    "path": str(path),
                    "exit_code": result.exit_code,
                    "busy": result.busy,
                    "stderr": result.stderr,
                    "timed_out": result.timed_out,
                }

            self._tx.run(
                "正在写入补丁并重新部署…",
                _job,
                schema_id=schema_id,
                on_success=self._on_neutralize_done,
                on_error=lambda message: self._on_neutralize_error(display, message),
            )

        def _on_neutralize_done(self, result: dict) -> None:
            self._refresh_reset()
            self._refresh()
            QMessageBox.information(
                self,
                "结果",
                f"自定义补丁：{Path(result['path']).is_file() and '已写入' or '未写入'}\n"
                f"部署退出码：{result.get('exit_code')}  忙：{result.get('busy')}\n"
                f"错误输出：{result.get('stderr') or '（空）'}",
            )

        def _on_neutralize_error(self, display: str, message: str) -> None:
            QMessageBox.critical(self, "失败", f"{display}：{message}")

        def _on_revert(self) -> None:
            """GUI 回退：remove our reset patch (previously CLI-only)."""
            schema_id = self.schema_combo.currentData() or self.schema_combo.currentText()
            display = SCHEMA_LABELS.get(schema_id, schema_id)
            if (
                QMessageBox.question(
                    self,
                    "确认还原",
                    f"将移除 {display} 中本应用写入的开关重置补丁，"
                    "恢复方案自带的 reset 行为（重开会话后所选选项会被重置），"
                    "并重新部署。\n\n"
                    f"{_RESTART_WARNING}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return

            def _job(*, schema_id: str) -> dict:
                from . import deploy, schema_patch

                path = schema_patch.revert_resets(schema_id)
                deployer = paths.weasel_deployer_exe(paths.weasel_root())
                result = deploy.run_deploy(deployer, timeout=120)
                return {
                    "path": str(path) if path else None,
                    "exit_code": result.exit_code,
                    "busy": result.busy,
                    "stderr": result.stderr,
                    "timed_out": result.timed_out,
                    # /deploy returns 0 even for invalid YAML: clean requires
                    # an empty stderr (design doc §5.6).
                    "clean": bool(result.ran)
                    and result.exit_code == 0
                    and not (result.stderr or "").strip(),
                }

            self._tx.run(
                "正在移除补丁并重新部署…",
                _job,
                schema_id=schema_id,
                on_success=self._on_revert_done,
                on_error=lambda message: self._on_revert_error(display, message),
            )

        def _on_revert_done(self, result: dict) -> None:
            self._refresh_reset()
            self._refresh()
            if result.get("clean"):
                _update_strip(self.strip, "success", "已还原方案重置补丁并重新部署。")
                QMessageBox.information(self, "结果", "重置补丁已还原，已重新部署。")
            else:
                _update_strip(
                    self.strip,
                    "warning" if result.get("busy") else "error",
                    "还原可能未完成："
                    f"退出码 {result.get('exit_code')}，"
                    f"stderr：{result.get('stderr') or '（空）'}",
                )

        def _on_revert_error(self, display: str, message: str) -> None:
            QMessageBox.critical(self, "失败", f"{display}：{message}")

    class DictionaryPage(QWidget):
        """词库与记忆 page: learning, local import, sync, dictionary manager."""

        def __init__(self, title: str, description: str) -> None:
            super().__init__()
            self.setObjectName("PageRoot")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.addWidget(_page_header(title, description))

            learning_card = Card()
            heading = QLabel("用户词典学习")
            heading.setObjectName("SectionHeading")
            learning_card.body.addWidget(heading)
            warning = _caption_label(
                "关闭后会完全停用用户词典：不仅停止学习新词，也会停止使用已学词条"
                "（已学词不再参与候选排序）。"
            )
            warning.setProperty("state", "warning")
            # Dynamic-property selectors are only re-evaluated on polish, so
            # re-polish explicitly to pick up QLabel#Caption[state="warning"].
            warning_style = warning.style()
            warning_style.unpolish(warning)
            warning_style.polish(warning)
            learning_card.body.addWidget(warning)

            self.learning_check = QCheckBox("启用用户词典（关闭会同时停用学习与已学词条）")
            self.learning_check.setToolTip(
                "写入 translator/enable_user_dict；关闭不仅停止学习新词，"
                "也会停止使用已学词条（已学词不再参与候选排序）。"
            )
            learning_card.body.addWidget(self.learning_check)

            self.state_label = QLabel("")
            self.state_label.setObjectName("DetailValue")
            self.state_label.setWordWrap(True)
            self.state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            learning_card.body.addWidget(self.state_label)

            learning_actions = QHBoxLayout()
            self.learning_refresh = QPushButton("刷新")
            self.learning_refresh.clicked.connect(self._refresh)
            learning_actions.addWidget(self.learning_refresh)
            learning_actions.addStretch(1)
            self.learning_apply = QPushButton("应用")
            self.learning_apply.setObjectName("PrimaryButton")
            self.learning_apply.clicked.connect(self._on_apply_learning)
            learning_actions.addWidget(self.learning_apply)
            learning_card.body.addLayout(learning_actions)

            self.learning_strip = QLabel("")
            self.learning_strip.setObjectName("StatusStrip")
            self.learning_strip.setWordWrap(True)
            learning_card.body.addWidget(self.learning_strip)

            self.learning_busy = BusyStrip()
            learning_card.body.addWidget(self.learning_busy)
            layout.addWidget(learning_card)

            import_card = Card()
            import_heading = QLabel("导入其他输入法词库")
            import_heading.setObjectName("SectionHeading")
            import_card.body.addWidget(import_heading)
            import_card.body.addWidget(
                _caption_label(
                    "可直接选择检测到的搜狗或微软拼音本机词库；也可选择从搜狗词库网站"
                    "下载的 .scel 文件，或导出的 TXT/CSV。词条仅在本机读取，"
                    "加入独立扩展词库，不覆盖原输入法数据。"
                )
            )
            site_link = QLabel('<a href="https://pinyin.sogou.com/dict/">浏览搜狗细胞词库网站</a>')
            site_link.setOpenExternalLinks(True)
            site_link.setAccessibleName("浏览搜狗细胞词库网站")
            import_card.body.addWidget(site_link)
            self.import_source = None
            self.import_state_label = QLabel("请选择导入来源。")
            self.import_state_label.setObjectName("DetailValue")
            self.import_state_label.setWordWrap(True)
            import_card.body.addWidget(self.import_state_label)
            self.import_source_combo = QComboBox()
            self.import_source_combo.setAccessibleName("其他输入法词库来源")
            self.import_source_combo.currentIndexChanged.connect(self._on_import_source_changed)
            import_card.body.addWidget(self.import_source_combo)
            import_row = QHBoxLayout()
            self.import_scan = QPushButton("重新扫描本机")
            self.import_scan.clicked.connect(self._scan_import_sources)
            import_row.addWidget(self.import_scan)
            self.import_browse = QPushButton("选择词库文件…")
            self.import_browse.clicked.connect(self._on_choose_export)
            import_row.addWidget(self.import_browse)
            import_row.addStretch(1)
            self.import_apply = QPushButton("导入并部署")
            self.import_apply.setObjectName("PrimaryButton")
            self.import_apply.setEnabled(False)
            self.import_apply.clicked.connect(self._on_import_export)
            import_row.addWidget(self.import_apply)
            import_card.body.addLayout(import_row)
            self.import_strip = QLabel("")
            self.import_strip.setObjectName("StatusStrip")
            self.import_strip.setWordWrap(True)
            import_card.body.addWidget(self.import_strip)
            self.import_busy = BusyStrip()
            import_card.body.addWidget(self.import_busy)
            layout.addWidget(import_card)
            _update_strip(self.import_strip, "neutral", "选择来源后会先检查可导入的词条数量。")

            actions_card = Card()
            actions_heading = QLabel("维护")
            actions_heading.setObjectName("SectionHeading")
            actions_card.body.addWidget(actions_heading)
            actions_card.body.addWidget(
                _caption_label(
                    "「同步用户数据」会运行 WeaselDeployer.exe /sync；"
                    "「打开词典管理」会打开一个模态对话框，本窗口仍可继续操作。"
                )
            )
            sync_row = QHBoxLayout()
            self.sync_button = QPushButton("同步用户数据")
            self.sync_button.clicked.connect(self._on_sync)
            sync_row.addWidget(self.sync_button)
            self.dict_button = QPushButton("打开词典管理")
            self.dict_button.clicked.connect(self._on_open_dict_manager)
            sync_row.addWidget(self.dict_button)
            sync_row.addStretch(1)
            actions_card.body.addLayout(sync_row)
            self.action_strip = QLabel("")
            self.action_strip.setObjectName("StatusStrip")
            self.action_strip.setWordWrap(True)
            actions_card.body.addWidget(self.action_strip)

            self.action_busy = BusyStrip()
            actions_card.body.addWidget(self.action_busy)
            self.note_label = _caption_label(rime_settings.dict_manager_note())
            actions_card.body.addWidget(self.note_label)
            layout.addWidget(actions_card)

            details = CollapsibleSection("详情（路径与技术信息）")
            self.detail_label = QLabel("")
            self.detail_label.setObjectName("DetailValue")
            self.detail_label.setWordWrap(True)
            self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details.addWidget(self.detail_label)
            layout.addWidget(details)
            layout.addStretch(1)

            _update_strip(self.action_strip, "neutral", "尚未执行同步或打开词典管理。")
            self._refresh()

            self._tx = TransactionController(
                self.learning_busy, self._interactive_controls, parent=self
            )
            self._scan_import_sources()

        # -- off-thread transaction support --

        def _interactive_controls(self):
            return [
                self.learning_check,
                self.learning_refresh,
                self.learning_apply,
                self.sync_button,
                self.dict_button,
                self.import_browse,
                self.import_scan,
                self.import_source_combo,
                self.import_apply,
            ]

        def transaction_active(self) -> bool:
            return self._tx.active

        # -- helpers --

        def _refresh(self) -> None:
            try:
                state = rime_settings.learning_state()
            except Exception as exc:  # pragma: no cover - defensive
                _update_strip(self.learning_strip, "error", f"学习状态不可用：{exc}")
                self.state_label.setText("")
                self.detail_label.setText("")
                return

            self.learning_check.setChecked(bool(state["enabled"]))
            schemas = state["schemas"]
            per_schema = "、".join(
                f"{SCHEMA_LABELS.get(schema_id, schema_id)}："
                f"{'开启' if info['enable_user_dict'] else '关闭'}"
                for schema_id, info in schemas.items()
            )
            size_text = format_size(state["userdb_size"]) if state["userdb_present"] else "无"
            self.state_label.setText(
                f"用户词典：{'存在' if state['userdb_present'] else '未检测到'}"
                f"（{size_text}）\n"
                f"有效学习状态：{'开启' if state['enabled'] else '关闭'}（{per_schema}）"
            )

            lines = [
                f"用户目录：{state['user_dir']}",
                f"用户词典：{state['userdb_path']}"
                f"  [{'存在' if state['userdb_present'] else '不存在'}]"
                f"  大小 {state['userdb_size']} 字节",
            ]
            for schema_id, info in schemas.items():
                lines.append(
                    f"{schema_id}：enable_user_dict="
                    f"{info['enable_user_dict']}  "
                    f"显式={info['explicit']}  "
                    f"自定义补丁={info['custom_path']}"
                    f" [{'存在' if info['custom_exists'] else '不存在'}]"
                    f" 覆盖={info['custom_has_override']}"
                )
            self.detail_label.setText("\n".join(lines))
            _update_strip(
                self.learning_strip,
                "neutral",
                "修改后点击「应用」写入方案补丁并重新部署。",
            )

        def _on_apply_learning(self) -> None:
            on = self.learning_check.isChecked()
            if on:
                semantics = (
                    "开启用户词典学习：已学词条恢复参与候选排序，并继续学习新词。"
                )
            else:
                semantics = (
                    "关闭用户词典会完全停用用户词典：不仅停止学习新词，"
                    "也会停止使用已学词条（已学词不再参与候选排序）。"
                )
            if (
                QMessageBox.question(
                    self,
                    "确认应用",
                    f"{semantics}\n\n{_RESTART_WARNING}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return

            self._tx.run(
                "正在应用学习设置并重新部署…",
                rime_settings.set_learning,
                on=on,
                on_success=lambda result: self._on_learning_done(on, result),
                on_error=self._on_learning_error,
            )

        def _on_learning_done(self, on: bool, result: dict) -> None:
            self._refresh()
            if not result:
                return
            if result.get("clean"):
                _update_strip(
                    self.learning_strip,
                    "success",
                    "用户词典学习已" + ("开启" if on else "完全停用") + "并重新部署。",
                )
            else:
                deploy = result.get("deploy") or {}
                _update_strip(
                    self.learning_strip,
                    "warning",
                    "已写入，但部署可能未成功："
                    f"退出码 {deploy.get('exit_code')}，"
                    f"stderr：{deploy.get('stderr') or '（空）'}",
                )

        def _on_learning_error(self, message: str) -> None:
            self._refresh()
            _update_strip(self.learning_strip, "error", f"学习设置失败：{message}")
            QMessageBox.critical(self, "学习设置失败", message)

        def _scan_import_sources(self) -> None:
            from . import dictionary_import

            self.import_source_combo.blockSignals(True)
            self.import_source_combo.clear()
            self.import_source_combo.addItem("选择本机输入法词库…", None)
            for item in dictionary_import.discover_local_sources():
                self.import_source_combo.addItem(item.label, item.path)
                self.import_source_combo.setItemData(self.import_source_combo.count() - 1, item.detail, Qt.ToolTipRole)
            self.import_source_combo.blockSignals(False)
            self.import_source = None
            self.import_apply.setEnabled(False)
            self.import_state_label.setText("请选择检测到的来源，或点击「选择词库文件…」。")
            count = self.import_source_combo.count() - 1
            _update_strip(self.import_strip, "neutral", f"检测到 {count} 类可读取的本机词库。")

        def _on_import_source_changed(self, _index: int) -> None:
            source = self.import_source_combo.currentData()
            if source:
                self._preview_import_source(source)
            else:
                self.import_source = None
                self.import_apply.setEnabled(False)

        def _on_choose_export(self) -> None:
            source, _filter = QFileDialog.getOpenFileName(
                self,
                "选择输入法词库",
                "",
                "可导入词库 (*.scel *.qcel *.txt *.csv *.bin *.dat);;所有文件 (*)",
            )
            if not source:
                return
            self.import_source_combo.blockSignals(True)
            self.import_source_combo.setCurrentIndex(0)
            self.import_source_combo.blockSignals(False)
            self._preview_import_source(source)

        def _preview_import_source(self, source: str) -> None:
            from . import dictionary_import

            self.import_source = None
            self.import_apply.setEnabled(False)
            self.import_state_label.setText("正在检查文件格式与词条数量…")
            self._tx.run(
                "正在检查词库…",
                dictionary_import.preview_export,
                source=source,
                busy=self.import_busy,
                on_success=self._on_preview_done,
                on_error=self._on_preview_error,
            )

        def _on_preview_done(self, preview) -> None:
            self.import_source = preview.source
            self.import_apply.setEnabled(True)
            self.import_state_label.setText(
                f"可导入 {preview.entries} 条（{preview.files} 个文件）；"
                f"跳过 {preview.skipped} 条，自动注音 {preview.generated_readings} 条。"
            )
            ignored = f"另有 {preview.ignored_files} 个格式不同的文件未读取。" if preview.ignored_files else ""
            _update_strip(
                self.import_strip,
                "neutral",
                f"格式：{preview.encoding}。{ignored}确认数量后点击「导入并部署」。",
            )

        def _on_preview_error(self, message: str) -> None:
            self.import_source = None
            self.import_apply.setEnabled(False)
            self.import_state_label.setText("文件无法作为词库导入。")
            _update_strip(self.import_strip, "error", message)

        def _on_import_export(self) -> None:
            from . import dictionary_import

            if not self.import_source:
                return
            if (
                QMessageBox.question(
                    self,
                    "确认导入",
                    "将识别到的词条加入独立扩展词库，并重新部署输入法。\n\n"
                    + _RESTART_WARNING,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
            self._tx.run(
                "正在导入词库并重新部署…",
                dictionary_import.import_export,
                source=self.import_source,
                busy=self.import_busy,
                on_success=self._on_import_done,
                on_error=self._on_import_error,
            )

        def _on_import_done(self, result: dict) -> None:
            self._refresh()
            summary = f"新词 {result['added']} 条，扩展词库共 {result['total']} 条。"
            if result.get("clean"):
                _update_strip(self.import_strip, "success", "导入并部署完成；" + summary)
            else:
                deploy = result.get("deploy") or {}
                stderr = deploy.get("stderr") or ""
                rejected = stderr.count("invalid syllable")
                if rejected:
                    detail = f"Rime 拒绝了 {rejected} 条拼音编码。"
                elif stderr.strip():
                    detail = "部署日志：" + stderr.strip().splitlines()[0][-180:]
                elif deploy.get("clean"):
                    detail = "部署命令已完成，但未检测到编译后的扩展词库。"
                else:
                    detail = deploy.get("note") or "请检查部署日志。"
                _update_strip(
                    self.import_strip,
                    "warning",
                    "已保存词库，但部署未验证成功；"
                    + summary
                    + " " + detail,
                )

        def _on_import_error(self, message: str) -> None:
            _update_strip(self.import_strip, "error", f"导入未完成：{message}")

        def _on_sync(self) -> None:
            self._tx.run(
                "正在同步用户数据…",
                rime_settings.sync_user_data,
                busy=self.action_busy,
                on_success=self._on_sync_done,
                on_error=self._on_sync_error,
            )

        def _on_sync_done(self, result: dict) -> None:
            if result.get("clean"):
                _update_strip(self.action_strip, "success", result.get("note", "已同步。"))
            elif result.get("busy"):
                _update_strip(self.action_strip, "warning", result.get("note", "部署器忙。"))
            else:
                _update_strip(
                    self.action_strip,
                    "error",
                    result.get("note", "同步未完成。"),
                )

        def _on_sync_error(self, message: str) -> None:
            _update_strip(self.action_strip, "error", f"同步未完成：{message}")

        def _on_open_dict_manager(self) -> None:
            try:
                result = rime_settings.open_dict_manager()
            except Exception as exc:  # pragma: no cover - defensive
                result = {"launched": False, "note": str(exc)}
            if result.get("launched"):
                _update_strip(
                    self.action_strip,
                    "success",
                    "已打开词典管理；" + rime_settings.dict_manager_note(),
                )
            else:
                _update_strip(
                    self.action_strip,
                    "error",
                    result.get("note", "无法打开词典管理。"),
                )

    def _info_page(
        title: str, description: str, sections: list[tuple[str, str]]
    ) -> "QWidget":
        """A polished informational page built from cards."""
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(_page_header(title, description))
        for heading, body in sections:
            card = Card()
            head = QLabel(heading)
            head.setObjectName("SectionHeading")
            card.body.addWidget(head)
            text = QLabel(body)
            text.setObjectName("PageDescription")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card.body.addWidget(text)
            layout.addWidget(card)
        layout.addStretch(1)
        return page

    class AboutPage(QWidget):
        """关于 page: version info, design doc entry and 重新部署."""

        def __init__(self, title: str, description: str) -> None:
            super().__init__()
            self.setObjectName("PageRoot")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.addWidget(_page_header(title, description))

            version_card = Card()
            head = QLabel("版本")
            head.setObjectName("SectionHeading")
            version_card.body.addWidget(head)
            value = QLabel(f"Language Input 设置  v{__version__}")
            value.setObjectName("CardValue")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            version_card.body.addWidget(value)
            qt_version = "未知"
            try:
                from PySide6 import QtCore

                qt_version = QtCore.qVersion()
            except Exception:  # pragma: no cover - defensive
                pass
            version_card.body.addWidget(
                _caption_label(
                    f"Python {platform.python_version()} · Qt {qt_version}"
                )
            )
            layout.addWidget(version_card)

            docs_card = Card()
            docs_head = QLabel("设计文档")
            docs_head.setObjectName("SectionHeading")
            docs_card.body.addWidget(docs_head)
            docs_value = QLabel("Language Input 设置应用设计文档")
            docs_value.setObjectName("CardValue")
            docs_card.body.addWidget(docs_value)
            docs_path = (
                _project_root()
                / "docs"
                / "2026-09-20-language-input-settings-app-design.md"
            )
            hint = _caption_label("设计与验证计划的权威来源。")
            hint.setToolTip(str(docs_path))
            docs_card.body.addWidget(hint)
            docs_button = QPushButton("打开设计文档")
            docs_button.clicked.connect(lambda: self._open_path(docs_path))
            docs_card.body.addWidget(docs_button)
            layout.addWidget(docs_card)

            action_card = Card()
            action_head = QLabel("维护")
            action_head.setObjectName("SectionHeading")
            action_card.body.addWidget(action_head)
            action_card.body.addWidget(
                _caption_label("重新部署会重建 Rime / Weasel 配置（可能短暂中断输入）。")
            )
            self.strip = QLabel("")
            self.strip.setObjectName("StatusStrip")
            self.strip.setWordWrap(True)
            action_card.body.addWidget(self.strip)

            self.busy = BusyStrip()
            action_card.body.addWidget(self.busy)
            row = QHBoxLayout()
            row.setSpacing(8)
            self.redeploy_button = QPushButton("重新部署")
            self.redeploy_button.setObjectName("PrimaryButton")
            self.redeploy_button.clicked.connect(self._on_redeploy)
            row.addWidget(self.redeploy_button)
            row.addStretch(1)
            open_data = QPushButton("打开用户数据目录")
            open_data.clicked.connect(
                lambda: self._open_path(paths.rime_user_dir())
            )
            row.addWidget(open_data)
            self.open_data_button = open_data
            open_appconfig = QPushButton("打开应用配置目录")
            open_appconfig.setToolTip("打开本应用自己的配置目录（config.json 所在处）。")
            open_appconfig.clicked.connect(self._open_appconfig_dir)
            row.addWidget(open_appconfig)
            self.open_appconfig_button = open_appconfig
            action_card.body.addLayout(row)

            reset_row = QHBoxLayout()
            reset_row.setSpacing(8)
            restore_button = QPushButton("恢复默认设置")
            restore_button.setToolTip(
                "还原本应用写入的全部配置（方案重置补丁、用户词典学习覆盖、"
                "外观覆盖、简洁译注影子副本、已保存的开关选项）。"
                "不会删除已安装的模型。"
            )
            restore_button.clicked.connect(self._on_restore)
            reset_row.addWidget(restore_button)
            self.restore_button = restore_button
            clear_cache = QPushButton("清除 AI 缓存")
            clear_cache.setToolTip("删除输入法服务的译注缓存文件，下次输入时重建。")
            clear_cache.clicked.connect(self._on_clear_cache)
            reset_row.addWidget(clear_cache)
            self.clear_cache_button = clear_cache
            reset_row.addStretch(1)
            action_card.body.addLayout(reset_row)

            self.detail_label = QLabel("")
            self.detail_label.setObjectName("DetailValue")
            self.detail_label.setWordWrap(True)
            self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            action_card.body.addWidget(self.detail_label)
            layout.addWidget(action_card)
            layout.addStretch(1)

            _update_strip(self.strip, "neutral", "尚未在本次会话中部署。")

            self._tx = TransactionController(
                self.busy, self._interactive_controls, parent=self
            )

        # -- off-thread transaction support --

        def _interactive_controls(self):
            return [
                self.redeploy_button,
                self.open_data_button,
                self.open_appconfig_button,
                self.restore_button,
                self.clear_cache_button,
            ]

        def transaction_active(self) -> bool:
            return self._tx.active

        def _open_path(self, path: Path) -> None:
            try:
                if path.exists():
                    os.startfile(str(path))  # type: ignore[attr-defined]
                else:
                    _update_strip(self.strip, "warning", f"路径不存在：{path}")
            except Exception as exc:  # pragma: no cover - defensive
                _update_strip(self.strip, "error", f"无法打开：{exc}")

        def _open_appconfig_dir(self) -> None:
            self._open_path(appconfig.config_dir())

        def _on_redeploy(self) -> None:
            from . import deploy

            deployer = paths.weasel_deployer_exe(paths.weasel_root())
            if (
                QMessageBox.question(
                    self,
                    "确认重新部署",
                    "重新部署会重建 Rime / Weasel 配置，期间可能短暂无法打字。"
                    "是否继续？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
            self._tx.run(
                "正在重新部署…",
                deploy.run_deploy,
                deployer_exe=deployer,
                timeout=120,
                on_success=lambda result: self._on_redeploy_done(deployer, result),
                on_error=self._on_redeploy_error,
            )

        def _on_redeploy_done(self, deployer, result) -> None:
            if not result.ran:
                state, text = "error", "未能启动部署器。"
            elif result.timed_out:
                state, text = "error", "部署超时，配置状态未知。"
            elif result.busy:
                state, text = "warning", "已有另一个部署器在运行，请稍后重试。"
            elif result.exit_code == 0 and not (result.stderr or "").strip():
                state, text = "success", "部署完成。"
            elif result.exit_code == 0:
                # `/deploy` returns 0 even for invalid YAML: a non-empty stderr
                # means the configuration was NOT deployed cleanly (S3).
                state, text = "error", "部署退出码为 0，但错误输出（stderr）非空。"
            else:
                state, text = "error", f"部署退出码 {result.exit_code}。"
            _update_strip(self.strip, state, text)
            self.detail_label.setText(
                f"退出码：{result.exit_code}  超时：{result.timed_out}  "
                f"忙：{result.busy}\n"
                f"部署器：{deployer}\n"
                f"提示：{result.note}\n"
                f"错误输出：{result.stderr or '（空）'}"
            )

        def _on_redeploy_error(self, message: str) -> None:
            _update_strip(self.strip, "error", f"部署失败：{message}")

        # -- restore defaults (恢复默认) --

        _RESTORE_ITEM_NAMES = {
            "learning_override": "用户词典学习覆盖",
            "weasel_style": "候选窗外观覆盖",
            "plain_gloss": "简洁译注影子副本",
            "user_yaml_options": "已保存的开关选项",
        }

        def _restore_item_text(self, item: dict) -> str:
            key = str(item.get("key") or "")
            if key == "schema_resets":
                schema_id = str(item.get("schema_id") or "")
                name = SCHEMA_LABELS.get(schema_id, "方案重置补丁")
            else:
                name = self._RESTORE_ITEM_NAMES.get(key, key or "未知项")

            if not item.get("ok"):
                status = "无法还原"
            elif item.get("changed"):
                status = "已还原"
            else:
                status = "无需更改"

            note = item.get("note")
            if key == "user_yaml_options":
                # Humanise the option names; never surface raw identifiers.
                removed = item.get("removed") or []
                unsafe = item.get("unsafe") or []
                parts: list[str] = []
                if removed:
                    parts.append(
                        "已移除：" + "、".join(switch_label(n) for n in removed)
                    )
                if unsafe:
                    parts.append(
                        "以下为流式映射，无法安全还原："
                        + "、".join(switch_label(n) for n in unsafe)
                    )
                note = "；".join(parts) or None

            text = f"{name}：{status}"
            if note:
                text += f"（{note}）"
            return text

        def _on_restore(self) -> None:
            from . import restore_defaults

            summary = (
                "将还原本应用写入的全部配置：\n\n"
                "· 各方案的重置补丁（恢复方案自带 reset 行为）\n"
                "· 用户词典学习覆盖（恢复默认开启）\n"
                "· 候选窗外观覆盖（恢复安装时默认外观）\n"
                "· 简洁译注影子副本（恢复内置语言标签）\n"
                "· 已保存的开关选项\n\n"
                "不会删除已安装的模型。完成后会重新部署一次。\n\n"
                f"{_RESTART_WARNING}"
            )
            if (
                QMessageBox.question(
                    self,
                    "确认恢复默认",
                    summary,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
            self._tx.run(
                "正在恢复默认设置并重新部署…",
                restore_defaults.restore_defaults,
                on_success=self._on_restore_done,
                on_error=self._on_restore_error,
            )

        def _on_restore_done(self, result: dict) -> None:
            lines = [self._restore_item_text(item) for item in result.get("items") or []]
            deploy = result.get("deploy") or {}
            if deploy:
                lines.append(
                    "部署："
                    + ("完成（stderr 为空）" if result.get("deploy_clean")
                       else f"退出码 {deploy.get('exit_code')}，"
                            f"stderr：{deploy.get('stderr') or '（空）'}")
                )
            self.detail_label.setText("\n".join(lines) or "（无更改）")

            if result.get("ok"):
                state = "success" if result.get("changed_any") else "neutral"
                text = (
                    "已恢复默认设置。"
                    if result.get("changed_any")
                    else "一切均已是默认状态，无需更改。"
                )
                _update_strip(self.strip, state, text)
                QMessageBox.information(self, "恢复默认", text + "\n明细见下方。")
            else:
                _update_strip(
                    self.strip,
                    "error",
                    "恢复默认未完全完成，无法安全还原的部分已在明细中说明。",
                )
                QMessageBox.warning(
                    self,
                    "恢复默认",
                    "部分项目未能还原，明细见下方。",
                )

        def _on_restore_error(self, message: str) -> None:
            _update_strip(self.strip, "error", f"恢复默认失败：{message}")
            QMessageBox.critical(self, "恢复默认失败", message)

        # -- clear AI cache --

        def _on_clear_cache(self) -> None:
            from . import server

            if (
                QMessageBox.question(
                    self,
                    "确认清除 AI 缓存",
                    "删除输入法服务的译注缓存？删除后下次输入会自动重建"
                    "（首次译注会略慢）。",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
            self._tx.run(
                "正在清除 AI 缓存…",
                server.clear_ai_cache,
                on_success=self._on_clear_cache_done,
                on_error=self._on_clear_cache_error,
            )

        def _on_clear_cache_done(self, result: dict) -> None:
            if result.get("removed"):
                _update_strip(self.strip, "success", "AI 缓存已清除。")
            elif result.get("existed"):
                _update_strip(self.strip, "error", "缓存文件存在但删除失败。")
            else:
                _update_strip(self.strip, "neutral", "没有需要清除的 AI 缓存。")
            self.detail_label.setText(f"缓存文件：{result.get('target') or '（未知）'}")

        def _on_clear_cache_error(self, message: str) -> None:
            _update_strip(self.strip, "error", f"清除 AI 缓存失败：{message}")

    class MainWindow(QMainWindow):
        """Settings window: left nav + stacked pages + status bar."""

        def __init__(self, on_hidden_to_tray=None) -> None:
            super().__init__()
            self._on_hidden_to_tray = on_hidden_to_tray
            self._force_close = False
            self.pages: dict[str, QWidget] = {}
            self.setWindowTitle(f"Language Input 设置  v{__version__}")
            self.resize(960, 640)
            self.setMinimumSize(720, 480)
            if icon_ico_path().is_file():
                self.setWindowIcon(QIcon(str(icon_ico_path())))
            self._build_ui()

        def _build_ui(self) -> None:
            central = QWidget(self)
            self.setCentralWidget(central)
            root_layout = QHBoxLayout(central)
            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(0)

            nav_container = QWidget(central)
            nav_container.setObjectName("NavContainer")
            nav_container.setAttribute(Qt.WA_StyledBackground, True)
            nav_container.setFixedWidth(208)
            nav_layout = QVBoxLayout(nav_container)
            nav_layout.setContentsMargins(0, 0, 0, 0)
            nav_layout.setSpacing(0)

            brand_box = QWidget()
            brand_box.setObjectName("CardInner")
            brand_layout = QVBoxLayout(brand_box)
            brand_layout.setContentsMargins(16, 16, 16, 12)
            brand_layout.setSpacing(4)
            brand = QLabel("Language Input")
            brand.setObjectName("Brand")
            sub = QLabel("设置")
            sub.setObjectName("BrandSub")
            brand_layout.addWidget(brand)
            brand_layout.addWidget(sub)
            nav_layout.addWidget(brand_box)

            self.nav = QListWidget(nav_container)
            self.nav.setObjectName("NavList")
            self.nav.setFrameShape(QFrame.NoFrame)
            self.nav.setIconSize(QSize(16, 16))
            self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            nav_layout.addWidget(self.nav, 1)
            root_layout.addWidget(nav_container)

            self.stack = QStackedWidget(central)
            root_layout.addWidget(self.stack, 1)

            for key, title, english, description in PAGE_SPECS:
                item = QListWidgetItem(_nav_icon(_PAGE_ICONS[key]), title)
                item.setSizeHint(QSize(0, 36))
                item.setToolTip(f"{title} / {english}")
                self.nav.addItem(item)

                if key == "overview":
                    page: QWidget = OverviewPage(title, description)
                elif key == "translation":
                    page = TranslationPage(
                        title, description,
                        open_switches=lambda: self.nav.setCurrentRow(5),
                    )
                elif key == "models":
                    page = ModelsPage(title, description)
                elif key == "dictionary":
                    page = DictionaryPage(title, description)
                elif key == "appearance":
                    page = AppearancePage(title, description)
                elif key == "keys":
                    page = KeysPage(title, description)
                else:
                    page = AboutPage(title, description)
                self.pages[key] = page
                self.stack.addWidget(_scrollable(page))

            self.nav.currentRowChanged.connect(self._on_page_changed)
            self.nav.setCurrentRow(0)

            bar = self.statusBar()
            bar.setSizeGripEnabled(False)
            bar.setFixedHeight(24)
            left = QLabel("就绪")
            left.setObjectName("StatusText")
            left.setToolTip(f"单实例互斥体：{_SINGLE_INSTANCE_MUTEX}")
            bar.addWidget(left, 1)

            right = QWidget()
            right.setObjectName("CardInner")
            right_layout = QHBoxLayout(right)
            right_layout.setContentsMargins(0, 0, 8, 0)
            right_layout.setSpacing(8)
            self.status_dot = StateDot("success", 8)
            self.status_right = QLabel("单实例已启用")
            self.status_right.setObjectName("StatusText")
            right_layout.addWidget(self.status_dot)
            right_layout.addWidget(self.status_right)
            bar.addPermanentWidget(right)

        def _on_page_changed(self, index: int) -> None:
            self.stack.setCurrentIndex(index)
            if index < 0 or index >= len(PAGE_SPECS):
                return
            key = PAGE_SPECS[index][0]
            page = self.pages.get(key)
            if key == "overview" and page is not None:
                page.refresh()
            elif key == "translation" and page is not None:
                page._refresh_status()
                page._refresh_plain_badge()

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
            if self._force_close:
                event.accept()
                return
            event.ignore()
            self.hide()
            if self._on_hidden_to_tray is not None:
                self._on_hidden_to_tray()

        def request_close(self) -> None:
            """Really close the window (used on quit / smoke teardown)."""
            self._force_close = True
            self.close()

        def transaction_active(self) -> bool:
            """True while any page is running an off-thread transaction.

            The tray ``退出`` handler consults this so the process cannot quit
            in the middle of a stop/start transaction.
            """
            for page in self.pages.values():
                guard = getattr(page, "transaction_active", None)
                if callable(guard) and guard():
                    return True
            return False

    class SettingsApp:
        """Owns the main window and the system-tray icon (main thread only)."""

        def __init__(self, app) -> None:
            self._app = app
            self._notified = False
            self.window = MainWindow(on_hidden_to_tray=self._notify_hidden)
            self.window.winId()  # Make a hidden tray window discoverable by a second launch.

            self.tray = QSystemTrayIcon(QIcon(str(icon_ico_path())), app)
            self.tray.setToolTip(_TRAY_TITLE)
            self._menu = QMenu()
            open_action = QAction("打开设置", self._menu)
            open_action.triggered.connect(self.show_window)
            quit_action = QAction("退出", self._menu)
            quit_action.triggered.connect(self.quit)
            self._menu.addAction(open_action)
            self._menu.addSeparator()
            self._menu.addAction(quit_action)
            self.tray.setContextMenu(self._menu)
            self.tray.activated.connect(self._on_tray_activated)

        # -- tray --

        def _on_tray_activated(self, reason) -> None:
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                self.show_window()

        def _notify_hidden(self) -> None:
            if self._notified:
                return
            self._notified = True
            try:
                self.tray.showMessage(
                    _TRAY_TITLE,
                    "设置窗口已隐藏到托盘，双击托盘图标可重新打开。",
                    QSystemTrayIcon.Information,
                    3000,
                )
            except Exception:
                pass

        # -- show / hide / quit --

        def show_window(self) -> None:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

        def start(self, *, minimized: bool = False) -> None:
            self.tray.show()
            if not minimized:
                self.show_window()

        def quit(self) -> None:
            if self.window.transaction_active():
                # Refuse to quit mid-transaction: aborting the stop -> poll ->
                # start sequence could leave WeaselServer stopped (no typing)
                # or the config half-written.  Report it instead of ignoring
                # the click.
                self.show_window()
                try:
                    self.tray.showMessage(
                        _TRAY_TITLE,
                        "正在重启输入法服务，请等待操作完成后再退出。",
                        QSystemTrayIcon.Warning,
                        4000,
                    )
                except Exception:
                    pass
                return
            try:
                self.tray.hide()
            except Exception:
                pass
            self._app.quit()

        def teardown(self) -> None:
            """Destroy window + tray without entering the event loop."""
            try:
                self.tray.hide()
            except Exception:
                pass
            self.window.request_close()
            self.window.deleteLater()
            self.tray.deleteLater()
            self._app.processEvents()


def run_gui(*, start_minimized: bool = False) -> int:
    if not _HAS_QT:
        print(
            f"error: PySide6 is required for the GUI ({_QT_IMPORT_ERROR}). "
            "Run with --selftest for a headless check.",
            file=sys.stderr,
        )
        return 2

    if not acquire_single_instance():
        show_existing_instance()
        return 0

    app = QApplication([sys.argv[0]])
    app.setApplicationName("Language Input Settings")
    app.setQuitOnLastWindowClosed(False)
    _apply_theme(app)

    # Honour both the explicit ``--start-minimized`` flag (used by the
    # autostart entry) and the persisted ``config.start_minimized`` setting.
    minimize_on_start = start_minimized
    if not minimize_on_start:
        try:
            minimize_on_start = bool(appconfig.load_config().start_minimized)
        except Exception:  # pragma: no cover - defensive
            minimize_on_start = False

    controller = SettingsApp(app)
    controller.start(minimized=minimize_on_start)
    return app.exec()


def run_gui_smoke() -> int:
    """Construct the window, all seven pages and the tray icon, then exit.

    Creates a ``QApplication`` and processes pending events, but never enters
    the event loop, never shows the window and never writes anywhere.
    """
    if not _HAS_QT:
        print(
            f"gui_smoke: failed (PySide6 import error: {_QT_IMPORT_ERROR})",
            file=sys.stderr,
        )
        return 1

    app = None
    controller = None
    try:
        app = QApplication([sys.argv[0]])
        app.setQuitOnLastWindowClosed(False)
        _apply_theme(app)

        controller = SettingsApp(app)

        if len(controller.window.pages) != len(PAGE_SPECS):
            raise RuntimeError(
                f"expected {len(PAGE_SPECS)} pages, built "
                f"{len(controller.window.pages)}"
            )
        if controller.tray.contextMenu() is None or not (
            controller.tray.contextMenu().actions()
        ):
            raise RuntimeError("tray context menu is empty")
        if controller.tray.icon().isNull():
            raise RuntimeError("tray icon is null (assets/icon.ico missing?)")

        app.processEvents()
        print("gui_smoke: passed")
        return 0
    except Exception as exc:  # pragma: no cover - failure path
        print(f"gui_smoke: failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            try:
                controller.teardown()
            except Exception:
                pass
        if app is not None:
            try:
                app.processEvents()
            except Exception:
                pass


def run_gui_shot(out_dir: str | os.PathLike[str]) -> int:
    """Render one PNG per page into ``out_dir`` and return 0.

    Constructs the window, shows it (so layout is realized), selects each page
    in turn and saves ``01-overview.png`` … ``07-about.png`` with
    ``QWidget.grab()``.  No user interaction and no event loop are required.
    """
    if not _HAS_QT:
        print(
            f"gui_shot: failed (PySide6 import error: {_QT_IMPORT_ERROR})",
            file=sys.stderr,
        )
        return 1

    try:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"gui_shot: failed ({exc})", file=sys.stderr)
        return 1

    app = None
    controller = None
    try:
        app = QApplication([sys.argv[0]])
        app.setQuitOnLastWindowClosed(False)
        _apply_theme(app)

        controller = SettingsApp(app)
        window = controller.window
        window.show()
        app.processEvents()
        window.raise_()
        app.processEvents()

        saved: list[Path] = []
        for index, (key, _title, _english, _description) in enumerate(PAGE_SPECS):
            window.nav.setCurrentRow(index)
            app.processEvents()
            window.repaint()
            app.processEvents()
            pixmap = window.grab()
            path = out / f"{index + 1:02d}-{key}.png"
            if not pixmap.save(str(path), "PNG"):
                raise RuntimeError(f"could not save {path}")
            saved.append(path)

        for path in saved:
            print(f"gui_shot: wrote {path.name} ({path.stat().st_size} bytes)")
        print("gui_shot: passed")
        return 0
    except Exception as exc:  # pragma: no cover - failure path
        print(f"gui_shot: failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            try:
                controller.teardown()
            except Exception:
                pass
        if app is not None:
            try:
                app.processEvents()
            except Exception:
                pass


# --- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="language-input-settings",
        description="Language Input (Weasel/Rime) settings tray application.",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run a read-only diagnostic report (no GUI) and exit",
    )
    parser.add_argument(
        "--gui-smoke",
        action="store_true",
        help="build the GUI and tray icon headlessly, then exit",
    )
    parser.add_argument(
        "--gui-shot",
        metavar="OUT_DIR",
        help="render one PNG per page into OUT_DIR, then exit",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the application version and exit",
    )
    parser.add_argument(
        "--start-minimized",
        action="store_true",
        help="start hidden in the system tray (used by the autostart entry)",
    )

    # Model management (M3).  Imported lazily so --selftest stays independent.
    from . import cli as _cli

    _cli.add_arguments(parser)

    args = parser.parse_args(argv)

    if args.version:
        print(f"language-input-settings {__version__}")
        return 0
    if args.selftest:
        return run_selftest()
    if args.gui_smoke:
        return run_gui_smoke()
    if args.gui_shot:
        return run_gui_shot(args.gui_shot)

    handled = _cli.dispatch(args)
    if handled is not None:
        return handled

    return run_gui(start_minimized=bool(args.start_minimized))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
