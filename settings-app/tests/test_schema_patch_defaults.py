"""Regression checks for switch defaults across a Rime server restart."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from language_input_settings import rime_settings, schema_patch


class SchemaPatchDefaultsTests(unittest.TestCase):
    def test_unsaved_switch_keeps_default_and_saved_choice_loses_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            user = Path(temp)
            build = user / "build"
            build.mkdir()
            (build / "language_input_flypy.schema.yaml").write_text(
                "switches:\n"
                "  - name: language_input_gloss\n"
                "  - name: language_input_ai\n    reset: 0\n"
                "  - options: [language_input_en, language_input_ja, language_input_es]\n"
                "    reset: 0\n"
                "  - name: language_input_speech\n",
                encoding="utf-8",
            )
            (user / "user.yaml").write_text(
                "var:\n  option:\n    language_input_ai: false\n"
                "    language_input_en: false\n    language_input_ja: true\n",
                encoding="utf-8",
            )
            custom = user / "language_input_flypy.custom.yaml"
            custom.write_text(
                "patch:\n  switches/@0/reset:\n  switches/@3/reset:\n"
                "  translator/packs/+: [language_input_sogou]\n",
                encoding="utf-8",
            )

            schema_patch.neutralize_resets("language_input_flypy", user)
            patch = rime_settings._load_yaml(custom)["patch"]
            self.assertNotIn("switches/@0/reset", patch)
            self.assertNotIn("switches/@3/reset", patch)
            self.assertIsNone(patch["switches/@1/reset"])
            self.assertIsNone(patch["switches/@2/reset"])
            self.assertIn("translator/packs/+", patch)

            before = custom.read_bytes()
            schema_patch.neutralize_resets("language_input_flypy", user)
            self.assertEqual(custom.read_bytes(), before)

    def test_no_saved_switch_does_not_remove_default(self):
        with tempfile.TemporaryDirectory() as temp:
            user = Path(temp)
            build = user / "build"
            build.mkdir()
            (build / "language_input_flypy.schema.yaml").write_text(
                "switches:\n  - name: language_input_gloss\n    reset: 1\n",
                encoding="utf-8",
            )
            custom = schema_patch.neutralize_resets("language_input_flypy", user)
            self.assertFalse(custom.exists())


if __name__ == "__main__":
    unittest.main()
