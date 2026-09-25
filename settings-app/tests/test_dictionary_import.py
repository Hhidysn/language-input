"""Focused checks for private Sogou-text to Rime-pack import."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from language_input_settings import dictionary_import


class DictionaryImportTests(unittest.TestCase):
    def test_preview_and_merge_preserve_existing_schema_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sogou.txt"
            source.write_text(
                "词语\t拼音\t词频\n"
                "已打开\tyi da kai\t15\n"
                "重庆\tchong qing\t3\n"
                "测试词\n"
                "https://example.invalid\n",
                encoding="utf-8",
            )
            user = root / "Rime"
            user.mkdir()
            flypy = user / "language_input_flypy.custom.yaml"
            flypy.write_text(
                "patch:\n  translator/enable_user_dict: false\n"
                "  translator/packs/+ :\n    - existing_pack\n",
                encoding="utf-8",
            )

            preview = dictionary_import.preview_export(source)
            self.assertEqual(preview.entries, 3)
            self.assertEqual(preview.skipped, 1)
            self.assertEqual(preview.generated_readings, 1)

            result = dictionary_import.import_export(source, user_dir=user, deploy=False)
            self.assertEqual(result["added"], 3)
            pack = (user / "language_input_sogou.dict.yaml").read_text(encoding="utf-8")
            self.assertIn("已打开\tyi da kai\t1015\n", pack)
            self.assertIn("重庆\tchong qing\t1003\n", pack)
            self.assertIn("测试词\tce shi ci\t1000\n", pack)
            self.assertIn("translator/enable_user_dict: false", flypy.read_text(encoding="utf-8"))
            self.assertIn("existing_pack", flypy.read_text(encoding="utf-8"))
            self.assertIn("language_input_sogou", flypy.read_text(encoding="utf-8"))
            self.assertIn(
                "language_input_sogou",
                (user / "language_input_pinyin.custom.yaml").read_text(encoding="utf-8"),
            )

            again = dictionary_import.import_export(source, user_dir=user, deploy=False)
            self.assertEqual(again["added"], 0)
            self.assertEqual(again["total"], 3)
            self.assertEqual(
                flypy.read_text(encoding="utf-8").count("language_input_sogou"), 1
            )

    def test_utf16_word_only_and_invalid_backup_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "export.txt"
            source.write_bytes("已打开\n".encode("utf-16"))
            preview = dictionary_import.preview_export(source)
            self.assertEqual(preview.entries, 1)
            self.assertEqual(preview.encoding, "UTF-16")
            self.assertEqual(preview.generated_readings, 1)

            source.write_bytes(b"\x00\x01\x02\x03")
            with self.assertRaises(dictionary_import.DictionaryImportError):
                dictionary_import.preview_export(source)

    def test_refuses_to_overwrite_user_managed_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "export.txt"
            source.write_text("已打开\n", encoding="utf-8")
            user = root / "Rime"
            user.mkdir()
            pack = user / "language_input_sogou.dict.yaml"
            pack.write_text("user data\n", encoding="utf-8")
            with self.assertRaises(dictionary_import.DictionaryImportError):
                dictionary_import.import_export(source, user_dir=user, deploy=False)
            self.assertEqual(pack.read_text(encoding="utf-8"), "user data\n")


if __name__ == "__main__":
    unittest.main()
