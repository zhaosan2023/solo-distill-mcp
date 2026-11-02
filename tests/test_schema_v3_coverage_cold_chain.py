"""新选择冷预检的隔离正负控；不占用真实样本或在线标记。

新资产使用固定覆盖恢复命名空间，旧样本及旧默认接口必须保留。
真实 stdio 仅运行隔离来源；通过不证明真实来源、在线或人工审核。
"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

from test_schema_v3_cold_chain import driver
from test_schema_v3_selection_coverage_oracle import GENERATION, PARSER
from test_schema_v3_stdio import Fixture
from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT

VERSION = "question-scout-v2"
SAMPLE = "preflight-coverage-20261102T050022Z-1"


class CoverageColdChainTests(Fixture):
    def coverage_arguments(self, **changes):
        self.files["sample.py"] = (GENERATION + PARSER).encode("utf-8")
        (self.root / "source/sample.py").write_bytes(self.files["sample.py"])
        arguments = self.arguments(
            request_id="coverage-cold-isolated-fixture",
            selection_version=VERSION,
            question="Explain generation and parser boundaries without execution.",
            dimensions=[
                {"id": "generation",
                 "question": "Explain generation exception handling and input/context limits.",
                 "required": True, "evidence": "sourced_inference"},
                {"id": "parsing",
                 "question": "Explain parser support and language boundaries.",
                 "required": True, "evidence": "sourced_inference"},
            ])
        arguments.update(changes)
        return arguments

    def test_new_version_real_isolated_stdio_dual_consumption_and_saved_binding(self):
        module = driver()
        old = self.root / PARENT / "preflight-1"
        old.mkdir()
        (old / "owner.txt").write_bytes(b"old sample must remain unchanged")
        arguments = self.coverage_arguments()
        original = copy.deepcopy(arguments)
        result = module.run_preflight(self.root, arguments, SAMPLE)
        target = self.root / PARENT / SAMPLE
        frozen = json.loads((target / "frozen-source-and-selection.json").read_bytes())
        oracle = json.loads((target / "independent-oracle.receipt.json").read_bytes())
        trace = self.trace(arguments["request_id"])
        self.assertEqual(arguments, original)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["selection_version"], VERSION)
        self.assertEqual(frozen["selection_version"], VERSION)
        self.assertEqual(frozen["selection"]["selection_version"], VERSION)
        self.assertEqual(trace["selection"], frozen["selection"])
        self.assertTrue(oracle["production_and_independent_equal"])
        self.assertEqual(oracle["selection_version"], VERSION)
        self.assertEqual(result["service_exit_code"], 0)
        self.assertEqual(result["service_cleanup"], "confirmed_natural_exit")
        self.assertEqual(result["gateway_requests"], 0)
        self.assertLessEqual(result["request_wire_bytes"], 65536)
        self.assertTrue(result["save"]["exact_readback"])
        self.assertEqual(set(result["save"]["artifact_sha256"]), set(ARTIFACT_NAMES))
        self.assertEqual(json.loads((target / "preflight.receipt.json").read_bytes()), result)
        self.assertEqual((old / "owner.txt").read_bytes(), b"old sample must remain unchanged")
        self.assertEqual((self.root / "source/sample.py").read_bytes(), self.files["sample.py"])

    def test_version_and_sample_namespace_mismatch_rejected_before_service(self):
        module = driver()
        cases = [
            (self.coverage_arguments(), "preflight-1"),
            (self.arguments(), SAMPLE),
            (self.coverage_arguments(), SAMPLE + "-unknown"),
            (self.coverage_arguments(), "../escape"),
            (self.coverage_arguments(selection_version="question-scout-v3"), SAMPLE),
        ]
        with patch.object(module, "StdioClient", side_effect=AssertionError("must not launch")):
            for arguments, sample in cases:
                with self.subTest(sample=sample, version=arguments.get("selection_version")):
                    with self.assertRaises(ValueError):
                        module.run_preflight(self.root, arguments, sample)
        self.assertFalse((self.root / PARENT / SAMPLE).exists())
        self.assertFalse((self.root / PARENT / "preflight-1").exists())

    def test_existing_new_sample_is_not_overwritten_or_replayed(self):
        module = driver()
        target = self.root / PARENT / SAMPLE
        target.mkdir()
        (target / "owner.txt").write_bytes(b"preserve failed or completed scene")
        with patch.object(module, "StdioClient", side_effect=AssertionError("must not launch")):
            with self.assertRaises(FileExistsError):
                module.run_preflight(self.root, self.coverage_arguments(), SAMPLE)
        self.assertEqual((target / "owner.txt").read_bytes(), b"preserve failed or completed scene")
        self.assertEqual(len(list(target.iterdir())), 1)

    def test_resigned_independent_witness_conflict_blocks_before_service(self):
        module = driver()
        independent = module.expected_selection

        def changed(*args, **kwargs):
            messages, post, selection = independent(*args, **kwargs)
            selection = copy.deepcopy(selection)
            selection["witness"]["unique_body_bytes"] += 1
            return messages, post, selection

        with patch.object(module, "expected_selection", side_effect=changed), \
                patch.object(module, "StdioClient", side_effect=AssertionError("must not launch")):
            with self.assertRaisesRegex(ValueError, "independent_selection_conflict"):
                module.run_preflight(self.root, self.coverage_arguments(), SAMPLE)
        self.assertFalse((self.root / PARENT / SAMPLE).exists())

    def test_self_reported_witness_cannot_enable_new_version(self):
        module = driver()
        arguments = self.coverage_arguments()
        arguments.pop("selection_version")
        arguments["frozen_selection"] = {"selection_version": VERSION}
        with patch.object(module, "StdioClient", side_effect=AssertionError("must not launch")):
            with self.assertRaises(ValueError):
                module.run_preflight(self.root, arguments, "preflight-1")
        self.assertFalse((self.root / PARENT / "preflight-1").exists())

    def test_independent_consumer_failure_never_saves_success(self):
        module = driver()
        with patch.object(module, "independent_receive", side_effect=ValueError("coverage_negative")):
            with self.assertRaisesRegex(ValueError, "coverage_negative"):
                module.run_preflight(self.root, self.coverage_arguments(), SAMPLE)
        self.assertFalse((self.root / PARENT / SAMPLE / "preflight.receipt.json").exists())
