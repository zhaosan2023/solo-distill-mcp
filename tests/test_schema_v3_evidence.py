"""V3 来源边界负控先行：可信扫描、秘密检查、变化拒绝与取消。

仅使用隔离临时来源；不访问网关、不导入或执行来源，不证明完整阶段。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from solo_distill.evidence import EvidenceBlocked
from solo_distill.extractor import Project, Scanner
from test_offline import configuration


class V3EvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = {
            "sample.py": b"def interfaces():\n return 1\n",
            "empty.py": b"# authorized empty Python file\n",
            "encoded.py": b"# coding: latin-1\ndef helper():\n return 'caf\xe9'\n",
        }
        for path, raw in self.files.items():
            (self.root / path).write_bytes(raw)
        self.project = Project.from_config("fixture", configuration(self.root))
        self.cancelled = threading.Event()
        self.scanner = None
        self.report = None

    def reader(self, *, deadline=None, report_changes=None, include=(".",)):
        from solo_distill.evidence_v3 import V3EvidenceReader
        self.scanner = Scanner(self.project, include, 10)
        self.report = self.scanner.run()
        report = dict(self.report)
        if report_changes:
            report.update(report_changes)
        return V3EvidenceReader(self.scanner, report,
            time.monotonic() + 10 if deadline is None else deadline, self.cancelled)

    def test_complete_scope_raw_bytes_and_empty_file_without_legacy_package(self):
        reader = self.reader()
        with patch("solo_distill.package_v2.build_package", side_effect=AssertionError("no legacy package")), \
                patch("solo_distill.evidence_v2.V2EvidenceReader.inventory", side_effect=AssertionError("no V2 inventory")), \
                patch("socket.socket", side_effect=AssertionError("offline")), \
                patch("subprocess.Popen", side_effect=AssertionError("source is data")):
            self.assertEqual(reader.inventory(), self.files)
            reader.verify()
        self.assertEqual(reader.raw_files, self.files)
        self.assertEqual(reader.snapshot_id, self.report["snapshot_id"])
        self.assertEqual(reader.project_id, "fixture")
        for entry in self.report["entries"]:
            if entry["status"] == "parsed":
                self.assertEqual(hashlib.sha256(reader.raw_files[entry["path"]]).hexdigest(),
                                 entry["content_sha256"])

    def test_returned_scope_and_report_are_isolated(self):
        reader = self.reader()
        before = self.report["snapshot_id"]
        value = reader.inventory()
        value.clear()
        view = reader.raw_files
        view["invented.py"] = b"pass\n"
        self.report["entries"].clear()
        self.report["snapshot_id"] = "changed"
        self.assertEqual(reader.raw_files, self.files)
        self.assertEqual(reader.snapshot_id, before)
        reader.verify()

    def test_cancelled_before_inventory_returns_no_sources(self):
        reader = self.reader()
        self.cancelled.set()
        with self.assertRaises(EvidenceBlocked):
            reader.inventory()
        self.assertEqual(reader.raw_files, {})

    def test_deadline_equality_and_overrun_block_before_read(self):
        for offset in (0, 0.001):
            with self.subTest(offset=offset):
                reader = self.reader()
                with patch("solo_distill.evidence_v3.time.monotonic", return_value=reader.deadline + offset):
                    with self.assertRaises(EvidenceBlocked):
                        reader.inventory()
                self.assertEqual(reader.raw_files, {})

    def test_cancel_after_read_is_not_recorded_as_complete(self):
        reader = self.reader()
        original = self.scanner.read_bytes
        def cancel_during_read(*args, **kwargs):
            raw = original(*args, **kwargs)
            self.cancelled.set()
            return raw
        with patch.object(self.scanner, "read_bytes", side_effect=cancel_during_read):
            with self.assertRaises(EvidenceBlocked):
                reader.inventory()
        self.assertEqual(reader.raw_files, {})

    def test_secret_in_any_authorized_file_blocks_entire_inventory(self):
        (self.root / "unselected.py").write_bytes(b"api_key = 'real-sensitive-token-12345'\n")
        reader = self.reader()
        with self.assertRaisesRegex(EvidenceBlocked, "possible_literal_secret"):
            reader.inventory()
        self.assertEqual(reader.raw_files, {})

    def test_source_change_between_scan_and_read_blocks(self):
        reader = self.reader()
        (self.root / "sample.py").write_bytes(self.files["sample.py"] + b"# changed\n")
        with self.assertRaises(EvidenceBlocked):
            reader.inventory()
        self.assertEqual(reader.raw_files, {})

    def test_same_size_source_change_is_not_accepted(self):
        reader = self.reader()
        path = self.root / "sample.py"
        info = path.stat()
        path.write_bytes(self.files["sample.py"].replace(b"1", b"2"))
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaises(EvidenceBlocked):
            reader.inventory()
        self.assertEqual(reader.raw_files, {})

    def test_post_inventory_content_change_blocks_final_verification(self):
        reader = self.reader()
        reader.inventory()
        (self.root / "sample.py").write_bytes(self.files["sample.py"] + b"# changed\n")
        with self.assertRaises(EvidenceBlocked):
            reader.verify()

    def test_new_file_after_inventory_blocks_directory_identity(self):
        reader = self.reader()
        reader.inventory()
        (self.root / "new.py").write_bytes(b"pass\n")
        with self.assertRaises(EvidenceBlocked):
            reader.verify()

    def test_repeated_inventory_does_not_bypass_source_freshness(self):
        reader = self.reader()
        self.assertEqual(reader.inventory(), self.files)
        (self.root / "sample.py").write_bytes(self.files["sample.py"] + b"# changed\n")
        with self.assertRaises(EvidenceBlocked):
            reader.inventory()

    def test_symlink_replacement_is_not_followed(self):
        reader = self.reader()
        path = self.root / "sample.py"
        path.unlink()
        path.symlink_to(self.root / "encoded.py")
        with self.assertRaises(EvidenceBlocked):
            reader.inventory()
        self.assertEqual(reader.raw_files, {})

    def test_parse_failure_and_zero_python_hits_are_blocked(self):
        (self.root / "broken.py").write_bytes(b"def broken(:\n")
        with self.assertRaises(EvidenceBlocked):
            reader = self.reader()
            reader.inventory()
        for path in self.root.iterdir():
            path.unlink()
        with self.assertRaises(EvidenceBlocked):
            reader = self.reader()
            reader.inventory()

    def test_unrelated_unsupported_asset_is_disclosed_not_parsed(self):
        (self.root / "data.txt").write_bytes(b"not Python; never executed")
        reader = self.reader()
        self.assertEqual(reader.inventory(), self.files)
        self.assertEqual(self.report["coverage"]["skipped_files"], 1)
        self.assertEqual(self.report["unfinished"], [{"path": "data.txt", "reason": "unsupported_language_or_asset"}])
        reader.verify()

    def test_narrow_authorized_scan_is_preserved_without_expansion(self):
        reader = self.reader(include=("sample.py",))
        self.assertEqual(reader.inventory(), {"sample.py": self.files["sample.py"]})
        self.assertEqual(self.report["include"], ["sample.py"])
        reader.verify()

    def test_mismatched_trusted_scan_context_is_rejected(self):
        for changes in ({"project_id": "other"}, {"include": ["other.py"]},
                        {"entries": []}, {"freshness": "unknown_or_changed"},
                        {"inventory_complete": False}, {"snapshot_id": ""}):
            with self.subTest(changes=changes), self.assertRaises((EvidenceBlocked, ValueError)):
                reader = self.reader(report_changes=changes)
                reader.inventory()

    def test_cancelled_final_verify_and_unread_source_verify_are_blocked(self):
        reader = self.reader()
        with self.assertRaises(EvidenceBlocked):
            reader.verify()
        reader.inventory()
        self.cancelled.set()
        with self.assertRaises(EvidenceBlocked):
            reader.verify()

    def test_top_level_source_stays_data(self):
        marker = self.root / "EXECUTED"
        raw = (f"open({str(marker)!r}, 'w').write('bad')\n"
               "raise RuntimeError('do not execute')\n").encode("utf-8")
        (self.root / "danger.py").write_bytes(raw)
        reader = self.reader()
        with patch("socket.socket", side_effect=AssertionError("offline")), \
                patch("subprocess.Popen", side_effect=AssertionError("source is data")):
            self.assertEqual(reader.inventory()["danger.py"], raw)
            reader.verify()
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
