# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Language Input settings application.

Build a **onedir**, **windowed** (no console) bundle named
``LanguageInputSettings`` from ``packaging/entry.py``.

Layout notes / packaging pitfalls
---------------------------------
* ``src/language_input_settings/app.py`` resolves its assets as
  ``Path(__file__).resolve().parents[2] / "assets"`` and its app-owned config
  as ``... / "config"``.  In a PyInstaller 6 onedir bundle the frozen package
  lives at ``<bundle>/_internal/language_input_settings/...``, so
  ``parents[2]`` is the **bundle root** (``<bundle>``).  PyInstaller cannot
  write DATA entries outside ``_internal`` (parent-dir components are
  rejected), so :file:`build.ps1` mirrors ``assets/`` and ``config/`` to the
  bundle root after the build.  The same files are also declared below so the
  bundle is self-describing and works if the runtime resolution ever changes.
* The vendored per-file trust descriptors *are* resolved relative to the
  package (``<...>/language_input_settings/data/components``) and therefore map
  directly onto a normal ``datas`` destination.
* UPX is disabled everywhere (``upx=False``); Qt DLLs must not be packed.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# ``SPECPATH`` is injected by PyInstaller and points at this file's directory.
SPEC_DIR = Path(SPECPATH).resolve()
APP_DIR = SPEC_DIR.parent  # settings-app/
SRC_DIR = APP_DIR / "src"
ASSETS_DIR = APP_DIR / "assets"
CONFIG_DIR = APP_DIR / "config"
COMPONENTS_DIR = SRC_DIR / "language_input_settings" / "data" / "components"

# --- data files -------------------------------------------------------------
# Vendored QuickMT per-file trust descriptors (design doc §7.3 / R10).  These
# are package data and resolve next to the frozen package.
datas = [
    (str(COMPONENTS_DIR / name), "language_input_settings/data/components")
    for name in sorted(path.name for path in COMPONENTS_DIR.glob("*.json"))
]

# App-owned assets and download metadata (mirrored to the bundle root by
# build.ps1 where app_root()/config_dir() expect them).
datas += [
    (str(ASSETS_DIR / "theme.qss"), "assets"),
    (str(ASSETS_DIR / "icon.ico"), "assets"),
    (str(CONFIG_DIR / "download-sources.json"), "config"),
]

# ``ruamel.yaml`` selects its (optional) C extension and codecs lazily; collect
# the whole subpackage so the pure-Python fallback is always present.
hiddenimports = collect_submodules("ruamel.yaml")

# --- excluded modules -------------------------------------------------------
# Keep the bundle lean: the app only uses QtCore/QtGui/QtWidgets/QtNetwork.
excludes = [
    # Qt modules/plugins the app never imports.
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtConcurrent",
    "PySide6.QtDBus",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickTest",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    # Interim (pre-PySide6) GUI/tray stack that must never be bundled.
    "pystray",
    "PIL",
    "tkinter",
    "_tkinter",
    # Unused heavy stdlib helpers.
    "pydoc_data",
    "unittest",
]

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LanguageInputSettings",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # --windowed: GUI subsystem, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS_DIR / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LanguageInputSettings",
)
