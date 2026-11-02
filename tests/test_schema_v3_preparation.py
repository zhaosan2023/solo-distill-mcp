"""V3 共用准备调用链负控先行：隔离来源、独立选择与双层结构差分。

不访问真实网关，不执行来源；本组不证明服务发布、冷预检进程期限
或人工语义验收。准备接口供后续显式服务及冷预检入口共同调用。
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from schema_v3_core_oracle import expected_core
from schema_v3_selection_oracle import expected_selection
from solo_distill.evidence import EvidenceBlocked
from solo_distill.extractor import Project
# 在负控补丁生效前加载准备链，避免首次导入永久绑定临时的 open_root mock。
from solo_distill.preparation_v3 import prepare_snapshot
from test_offline import configuration


class SnapshotPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = {
            "sample.py": b"def interfaces():\n return 1\n",
            "empty.py": b"# empty authorized Python file\n",
        }
        for path, raw in self.files.items():
            (self.root / path).write_bytes(raw)
        self.project = Project.from_config("fixture", configuration(self.root))
        self.cancelled = threading.Event()
        self.question = "interfaces"
        self.dimensions = [{"id": "interfaces", "question": "interfaces",
                            "required": True, "evidence": "sourced_inference"}]

    def prepare(self, **changes):
        options = {"project": self.project, "include": (".",),
                   "question": self.question, "dimensions": self.dimensions,
                   "deadline": time.monotonic() + 10,
                   "cancelled": self.cancelled}
        options.update(changes)
        with patch("socket.socket", side_effect=AssertionError("offline only")), \
                patch("subprocess.Popen", side_effect=AssertionError("source is data")):
            return prepare_snapshot(**options)

    def test_complete_chain_matches_independent_selection_and_core(self):
        result = self.prepare()
        self.assertEqual(result.raw_files, self.files)
        report = result.report
        self.assertEqual(report["coverage"]["parsed_files"], 2)
        trusted = {"project_id": "fixture", "snapshot_id": report["snapshot_id"],
                   "question": self.question, "dimensions": self.dimensions,
                   "frozen_coverage": report["coverage"]}
        messages, post, selection = expected_selection(self.files, **trusted)
        self.assertEqual(result.messages, messages)
        self.assertEqual(result.post, post)
        self.assertEqual(result.selection, selection)
        core = expected_core(self.files, project_id="fixture",
            snapshot_id=report["snapshot_id"],
            required_core_unit_ids=selection["core_unit_ids"],
            selection_id=selection["selection_id"])
        self.assertEqual(result.core.document, core)
        self.assertEqual(result.package, core["package"])
        result.verify_sources()
        self.assertLessEqual(len(result.post), 150000)

    def test_public_messages_reencode_exact_post_with_caller_field_order(self):
        from solo_distill.gateway import encode_request
        from solo_distill.task_v3 import build_task
        dimensions = [{"evidence": "sourced_inference", "required": True,
                       "question": "interfaces", "id": "interfaces"}]
        result = self.prepare(dimensions=dimensions)
        self.assertEqual(encode_request(result.messages), result.post)
        task = build_task({"summary": "", "conclusions": [], "unknowns": []},
            result.core, result.raw_files, project_id="fixture",
            snapshot_id=result.report["snapshot_id"],
            required_core_unit_ids=result.selection["core_unit_ids"],
            selection_id=result.selection["selection_id"], request_id="order-regression",
            question=self.question, dimensions=dimensions, post=result.post,
            frozen_coverage=result.report["coverage"], failures=[])
        self.assertEqual(task["status"], "partial")

    def test_operation_local_parse_reuse_and_no_historical_metrics(self):
        from solo_distill.snapshot_v2 import snapshot_metrics
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first.metrics["syntax"]["parse_calls"], len(self.files))
        self.assertEqual(second.metrics["syntax"]["parse_calls"], len(self.files))
        self.assertEqual(first.core.payload, second.core.payload)
        self.assertEqual(first.post, second.post)
        self.assertEqual(snapshot_metrics()["parse_calls"], 0)
        for name in ("scan", "read", "index", "selection", "build",
                     "authenticate", "encode", "source_verify"):
            self.assertIn(name, first.metrics["stage_seconds"])
            self.assertGreaterEqual(first.metrics["stage_seconds"][name], 0)

    def test_public_views_do_not_mutate_frozen_preparation(self):
        result = self.prepare()
        before = copy.deepcopy(result.selection)
        result.raw_files.clear()
        result.selection["core_unit_ids"].clear()
        result.report["entries"].clear()
        result.package["macro"]["files"].clear()
        result.messages.clear()
        result.metrics["syntax"]["parse_calls"] = -1
        self.assertEqual(result.raw_files, self.files)
        self.assertEqual(result.selection, before)
        self.assertEqual(result.report["coverage"]["parsed_files"], 2)
        self.assertEqual(len(result.package["macro"]["files"]), 2)
        self.assertEqual(len(result.messages), 2)
        self.assertEqual(result.metrics["syntax"]["parse_calls"], 2)
        result.verify_sources()

    def test_exact_external_selection_is_required_when_supplied(self):
        first = self.prepare()
        second = self.prepare(frozen_selection=first.selection)
        self.assertEqual(second.post, first.post)
        self.assertEqual(second.core.payload, first.core.payload)
        for change in ({"core_unit_ids": []}, {"selection_id": "invented"},
                       {"snapshot_id": "other"}, {"question_id": "other"}):
            frozen = {**first.selection, **change}
            with self.subTest(change=change), self.assertRaises((ValueError, EvidenceBlocked)):
                self.prepare(frozen_selection=frozen)

    def test_source_drift_rejects_previous_frozen_selection(self):
        first = self.prepare()
        (self.root / "sample.py").write_bytes(self.files["sample.py"] + b"# changed\n")
        with self.assertRaises((ValueError, EvidenceBlocked)):
            self.prepare(frozen_selection=first.selection)

    def test_cancel_before_scan_and_expired_deadline_do_not_read(self):
        self.cancelled.set()
        with patch("solo_distill.extractor.open_root",
                   side_effect=AssertionError("must reject before opening source")):
            with self.assertRaises(EvidenceBlocked):
                self.prepare()
        self.cancelled.clear()
        with patch("solo_distill.extractor.open_root",
                   side_effect=AssertionError("must reject before opening source")):
            with self.assertRaises(EvidenceBlocked):
                self.prepare(deadline=time.monotonic() - 1)

    def test_external_guard_is_checked_before_source_access(self):
        def stop():
            raise EvidenceBlocked("external_guard")
        with patch("solo_distill.extractor.open_root",
                   side_effect=AssertionError("guard must run first")):
            with self.assertRaisesRegex(EvidenceBlocked, "external_guard"):
                self.prepare(tick=stop)

    def test_cancel_during_scan_blocks_preparation(self):
        from solo_distill.extractor import parse_structure
        def cancel_after_parse(*args):
            value = parse_structure(*args)
            self.cancelled.set()
            return value
        with patch("solo_distill.extractor.parse_structure", side_effect=cancel_after_parse):
            with self.assertRaises(EvidenceBlocked):
                self.prepare()

    def test_source_changes_and_cancellation_block_final_reverification(self):
        result = self.prepare()
        (self.root / "sample.py").write_bytes(self.files["sample.py"] + b"# changed\n")
        with self.assertRaises(EvidenceBlocked):
            result.verify_sources()
        (self.root / "sample.py").write_bytes(self.files["sample.py"])
        result = self.prepare()
        self.cancelled.set()
        with self.assertRaises(EvidenceBlocked):
            result.verify_sources()

    def test_secret_and_required_zero_hits_never_produce_core(self):
        (self.root / "unselected.py").write_bytes(b"api_key = 'sensitive-token-123456789'\n")
        with self.assertRaisesRegex(EvidenceBlocked, "possible_literal_secret"):
            self.prepare()
        (self.root / "unselected.py").unlink()
        dimensions = [{**self.dimensions[0], "question": "absentuniquesymbol"}]
        with self.assertRaises((ValueError, EvidenceBlocked)):
            self.prepare(dimensions=dimensions)

    def test_scope_cannot_expand_project_authorization(self):
        narrowed = Project.from_config("fixture", configuration(self.root, include=["sample.py"]))
        with self.assertRaises((ValueError, EvidenceBlocked)):
            self.prepare(project=narrowed, include=(".",))
        result = self.prepare(project=narrowed, include=("sample.py",))
        self.assertEqual(result.raw_files, {"sample.py": self.files["sample.py"]})
        for invalid in (("../escape",), ("/absolute",), (), ["."], (True,)):
            with self.subTest(include=invalid), self.assertRaises((ValueError, EvidenceBlocked)):
                self.prepare(include=invalid)

    def test_invalid_deadlines_and_guard_contract_are_rejected(self):
        for invalid in (True, None, float("nan"), float("inf"), "30"):
            with self.subTest(deadline=repr(invalid)), self.assertRaises(ValueError):
                self.prepare(deadline=invalid)
        with self.assertRaises(ValueError):
            self.prepare(tick=True)

    def test_preparation_does_not_import_independent_expectation_assets(self):
        root = Path(__file__).resolve().parents[1] / "src" / "solo_distill"
        tree = ast.parse((root / "preparation_v3.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("oracle", node.module or "")
                self.assertFalse((node.module or "").startswith("tests"))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("oracle", alias.name)
                    self.assertFalse(alias.name.startswith("tests"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotIn("fastcode_acceptance.json", node.value)


if __name__ == "__main__":
    unittest.main()
