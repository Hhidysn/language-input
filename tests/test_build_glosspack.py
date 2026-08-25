from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_glosspack import Sense, build_pack, choose_gloss, clean_sense  # noqa: E402


class GlossPackBuilderTests(unittest.TestCase):
    def test_clean_sense_removes_dictionary_markup(self) -> None:
        self.assertEqual("thank you", clean_sense("(coll.) thank you"))
        self.assertEqual("see 谢谢", clean_sense("see 謝謝|谢谢[xie4 xie5]"))

    def test_choose_gloss_prefers_meaning_over_cross_reference(self) -> None:
        senses = [
            Sense("variant of 學習", 0),
            Sense("to study", 1),
            Sense("to learn", 2),
        ]
        self.assertEqual("to study; to learn", choose_gloss(senses))

    def test_build_pack_is_sorted_and_records_provenance(self) -> None:
        sample = (
            "# CC-CEDICT\n"
            "#! version=1\n"
            "#! subversion=0\n"
            "#! date=2026-08-24T05:05:01Z\n"
            "世界 世界 [shi4 jie4] /world/\n"
            "學習 学习 [xue2 xi2] /to study/to learn/\n"
            "電腦 电脑 [dian4 nao3] /computer/CL:臺|台[tai2]/\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            source = temp / "cedict.zip"
            with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("cedict_ts.u8", sample)
            output = temp / "en.tsv"
            manifest_path = temp / "en.manifest.json"
            manifest = build_pack(source, output, manifest_path)

            rows = [
                line
                for line in output.read_text(encoding="utf-8").splitlines()
                if not line.startswith("#")
            ]
            self.assertEqual(sorted(rows), rows)
            self.assertIn("世界\tworld", rows)
            self.assertIn("学习\tto study; to learn", rows)
            self.assertIn("學習\tto study; to learn", rows)
            self.assertIn("电脑\tcomputer", rows)
            self.assertNotIn("电脑\tcomputer; 台", rows)
            self.assertEqual(5, manifest["entries"])
            on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("CC-BY-SA-4.0", on_disk["source"]["license"])
            self.assertEqual(64, len(on_disk["output"]["sha256"]))


if __name__ == "__main__":
    unittest.main()
