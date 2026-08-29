from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_limodel_pack import audit_pack, build_pack, load_component_manifest


class LiModelPackTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        component = root / "component"
        component.mkdir()
        (component / "model.bin").write_bytes(b"model-bytes")
        notice = root / "NOTICE.txt"
        notice.write_text("attribution\n", encoding="utf-8")
        manifest = root / "component.json"
        payload = (component / "model.bin").read_bytes()
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "component_id": "fixture-en",
                    "repository": "example/fixture",
                    "revision": "deadbeef",
                    "license": "CC-BY-4.0",
                    "provides": ["en"],
                    "requires": [],
                    "runtime_bytes": len(payload),
                    "files": [
                        {
                            "path": "model.bin",
                            "size": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest, component, notice

    def test_builds_stored_reproducible_pack_and_audits_exact_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, component, notice = self.make_fixture(root)
            first = root / "first.limodel"
            second = root / "second.limodel"
            result = build_pack(
                component_manifest_path=manifest,
                component_root=component,
                notice_path=notice,
                output_path=first,
            )
            build_pack(
                component_manifest_path=manifest,
                component_root=component,
                notice_path=notice,
                output_path=second,
            )
            self.assertEqual(result["sha256"], hashlib.sha256(second.read_bytes()).hexdigest())
            audited = audit_pack(first)
            self.assertEqual("fixture-en", audited["component_id"])
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    {"manifest.json", "NOTICE.txt", "model/model.bin"},
                    set(archive.namelist()),
                )
                self.assertTrue(
                    all(row.compress_type == zipfile.ZIP_STORED for row in archive.infolist())
                )

    def test_rejects_corrupt_component_before_creating_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, component, notice = self.make_fixture(root)
            (component / "model.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                build_pack(
                    component_manifest_path=manifest,
                    component_root=component,
                    notice_path=notice,
                    output_path=root / "bad.limodel",
                )

    def test_rejects_manifest_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _, _ = self.make_fixture(root)
            document = json.loads(manifest.read_text(encoding="utf-8"))
            document["files"][0]["path"] = "../model.bin"
            manifest.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsafe component path"):
                load_component_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
