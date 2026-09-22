"""PyInstaller entry point for the Language Input settings tray application.

This launcher deliberately contains **no application logic**.  It only imports
:func:`language_input_settings.app.main` and forwards its exit code, so the
frozen bundle behaves exactly like ``python -m language_input_settings``.  All
behaviour lives in ``src/language_input_settings`` (which is never modified for
packaging).
"""

from __future__ import annotations

from language_input_settings.app import main

if __name__ == "__main__":
    raise SystemExit(main())
