"""Priority state/encoding/consumer regressions; isolated offline fixtures only."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from solo_distill.extractor import Project, Scanner, bounded_result, serialized_size
from solo_distill.priority_v1 import expand_entries
from solo_distill.server import encode
from test_backpressure import config, initialized, request

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "core_validation", ROOT / "verification/.scratch/step4_core_validation_20261026.py")
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


class PriorityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "entry.py").write_text(
            "import os\nfrom helper import work\n"
            "class Service:\n def run(self):\n  return work()\n"
            "def start():\n" + " os.getcwd()\n" * 751,
            encoding="utf-8")
        (self.root / "helper.py").write_text("def work():\n return 1\n", encoding="utf-8")
        self.project = Project.from_config("fixture", config(self.root))
        self.report = Scanner(self.project, (".",), 5).run()
        self.assertEqual(self.report["status"], "complete")

    def response(self, budget, report=None, rpc_id=3):
        with patch("solo_distill.extractor.Scanner.run", return_value=copy.deepcopy(report or self.report)):
            response = initialized(self.project).dispatch(request(budget, rpc_id))
        self.assertIn("result", response, response)
        body = json.loads(response["result"]["content"][0]["text"])
        return response, body

    def test_local_call_flood_preserves_all_core_and_exits_success(self):
        response, body = self.response(24576)
        entries = expand_entries(body)
        self.assertEqual(sum(len(e.get("symbols", [])) for e in entries), 4)
        self.assertEqual(sum(len(e.get("imports", [])) for e in entries), 2)
        self.assertTrue(body["delivery"]["core_complete"])
        self.assertTrue(body["partial"])
        self.assertGreater(body["delivery"]["omitted_counts"]["p3"], 0)
        self.assertEqual(body["delivery"]["omitted_counts"]["p0"], 0)
        self.assertEqual(body["delivery"]["omitted_counts"]["p1"], 0)
        self.assertLessEqual(len(encode(response)) + 1, 24576)
        value = oracle.assess_structure(body, False, ["entry.py", "helper.py"], len(encode(response)) + 1, 24576)
        self.assertEqual(value["status"], "PASS")
        self.assertEqual(value["delivered_symbols"], 4)
        self.assertFalse(body["backpressure"]["retryable"])
        self.assertLess(body["backpressure"]["estimated_full_bytes"], 24576)

    def test_core_roundtrip_is_exact_including_methods_and_provenance(self):
        original = copy.deepcopy(self.report)
        _, body = self.response(24576)
        decoded = expand_entries(body)
        for source, delivered in zip(self.report["entries"], decoded):
            if source["status"] == "parsed":
                self.assertEqual(source["symbols"], delivered["symbols"])
                self.assertEqual(source["imports"], delivered["imports"])
                raw_calls = {r["source_id"]: r for r in source["calls"]}
                for call in delivered["calls"]:
                    self.assertEqual(call, raw_calls[call["source_id"]])
        self.assertEqual(original, self.report)

    def test_monotone_budgets_never_remove_core_after_first_fit(self):
        seen_core = False
        previous = 0
        for budget in (1024, 2048, 4096, 8192, 16384, 24576):
            response, body = self.response(budget)
            self.assertLessEqual(len(encode(response)) + 1, budget)
            complete = body["delivery"]["core_complete"]
            if seen_core:
                self.assertTrue(complete)
            if complete:
                self.assertEqual(body["delivery"]["delivered_counts"]["p0"], 2)
                self.assertEqual(body["delivery"]["delivered_counts"]["p1"], 4)
                calls = sum(body["delivery"]["delivered_counts"][k] for k in ("p2", "p3"))
                self.assertGreaterEqual(calls, previous)
                previous = calls
                seen_core = True
        self.assertTrue(seen_core)

    def test_optional_condition_flood_cannot_inflate_core(self):
        report = copy.deepcopy(self.report)
        for entry in report["entries"]:
            for index, call in enumerate(entry.get("calls", [])):
                call["conditions"] = ["if:" + str(index) + ":" + "x" * 1500]
        response, body = self.response(8192, report)
        self.assertTrue(body["delivery"]["core_complete"])
        self.assertLessEqual(len(encode(response)) + 1, 8192)
        self.assertLess(body["backpressure"]["estimated_full_bytes"], 8192)

    def test_p2_precedes_local_calls_and_never_becomes_runtime_graph(self):
        report = copy.deepcopy(self.report)
        calls = report["entries"][0]["calls"]
        calls[-1]["caller"] = "<module>"
        calls[-1]["expression"] = "start"
        _, body = self.response(8192, report)
        self.assertEqual(body["delivery"]["delivered_counts"]["p2"], 1)
        decoded = expand_entries(body)
        self.assertTrue(any(r["caller"] == "<module>" for r in decoded[0]["calls"]))
        self.assertEqual(body["graph"]["call_graph"], "not_resolved")
        self.assertTrue(all(r["runtime_execution"] == "unknown" for e in decoded for r in e.get("calls", [])))

    def test_true_core_overflow_blocks_without_expansion_past_cap(self):
        report = copy.deepcopy(self.report)
        report["entries"][0]["symbols"][0]["qualified_name"] = "x" * 30000
        response, body = self.response(24576, report)
        self.assertEqual(body["delivery"]["state"], "CORE_BLOCKED")
        self.assertFalse(body["delivery"]["core_complete"])
        self.assertFalse(body["backpressure"]["retryable"])
        self.assertTrue(body["backpressure"]["hard_limit_exceeded"])
        self.assertEqual(body["backpressure"]["estimate_scope"], "core_p0_p1")
        self.assertTrue(response["result"]["isError"])
        self.assertLessEqual(len(encode(response)) + 1, 24576)

    def test_business_failure_cannot_be_core_success(self):
        report = copy.deepcopy(self.report)
        report["status"] = "partial"
        report["unfinished"] = [{"path": "broken.py", "reason": "SyntaxError"}]
        response, body = self.response(24576, report)
        self.assertFalse(body["delivery"]["core_complete"])
        self.assertFalse(body["backpressure"]["retryable"])
        with self.assertRaises(ValueError):
            oracle.assess_structure(body, response["result"]["isError"], ["entry.py", "helper.py"], len(encode(response)) + 1, 24576)

    def test_nonzero_but_incomplete_core_fails_oracle(self):
        response, body = self.response(24576)
        mutated = copy.deepcopy(body)
        mutated["entries"][0]["symbols"].pop()
        with self.assertRaisesRegex(ValueError, "mandatory_core_incomplete"):
            oracle.assess_structure(mutated, False, ["entry.py", "helper.py"], len(encode(response)) + 1, 24576)

    def test_dependency_edges_preserve_every_import_and_only_selected_targets(self):
        _, body = self.response(24576)
        graph = body["graph"]
        self.assertEqual(len(graph["edges"]), 2)
        self.assertEqual(graph["edges"][0], [0, 0, None])
        self.assertEqual(graph["edges"][1], [0, 1, 1])
        self.assertEqual(graph["direction"], "importer_to_module")

    def test_corrupt_condition_reference_or_graph_source_rejected(self):
        response, body = self.response(24576)
        corrupt = copy.deepcopy(body)
        corrupt["entries"][0]["symbols"][0][-1] = [999]
        with self.assertRaisesRegex(ValueError, "dangling_condition_reference"):
            expand_entries(corrupt)
        corrupt = copy.deepcopy(body)
        corrupt["graph"]["edges"][0][1] = 999
        with self.assertRaisesRegex(ValueError, "dangling_dependency_source"):
            oracle.assess_structure(corrupt, False, ["entry.py", "helper.py"], len(encode(response)) + 1, 24576)


if __name__ == "__main__":
    unittest.main()
