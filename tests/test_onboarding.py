"""Focused onboarding selection and evidence-accounting regressions; no live calls."""
from __future__ import annotations

import copy
import json
import threading
import time
import unittest
from unittest.mock import patch

import run_live_integration as live
import test_distillation as contracts
import test_offline as offline
from test_distillation import FakeGateway, arguments
from solo_distill.distillation import tool_response, utf8_json
from solo_distill.evidence import EvidenceReader
from solo_distill.extractor import Scanner


class NavigationGateway(FakeGateway):
    def __init__(self, choose=None):
        super().__init__()
        self.choose = choose or (lambda payload: [next(
            item["candidate_id"] for item in payload["candidates"]
            if item["path"] == "entry.py" and item["symbol"] == "Worker.run")])

    def generate(self, messages, deadline, cancelled):
        payload = json.loads(messages[-1]["content"])
        if payload["operation"] != "navigation":
            return super().generate(messages, deadline, cancelled)
        self.calls.append((payload, deadline))
        return json.dumps({"candidate_ids": self.choose(payload)}), {
            "outer_replays": 0, "finish_reason": "stop", "done_marker": True,
        }


class OnboardingTests(unittest.TestCase):
    setUp = offline.ExtractorTests.setUp
    distiller = contracts.DistillationTests.distiller

    def onboarding(self, **overrides):
        return arguments(scenario="A", seeds=[], question="Map Worker.run and ownership boundaries.",
                         **overrides)

    def reader(self):
        scanner = Scanner(self.project, (".",), 10)
        return EvidenceReader(scanner, scanner.run(), time.monotonic() + 10, threading.Event())

    def test_keyword_hits_still_use_one_navigation_and_one_compression(self):
        gateway = NavigationGateway()
        report = self.distiller(gateway).distill(self.onboarding())
        self.assertEqual(report["status"], "complete", report["unfinished"])
        self.assertEqual([call[0]["operation"] for call in gateway.calls], ["navigation", "compression"])
        self.assertLess(gateway.calls[0][1], gateway.calls[1][1])
        self.assertEqual(report["observations"]["outer_replays"], 0)
        self.assertEqual(report["observations"]["target_writes"], 0)

    def test_required_owners_are_smallest_complete_definitions(self):
        reader = self.reader()
        dimensions = [
            {"required": True, "evidence": "direct_static", "selector": {
                "record_kind": "definition", "path": "entry.py", "qualified_name": "Worker.run"}},
            {"required": True, "evidence": "direct_static", "selector": {
                "record_kind": "call_expression", "path": "entry.py", "expression": "run"}},
            {"required": True, "evidence": "direct_static", "selector": {
                "record_kind": "import", "path": "entry.py", "module": "os", "name": None}},
        ]
        identities = reader.required_candidates(dimensions)
        self.assertEqual([reader.candidates[item]["qualified_name"] for item in identities], ["Worker.run"])
        # Module imports lack a complete definition owner; do not silently promote them.
        self.assertEqual(reader.sources, [])
        self.assertEqual(reader.records, [])

    def test_required_caller_is_reserved_without_navigation_selecting_it(self):
        (self.root / "caller.py").write_text("def bridge():\n    return target()\n")
        gateway = NavigationGateway()
        dimensions = [{"id": "caller", "question": "Locate target call", "required": True,
                       "evidence": "direct_static", "selector": {
                           "record_kind": "call_expression", "path": "caller.py", "expression": "target"}}]
        report = self.distiller(gateway).distill(self.onboarding(dimensions=dimensions))
        self.assertEqual(report["status"], "complete", report["unfinished"])
        self.assertEqual(report["sources"][0]["symbol"], "bridge")
        self.assertEqual(report["relations"][0]["record"]["expression"], "target")
        self.assertEqual(report["relations"][0]["runtime_execution"], "unknown")
        reserved = gateway.calls[0][0]["already_selected_candidate_ids"]
        self.assertEqual(len(reserved), 1)
        self.assertEqual(gateway.calls[0][0]["maximum_additional_candidates"], 31)

    def test_missing_required_owner_remains_partial_even_with_model_prose(self):
        value = self.onboarding()
        value["dimensions"][0]["selector"]["qualified_name"] = "Absent"
        report = self.distiller(NavigationGateway()).distill(value)
        self.assertEqual(report["status"], "partial")
        self.assertIn({"dimension_id": "interface", "reason": "required_evidence_missing"}, report["unfinished"])
        self.assertEqual(report["relations"], [])

    def test_invalid_navigation_never_calls_compression(self):
        for choice in (lambda payload: ["invented"], lambda payload: [],
                       lambda payload: [payload["candidates"][0]["candidate_id"]] * 2):
            with self.subTest(choice=choice):
                gateway = NavigationGateway(choice)
                report = self.distiller(gateway).distill(self.onboarding())
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(len(gateway.calls), 1)
                self.assertIn({"reason": "invalid_navigation_candidates"}, report["unfinished"])

    def test_union_budget_does_not_silently_drop_reserved_or_selected_candidates(self):
        (self.root / "extra.py").write_text("\n".join(f"def item_{i}():\n    return {i}\n" for i in range(32)))
        gateway = NavigationGateway(lambda payload: [item["candidate_id"] for item in payload["candidates"]
                                                     if item["path"] == "extra.py"])
        report = self.distiller(gateway).distill(self.onboarding())
        self.assertEqual(report["status"], "blocked")
        self.assertIn({"reason": "navigation_and_required_block_budget"}, report["unfinished"])
        self.assertEqual(len(gateway.calls), 1)

    def test_all_required_owners_must_fit_block_budget(self):
        (self.root / "many.py").write_text("\n".join(f"def item_{i}():\n    return target()\n" for i in range(33)))
        gateway = NavigationGateway()
        dimension = {"id": "many", "question": "Locate target calls", "required": True,
                     "evidence": "direct_static", "selector": {
                         "record_kind": "call_expression", "path": "many.py", "expression": "target"}}
        report = self.distiller(gateway).distill(self.onboarding(dimensions=[dimension]))
        self.assertEqual(report["status"], "blocked")
        self.assertIn({"reason": "required_candidate_block_budget"}, report["unfinished"])
        self.assertEqual(gateway.calls, [])

    def test_non_python_assets_preserve_inventory_and_every_blind_spot(self):
        (self.root / "readme.md").write_text("Documentation is not parsed Python evidence.\n")
        (self.root / "other.go").write_text("package main\n")
        gateway = NavigationGateway()
        distiller = self.distiller(gateway)
        report = distiller.distill(self.onboarding())
        self.assertEqual(report["status"], "complete", report["unfinished"])
        self.assertEqual(report["coverage"]["file_denominator"], 3)
        self.assertEqual(report["coverage"]["parsed_files"], 1)
        self.assertEqual(report["coverage"]["skipped_files"], 2)
        local = [item for item in report["uncertainties"] if item["reason"] == "unsupported_language_or_asset"]
        self.assertEqual({item["path"] for item in local}, {"readme.md", "other.go"})
        saved = json.loads((distiller.artifact_root / report["full_evidence_trace"]).read_text())
        self.assertEqual(len(saved["structure"]["unfinished"]), 2)
        self.assertEqual(saved["report"]["coverage"], saved["structure"]["coverage"])
        with patch.object(live, "ROOT", distiller.artifact_root):
            self.assertEqual(live.check_scratch(report)["full_uncertainties"], 2)
        self.assertTrue(all("text" not in source for source in report["sources"]))
        self.assertFalse({"source_bodies", "model_outputs", "local_records", "structure"} & report.keys())
        self.assertLessEqual(len(utf8_json(tool_response(report, 3))) + 1, 24576)

    def test_task_retrieval_does_not_reclassify_unsupported_files(self):
        (self.root / "other.go").write_text("package main\n")
        gateway = FakeGateway()
        report = self.distiller(gateway).distill(arguments())
        self.assertEqual(report["status"], "partial")
        self.assertIn({"path": "other.go", "reason": "unsupported_language_or_asset"}, report["unfinished"])
        self.assertEqual(len(gateway.calls), 1)

    def test_parse_failures_and_symlinks_remain_hard_obligations(self):
        (self.root / "broken.py").write_text("def ???\n")
        (self.root / "linked.py").symlink_to(self.root / "entry.py")
        report = self.distiller(NavigationGateway()).distill(self.onboarding())
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["coverage"]["failed_files"], 1)
        self.assertIn({"path": "linked.py", "reason": "symlink_not_followed"}, report["unfinished"])
        self.assertTrue(any(item["path"] == "broken.py" for item in report["unfinished"]))

    def test_scratch_detects_removed_blind_spot_or_changed_denominator(self):
        (self.root / "other.go").write_text("package main\n")
        distiller = self.distiller(NavigationGateway())
        report = distiller.distill(self.onboarding())
        trace = distiller.artifact_root / report["full_evidence_trace"]
        original = json.loads(trace.read_text())
        for problem, expected in (("blind_spot", "scratch_inventory_blind_spots_not_preserved"),
                                  ("denominator", "scratch_inventory_denominator_changed")):
            with self.subTest(problem=problem):
                saved = copy.deepcopy(original)
                if problem == "blind_spot":
                    saved["report"]["uncertainties"] = []
                else:
                    saved["report"]["coverage"]["file_denominator"] = 1
                trace.write_bytes(utf8_json(saved))
                with patch.object(live, "ROOT", distiller.artifact_root):
                    with self.assertRaisesRegex(live.ClientError, expected):
                        live.check_scratch(report)

    def test_scratch_detects_removed_parse_failure_obligation(self):
        (self.root / "broken.py").write_text("def ???\n")
        distiller = self.distiller(NavigationGateway())
        report = distiller.distill(self.onboarding())
        self.assertEqual(report["status"], "partial")
        trace = distiller.artifact_root / report["full_evidence_trace"]
        saved = json.loads(trace.read_text())
        saved["report"]["unfinished"] = []
        trace.write_bytes(utf8_json(saved))
        with patch.object(live, "ROOT", distiller.artifact_root):
            with self.assertRaisesRegex(live.ClientError, "scratch_scan_obligations_not_preserved"):
                live.check_scratch(report)

    def test_non_python_assets_cannot_supply_missing_required_evidence(self):
        (self.root / "other.go").write_text("package main\n")
        value = self.onboarding()
        value["dimensions"][0]["selector"]["path"] = "other.go"
        report = self.distiller(NavigationGateway()).distill(value)
        self.assertEqual(report["status"], "partial")
        self.assertIn({"dimension_id": "interface", "reason": "required_evidence_missing"}, report["unfinished"])
        self.assertEqual(len(report["uncertainties"]), 1)


if __name__ == "__main__":
    unittest.main()
