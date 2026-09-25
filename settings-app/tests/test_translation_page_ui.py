"""Focused checks for the translation page's local and remote controls."""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from language_input_settings.app import QApplication, QMessageBox, TranslationPage
except ImportError:  # pragma: no cover - GUI dependency absent
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is unavailable")
class TranslationPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = TranslationPage("翻译", "测试")
        self.page._effective_backend = "local"
        self.page._local_coverage = {
            code: {"available": True, "missing": []}
            for code in ("en", "ja", "es")
        }
        self.page._on_backend_changed()

    def tearDown(self):
        self.page.deleteLater()
        self.app.processEvents()

    def test_local_language_action_uses_rime_switches(self):
        self.assertTrue(self.page.remote_card.isHidden())
        self.assertFalse(self.page.local_card.isHidden())
        self.page.local_language_combo.setCurrentIndex(
            self.page.local_language_combo.findData("es")
        )
        captured = {}

        def fake_run(_controller, _status, _func, **kwargs):
            captured.update(kwargs)
            return True

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            with patch.object(type(self.page._tx), "run", fake_run):
                self.page._on_apply_local_language()

        self.assertEqual(
            {
                "language_input_es": True,
                "language_input_gloss": True,
                "language_input_ai": True,
            },
            captured["changes"],
        )
        self.assertTrue(captured["preserve_across_sessions"])

    def test_remote_fields_show_only_for_remote_and_missing_model_disables_apply(self):
        self.page._local_coverage["ja"] = {
            "available": False,
            "missing": ["quickmt-en-ja"],
        }
        self.page.local_language_combo.setCurrentIndex(
            self.page.local_language_combo.findData("ja")
        )
        self.page._update_local_language_status()
        self.assertFalse(self.page.apply_language_button.isEnabled())
        self.assertIn("缺少", self.page.local_language_status.text())

        for button in self.page._backend_buttons.buttons():
            if button.property("backend_value") == "remote":
                button.setChecked(True)
        self.page._on_backend_changed()
        self.assertTrue(self.page.local_card.isHidden())
        self.assertFalse(self.page.remote_card.isHidden())
        self.assertTrue(self.page.url_edit.isEnabled())
        self.assertEqual("应用外接 API", self.page.apply_button.text())
        self.assertFalse(self.page.backend_pending_label.isHidden())


if __name__ == "__main__":
    unittest.main()
