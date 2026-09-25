"""Focused checks for synchronizing active Sogou fuzzy pairs to Xiaohe."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from language_input_settings import fuzzy_pinyin, rime_settings


def _sogou_file(path: Path, fuzzy: str) -> None:
    path.write_text(
        "[Metadata]\nversion=1\n[Fuzzy]\n" + fuzzy + "\n[Gray]\nh=f\n",
        encoding="utf-16",
    )


class FuzzyPinyinTests(unittest.TestCase):
    def test_sync_preserves_custom_patch_and_updates_selected_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "Fuzzy.dat"
            _sogou_file(source, "zh=z\nn=l\ning=in")
            user = root / "Rime"
            user.mkdir()
            custom = user / "language_input_flypy.custom.yaml"
            custom.write_text(
                "patch:\n  switches/@4/reset:\n  translator/packs/+ :\n"
                "    - language_input_sogou\n",
                encoding="utf-8",
            )
            result = fuzzy_pinyin.sync_sogou_fuzzy(source=source, user_dir=user, deploy=False)
            self.assertEqual(result["pairs"], (("zh", "z"), ("n", "l"), ("ing", "in")))
            self.assertEqual(len(result["rules"]), 6)
            self.assertTrue(result["changed"])
            document = rime_settings._load_yaml(custom)
            patch = document["patch"]
            self.assertIn("translator/packs/+", patch)
            self.assertIn("switches/@4/reset", patch)
            self.assertEqual(patch["speller/algebra/@before 00"], "derive/^zh/z/")
            self.assertEqual(patch["speller/algebra/@before 05"], "derive/in$/ing/")
            self.assertNotIn("derive/^h/f/", patch.values())

            again = fuzzy_pinyin.sync_sogou_fuzzy(source=source, user_dir=user, deploy=False)
            self.assertFalse(again["changed"])
            _sogou_file(source, "n=l")
            updated = fuzzy_pinyin.sync_sogou_fuzzy(source=source, user_dir=user, deploy=False)
            self.assertTrue(updated["changed"])
            patch = rime_settings._load_yaml(custom)["patch"]
            self.assertEqual(patch["speller/algebra/@before 00"], "derive/^n/l/")
            self.assertEqual(patch["speller/algebra/@before 01"], "derive/^l/n/")
            self.assertNotIn("speller/algebra/@before 02", patch)

    def test_unknown_sogou_pair_and_custom_rule_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "Fuzzy.dat"
            _sogou_file(source, "unknown=pair")
            with self.assertRaises(fuzzy_pinyin.FuzzyPinyinError):
                fuzzy_pinyin.read_sogou_pairs(source)

            _sogou_file(source, "n=l")
            user = root / "Rime"
            user.mkdir()
            custom = user / "language_input_flypy.custom.yaml"
            original = "patch:\n  speller/algebra/@before 00: derive/^x/y/\n"
            custom.write_text(original, encoding="utf-8")
            with self.assertRaises(fuzzy_pinyin.FuzzyPinyinError):
                fuzzy_pinyin.sync_sogou_fuzzy(source=source, user_dir=user, deploy=False)
            self.assertEqual(custom.read_text(encoding="utf-8"), original)

            custom.write_text("patch:\n  speller:\n    algebra: []\n", encoding="utf-8")
            with self.assertRaises(fuzzy_pinyin.FuzzyPinyinError):
                fuzzy_pinyin.sync_sogou_fuzzy(source=source, user_dir=user, deploy=False)

    def test_deploy_restarts_running_server_after_prism_is_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "Fuzzy.dat"
            _sogou_file(source, "n=l")
            user = root / "Rime"
            build = user / "build"
            build.mkdir(parents=True)
            (build / "language_input_flypy.schema.yaml").write_text(
                "speller:\n  algebra:\n    - derive/^n/l/\n    - derive/^l/n/\n",
                encoding="utf-8",
            )
            (build / "language_input_flypy.prism.bin").write_bytes(b"prism")
            events = []

            def deploy():
                events.append("deploy")
                return {"clean": True}

            @contextmanager
            def restart():
                events.append("stop")
                state = {"was_running": True, "started": True, "start_error": None}
                yield state
                events.append("start")

            with (
                patch.object(fuzzy_pinyin.rime_settings, "run_deploy", deploy),
                patch.object(fuzzy_pinyin.server, "server_stopped", restart),
            ):
                result = fuzzy_pinyin.sync_sogou_fuzzy(
                    source=source, user_dir=user, deploy=True
                )
            self.assertTrue(result["clean"])
            self.assertEqual(events, ["deploy", "stop", "start"])


if __name__ == "__main__":
    unittest.main()
