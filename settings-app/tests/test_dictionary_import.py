"""Focused checks for private Sogou-text to Rime-pack import."""

from __future__ import annotations

import tempfile
import struct
import unittest
from pathlib import Path

from language_input_settings import dictionary_import
from language_input_settings.dictionary_syllables import MSPY_SYLLABLES, SOGOU_SYLLABLES


def _scel(word: str, syllables: tuple[str, ...]) -> bytes:
    data = bytearray(0x1540)
    struct.pack_into("<III", data, 0x120, 1, 1, 0)
    struct.pack_into("<I", data, 0, 0x1540)
    data += struct.pack("<I", len(syllables))
    for index, syllable in enumerate(syllables):
        encoded = syllable.encode("utf-16-le")
        data += struct.pack("<HH", index, len(encoded)) + encoded
    encoded_word = word.encode("utf-16-le")
    data += struct.pack("<HH", 1, len(syllables) * 2)
    data += b"".join(struct.pack("<H", i) for i in range(len(syllables)))
    data += struct.pack("<H", len(encoded_word)) + encoded_word
    data += struct.pack("<HI", 4, 25)
    return bytes(data)


def _sgpu(word: str, syllables: tuple[str, ...]) -> bytes:
    encoded_word = word.encode("utf-16-le")
    record = struct.pack("<hH", 23, 0) + b"\0" * 5
    record += struct.pack("<H", len(syllables) * 2)
    record += b"".join(struct.pack("<H", SOGOU_SYLLABLES.index(s)) for s in syllables)
    record += struct.pack("<HH", 0, len(encoded_word)) + encoded_word
    data = bytearray(100 + len(record))
    data[:4] = b"SGPU"
    struct.pack_into("<I", data, 16, len(data))
    struct.pack_into("<IIII", data, 56, 96, 4, 1, 100)
    struct.pack_into("<I", data, 72, len(record))
    data[100:] = record
    return bytes(data)


def _udl(word: str, syllables: tuple[str, ...]) -> bytes:
    data = bytearray(0x2400 + 60)
    data[:4] = b"\x55\xaa\x88\x81"
    struct.pack_into("<I", data, 12, 1)
    data[0x2400 + 10] = len(word)
    data[0x2400 + 12 : 0x2400 + 12 + len(word) * 2] = word.encode("utf-16-le")
    pos = 0x2400 + 12 + len(word) * 2
    for syllable in syllables:
        struct.pack_into("<H", data, pos, MSPY_SYLLABLES.index(syllable))
        pos += 2
    return bytes(data)


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

    def test_scel_and_local_ime_formats_preserve_readings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user = root / "Rime"
            user.mkdir()
            for filename, content in (
                ("sample.scel", _scel("已打开", ("yi", "da", "kai"))),
                ("sgim_gd_usr.bin", _sgpu("重庆", ("chong", "qing"))),
                ("ChsPinyinUDL.dat", _udl("银行", ("yin", "hang"))),
            ):
                source = root / filename
                source.write_bytes(content)
                preview = dictionary_import.preview_export(source)
                self.assertEqual(preview.entries, 1)
                self.assertEqual(preview.generated_readings, 0)
                dictionary_import.import_export(source, user_dir=user, deploy=False)
            pack = (user / "language_input_sogou.dict.yaml").read_text(encoding="utf-8")
            self.assertIn("已打开\tyi da kai\t1025", pack)
            self.assertIn("重庆\tchong qing\t1023", pack)
            self.assertIn("银行\tyin hang\t1001", pack)

    def test_discovery_and_cell_directory_ignore_unknown_header(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            sogou = home / "AppData/LocalLow/SogouPY"
            cells = sogou / "scd"
            cells.mkdir(parents=True)
            (cells / "one.scel").write_bytes(_scel("已打开", ("yi", "da", "kai")))
            (cells / "hotcell_dict.scel").write_bytes(b"other format")
            (sogou / "sgim_gd_usr.bin").write_bytes(_sgpu("重庆", ("chong", "qing")))
            microsoft = home / "AppData/Roaming/Microsoft/InputMethod/Chs"
            microsoft.mkdir(parents=True)
            (microsoft / "ChsPinyinUDL.dat").write_bytes(_udl("银行", ("yin", "hang")))
            sources = dictionary_import.discover_local_sources(home)
            self.assertEqual(len(sources), 3)
            preview = dictionary_import.preview_export(cells)
            self.assertEqual(preview.entries, 1)
            self.assertEqual(preview.ignored_files, 1)
            self.assertEqual(preview.files, 1)

    def test_corrupt_binary_is_rejected_without_writing_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "bad.scel"
            source.write_bytes(_scel("已打开", ("yi", "da", "kai"))[:-3])
            with self.assertRaises(dictionary_import.DictionaryImportError):
                dictionary_import.import_export(source, user_dir=root / "Rime", deploy=False)
            self.assertFalse((root / "Rime" / "language_input_sogou.dict.yaml").exists())

    def test_sogou_lue_nue_are_migrated_to_rime_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user = root / "Rime"
            user.mkdir()
            source = root / "sgim_gd_usr.bin"
            source.write_bytes(_sgpu("略", ("lue",)))
            pack_path = user / "language_input_sogou.dict.yaml"
            pack_path.write_text(
                "# Generated by Language Input from a local text export.\n"
                "---\nname: language_input_sogou\nversion: \"old\"\n"
                "sort: by_weight\n...\n略\tlue\t1023\n",
                encoding="utf-8",
            )
            result = dictionary_import.import_export(source, user_dir=user, deploy=False)
            self.assertEqual(result["added"], 0)
            self.assertEqual(result["total"], 1)
            pack = pack_path.read_text(encoding="utf-8")
            self.assertIn("略\tlve\t1023\n", pack)
            self.assertNotIn("略\tlue\t", pack)

            source.write_bytes(_sgpu("虐", ("nue",)))
            dictionary_import.import_export(source, user_dir=user, deploy=False)
            self.assertIn("虐\tnve\t1023\n", pack_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
