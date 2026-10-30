"""Budget diagnostics and adaptive harness regression; isolated offline fixtures."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from solo_distill.extractor import Project, Scanner, bounded_result, serialized_size
from solo_distill.server import Server, encode, serve
from solo_distill.priority_v1 import expand_entries

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "adaptive_support", ROOT / "verification/.scratch/step4_adaptive_support_20261026.py")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def config(root, ceiling=24576):
    return {"root": str(root), "include": ["."], "exclude": ["__pycache__"],
            "max_entries": 1000, "max_file_bytes": 1048576, "max_total_bytes": 16777216,
            "max_seconds": 5, "return_bytes": ceiling}


def initialized(project):
    server = Server({"fixture": project})
    server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "budget-fixture", "version": "1"}}})
    server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


def request(budget, request_id=3):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {
        "name": "solo_extract_structure_v1", "arguments": {
            "contract_version": 1, "project_id": "fixture", "return_bytes": budget}}}


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "entry.py").write_text("import os\ndef run():\n return os.getcwd()\n", encoding="utf-8")
        self.project = Project.from_config("fixture", config(self.root))
        self.report = Scanner(self.project, (".",), 5).run()

    def dispatch(self, budget, report=None, request_id=3):
        with patch("solo_distill.extractor.Scanner.run", return_value=copy.deepcopy(report or self.report)):
            return initialized(self.project).dispatch(request(budget, request_id))

    def body(self, response):
        self.assertIn("result", response)
        return json.loads(response["result"]["content"][0]["text"])

    def test_exact_wire_estimate_and_one_byte_boundary(self):
        for request_id in (3, '"\\雪' * 20):
            with self.subTest(request_id=request_id):
                full = self.dispatch(24576, request_id=request_id)
                report = self.body(full)
                size = len(encode(full)) + 1
                self.assertEqual(report["backpressure"]["estimated_full_bytes"], size)
                self.assertFalse(report["partial"])
                exact = self.dispatch(size, request_id=request_id)
                self.assertEqual(full, exact)
                smaller_response = self.dispatch(size - 1, request_id=request_id)
                pressure = self.body(smaller_response)
                self.assertLessEqual(len(encode(smaller_response)) + 1, size - 1)
                self.assertEqual(expand_entries(pressure)[0]["symbols"], self.report["entries"][0]["symbols"])
                self.assertEqual(expand_entries(pressure)[0]["imports"], self.report["entries"][0]["imports"])
                # Lossless factoring may make the complete evidence fit even
                # below the legacy frame boundary; no mandatory record may vanish.
                self.assertEqual(pressure["delivery"]["omitted_counts"]["p0"], 0)
                self.assertEqual(pressure["delivery"]["omitted_counts"]["p1"], 0)

    def test_default_report_measure_preserved_without_mutating_source(self):
        original = copy.deepcopy(self.report)
        result = bounded_result(self.report, 24576)
        self.assertEqual(result["backpressure"]["estimated_full_bytes"], serialized_size(result))
        self.assertEqual(self.report, original)
        for budget in (1024, 2048, 4096):
            reduced = bounded_result(self.report, budget)
            self.assertLessEqual(serialized_size(reduced), budget)
            if reduced["partial"]:
                self.assertEqual(reduced["backpressure"]["reason"], "return_budget")

    def test_hard_top_and_project_ceiling_enforced(self):
        large = copy.deepcopy(self.report)
        # Exercise a genuine mandatory-core overflow after lossless factoring,
        # not the obsolete legacy-envelope limit or duplicated source identities.
        original = large["entries"][0]["symbols"][0]
        large["entries"][0]["symbols"] = [
            {**copy.deepcopy(original), "source_id": f"{index:024x}",
             "qualified_name": f"function_{index}", "name": f"function_{index}"}
            for index in range(400)
        ]
        large["coverage"]["symbols"] = len(large["entries"][0]["symbols"])
        for budget in (1024, 4096, 24576):
            response = self.dispatch(budget, large)
            self.assertLessEqual(len(encode(response)) + 1, budget)
            result = self.body(response)
            pressure = result["backpressure"]
            self.assertTrue(result["partial"])
            self.assertTrue(pressure["hard_limit_exceeded"])
            self.assertFalse(pressure["retryable"])
            self.assertEqual(result["delivery"]["state"], "CORE_BLOCKED")
            self.assertFalse(result["delivery"]["core_complete"])
            self.assertGreater(pressure["estimated_full_bytes"], 24576)
            self.assertEqual(pressure["omitted_count"], 402)
        narrow = Project.from_config("fixture", config(self.root, 4096))
        response = initialized(narrow).dispatch(request(4097))
        self.assertEqual(response["error"]["code"], -32602)

    def test_nonbudget_business_failure_not_retryable(self):
        broken = copy.deepcopy(self.report)
        broken["status"] = "partial"
        broken["unfinished"] = [{"path": "broken.py", "reason": "SyntaxError"}] * 100
        response = self.dispatch(1024, broken)
        report = self.body(response)
        self.assertTrue(report["partial"])
        self.assertFalse(report["backpressure"]["retryable"])
        self.assertEqual(report["backpressure"]["source_status"], "partial")

    def test_actual_writer_preserves_newline_budget(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "budget-fixture", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, request(1024)]
        output = io.BytesIO()
        with patch("solo_distill.extractor.Scanner.run", return_value=copy.deepcopy(self.report)):
            self.assertEqual(serve(Server({"fixture": self.project}),
                                   io.BytesIO(b"".join(encode(item) + b"\n" for item in messages)), output), 0)
        lines = output.getvalue().splitlines(keepends=True)
        self.assertEqual(len(lines), 2)
        self.assertLessEqual(len(lines[-1]), 1024)
        self.assertEqual(json.loads(lines[-1])["id"], 3)


class LoopTests(unittest.TestCase):
    def attempt(self, budget, estimate=None, status="PRESSURE", snapshot="same"):
        pressure = {"reason": "return_budget", "retryable": budget < 24576,
                    "source_status": "complete"}
        if estimate is not None:
            pressure["estimated_full_bytes"] = estimate
        return {"status": status, "budget": budget, "wire_bytes": 8000,
                "snapshot_id": snapshot, "backpressure": pressure, "reasons": []}

    def test_feedback_reaches_sufficient_budget(self):
        calls = []
        def call(budget, number):
            calls.append(budget)
            return self.attempt(budget, 8000, "PASS" if budget >= 8000 else "PRESSURE")
        attempts, baseline, reason = harness.adaptive_loop(call, 4096)
        self.assertEqual(calls, [4096, 8000])
        self.assertEqual(baseline["verified_success_budget"], 8000)
        self.assertIsNone(reason)

    def test_exponential_fallback_and_three_attempt_limit(self):
        calls = []
        def call(budget, number):
            calls.append(budget)
            return self.attempt(budget)
        attempts, baseline, reason = harness.adaptive_loop(call, 4096)
        self.assertEqual(calls, [4096, 8192, 24576])
        self.assertEqual(len(attempts), 3)
        self.assertIsNone(baseline)
        self.assertEqual(reason, "hard_limit_cannot_fit")

    def test_above_cap_jumps_to_cap_and_blocks(self):
        calls = []
        def call(budget, number):
            calls.append(budget)
            return self.attempt(budget, 400000)
        attempts, baseline, reason = harness.adaptive_loop(call, 4096)
        self.assertEqual(calls, [4096, 24576])
        self.assertIsNone(baseline)
        self.assertEqual(reason, "hard_limit_cannot_fit")

    def test_business_failure_and_snapshot_change_stop(self):
        for case in ("failure", "snapshot", "nonbudget"):
            calls = []
            def call(budget, number):
                calls.append(budget)
                value = self.attempt(budget, 8000, snapshot="changed" if number == 2 else "same")
                if case == "failure":
                    value["status"] = "BLOCKED"
                if case == "nonbudget":
                    value["backpressure"]["reason"] = "deadline_exhausted"
                return value
            attempts, baseline, reason = harness.adaptive_loop(call, 4096)
            self.assertIsNone(baseline)
            self.assertEqual(len(calls), 2 if case == "snapshot" else 1)

    def test_initial_and_server_limits(self):
        for value in (True, 1000, 24577):
            with self.assertRaises(ValueError):
                harness.adaptive_loop(lambda *args: self.fail("must not call"), value)
        calls = []
        def call(budget, number):
            calls.append(budget)
            value = self.attempt(budget, 10000)
            value["backpressure"]["retryable"] = budget < 8192
            return value
        attempts, baseline, reason = harness.adaptive_loop(call, 4096, 8192)
        self.assertEqual(calls, [4096, 8192])
        self.assertIsNone(baseline)

    def test_real_fixture_scans_converge_and_preserve_ast(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "entry.py").write_text("import os\n" + "\n".join(
                f"def function_{i}():\n return os.getcwd()" for i in range(24)), encoding="utf-8")
            project = Project.from_config("fixture", config(root))
            calls = []
            def call(budget, number):
                response = initialized(project).dispatch(request(budget))
                report = json.loads(response["result"]["content"][0]["text"])
                calls.append(budget)
                if report["delivery"]["core_complete"]:
                    entry = expand_entries(report)[0]
                    self.assertEqual(len(entry["symbols"]), 24)
                    self.assertEqual(len(entry["imports"]), 1)
                    self.assertEqual(len(entry["calls"]) + report["delivery"]["omitted_counts"]["p3"], 24)
                return {"status": "PASS" if report["delivery"]["core_complete"] else "PRESSURE",
                        "snapshot_id": report["snapshot_id"], "backpressure": report["backpressure"],
                        "wire_bytes": len(encode(response)) + 1, "reasons": []}
            attempts, baseline, reason = harness.adaptive_loop(call, 4096)
            self.assertIsNotNone(baseline, attempts)
            self.assertIsNone(reason)
            self.assertGreater(len(calls), 1)
            self.assertLessEqual(len(calls), 3)
            self.assertLessEqual(baseline["verified_success_budget"], 24576)


if __name__ == "__main__":
    unittest.main()
