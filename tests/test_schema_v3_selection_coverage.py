"""新选择版本生产差分及准备传播；不证明在线、锚点或人工审核。

期望来自独立资产，生产代码不得导入这些资产；旧版回归另保留。
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from schema_v3_core_oracle import expected_core
from schema_v3_selection_oracle import expected_selection
from test_schema_v3_selection_coverage_oracle import (
    GENERATION, PARSER, IndependentCoverageOracleTests, context, resign,
)
from test_schema_v3_preparation import SnapshotPreparationTests
from test_schema_v3_stdio import Fixture
from solo_distill.evidence import EvidenceBlocked
from solo_distill.live_v2 import query_tokens
from solo_distill.scouting_coverage_v3 import coverage_roles
from solo_distill.scouting_v3 import (
    COVERAGE_VERSION, VERSION, authenticate_selection, select_prompt_v3,
)


class ProductionCoverageTests(IndependentCoverageOracleTests):
    def choose(self, files, **changes):
        expected = super().choose(files, **changes)
        payload, expected_post, expected_frozen, trusted = expected
        with patch("socket.socket", side_effect=AssertionError("offline only")), \
                patch("subprocess.Popen", side_effect=AssertionError("source is data")), \
                patch("solo_distill.package_v2.build_package",
                      side_effect=AssertionError("no full legacy knowledge package")):
            messages, post, frozen = select_prompt_v3(files, **trusted)
            self.assertEqual(json.loads(messages[-1]["content"]), payload)
            self.assertEqual(post, expected_post)
            self.assertEqual(frozen, expected_frozen)
            self.assertEqual(authenticate_selection(frozen, files, **trusted), frozen)
        return expected

    def roles(self, source):
        import ast
        return coverage_roles(ast.parse(source).body[0], query_tokens, lambda: None)

    def test_production_default_old_and_explicit_versions(self):
        files = {"sample.py": GENERATION.encode()}
        trusted = context(files)
        trusted.pop("selection_version")
        implicit = select_prompt_v3(files, **trusted)
        explicit = select_prompt_v3(files, **trusted, selection_version=VERSION)
        self.assertEqual(implicit, explicit)
        self.assertEqual(implicit, expected_selection(files, **trusted))
        updated = select_prompt_v3(files, **trusted, selection_version=COVERAGE_VERSION)
        self.assertEqual(updated, expected_selection(
            files, **trusted, selection_version=COVERAGE_VERSION))
        self.assertEqual(implicit[:2], updated[:2])
        self.assertNotEqual(implicit[2]["selection_id"], updated[2]["selection_id"])
        self.assertEqual(implicit[2]["input_id"], updated[2]["input_id"])

    def test_production_unknown_versions_do_not_fall_back(self):
        files = {"sample.py": GENERATION.encode()}
        for version in ("question-scout-v3", "", None, True, [], {}):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "unknown_version"):
                select_prompt_v3(files, **context(files, selection_version=version))

    def test_production_resigned_complete_witness_and_trusted_version_conflicts(self):
        files = {"sample.py": GENERATION.encode()}
        trusted = context(files)
        _, _, frozen = select_prompt_v3(files, **trusted)
        mutations = [
            lambda value: value.update(selection_version=VERSION),
            lambda value: value["witness"].update(version=VERSION),
            lambda value: value["witness"].update(duties=[]),
            lambda value: value["witness"]["selected_evidence"][0].update(roles=[]),
            lambda value: value.update(core_unit_ids=[]),
            lambda value: value["witness"].update(allowed_source_ids=[]),
        ]
        for mutate in mutations:
            value = copy.deepcopy(frozen)
            mutate(value)
            resign(value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "witness_or_trusted_input_conflict"):
                authenticate_selection(value, files, **trusted)
        with self.assertRaisesRegex(ValueError, "witness_or_trusted_input_conflict"):
            authenticate_selection(frozen, files, **{**trusted, "selection_version": VERSION})

    def test_production_missing_joint_generation_boundary_blocks(self):
        source = ("def generate(context):\n if input_tokens > max_tokens:\n"
                  "  return provider.generate(context)\n return context\n")
        files = {"sample.py": source.encode()}
        with self.assertRaisesRegex(EvidenceBlocked, "required_question_duty_unresolved"):
            select_prompt_v3(files, **context(files))


class CoveragePreparationTests(unittest.TestCase):
    # 复用隔离夹具和准备调用助手，不重复继承运行整组旧版测试。
    setUp = SnapshotPreparationTests.setUp
    prepare = SnapshotPreparationTests.prepare

    def coverage_fixture(self):
        self.files["sample.py"] = (GENERATION + PARSER).encode()
        (self.root / "sample.py").write_bytes(self.files["sample.py"])
        self.question = "Explain generation and parser boundaries without execution."
        self.dimensions = [
            {"id": "generation", "question": "Explain generation exception handling and input/context limits.",
             "required": True, "evidence": "sourced_inference"},
            {"id": "parsing", "question": "Explain parser support and language boundaries.",
             "required": True, "evidence": "sourced_inference"},
        ]

    def test_explicit_version_reaches_selection_core_and_exact_post(self):
        self.coverage_fixture()
        prepared = self.prepare(selection_version=COVERAGE_VERSION)
        trusted = {"project_id": "fixture", "snapshot_id": prepared.report["snapshot_id"],
                   "question": self.question, "dimensions": self.dimensions,
                   "frozen_coverage": prepared.report["coverage"],
                   "selection_version": COVERAGE_VERSION}
        messages, post, selection = expected_selection(self.files, **trusted)
        self.assertEqual(prepared.messages, messages)
        self.assertEqual(prepared.post, post)
        self.assertEqual(prepared.selection, selection)
        core = expected_core(self.files, project_id="fixture",
            snapshot_id=prepared.report["snapshot_id"],
            required_core_unit_ids=selection["core_unit_ids"],
            selection_id=selection["selection_id"])
        self.assertEqual(prepared.core.document, core)
        second = self.prepare(selection_version=COVERAGE_VERSION, frozen_selection=selection)
        self.assertEqual(second.post, post)
        self.assertEqual(second.core.payload, prepared.core.payload)
        prepared.verify_sources()
        second.verify_sources()

    def test_frozen_self_report_cannot_enable_new_version(self):
        self.coverage_fixture()
        updated = self.prepare(selection_version=COVERAGE_VERSION)
        with self.assertRaisesRegex(EvidenceBlocked, "frozen_selection_conflict"):
            self.prepare(frozen_selection=updated.selection)
        old = self.prepare()
        with self.assertRaisesRegex(EvidenceBlocked, "frozen_selection_conflict"):
            self.prepare(selection_version=COVERAGE_VERSION, frozen_selection=old.selection)

    def test_unknown_version_rejected_before_source_access(self):
        for version in ("question-scout-v3", "", None, True, [], {}):
            with self.subTest(version=version), \
                    patch("solo_distill.preparation_v3.Scanner",
                          side_effect=AssertionError("invalid version must not scan")), \
                    self.assertRaisesRegex(ValueError, "unknown_selection_version"):
                self.prepare(selection_version=version)


class CoverageBoundaryTests(Fixture):
    """包外显式版本沿任务、服务、发布和双消费传播的隔离回归。"""

    def setUp(self):
        super().setUp()
        self.files["sample.py"] = (GENERATION + PARSER).encode()
        (self.root / "source/sample.py").write_bytes(self.files["sample.py"])
        self.question = "Explain generation and parser boundaries without execution."
        self.dimensions = [
            {"id": "generation", "question": "Explain generation exception handling and input/context limits.",
             "required": True, "evidence": "sourced_inference"},
            {"id": "parsing", "question": "Explain parser support and language boundaries.",
             "required": True, "evidence": "sourced_inference"},
        ]

    def prepared(self, version=COVERAGE_VERSION):
        import threading
        import time
        from solo_distill.preparation_v3 import prepare_snapshot
        with patch("socket.socket", side_effect=AssertionError("offline only")), \
                patch("subprocess.Popen", side_effect=AssertionError("source is data")):
            return prepare_snapshot(project=self.project, include=(".",),
                question=self.question, dimensions=self.dimensions,
                deadline=time.monotonic() + 10, cancelled=threading.Event(),
                selection_version=version)

    def external_context(self, prepared):
        trusted = {"project_id": "fixture", "snapshot_id": prepared.report["snapshot_id"],
                   "question": self.question, "dimensions": self.dimensions,
                   "frozen_coverage": prepared.report["coverage"],
                   "selection_version": COVERAGE_VERSION}
        messages, post, selection = expected_selection(self.files, **trusted)
        self.assertEqual(selection, prepared.selection)
        self.assertEqual(messages, prepared.messages)
        self.assertEqual(post, prepared.post)
        return {**trusted, "required_core_unit_ids": selection["core_unit_ids"],
                "selection_id": selection["selection_id"], "request_id": "coverage-boundary",
                "post": post, "failures": []}

    def candidate(self, prepared):
        records = json.loads(prepared.messages[-1]["content"])["sources"]
        by_symbol = {row["symbol"]: row["source_id"] for row in records}
        return {"summary": "隔离夹具的有来源推断，不代表真实人工审核。",
                "conclusions": [
                    {"dimension_id": "generation", "text": "生成入口保留限额比较及异常处理正文。",
                     "source_ids": [by_symbol["generate"]]},
                    {"dimension_id": "parsing", "text": "解析入口保留分派分支正文。",
                     "source_ids": [by_symbol["parse_file"]]},
                ], "unknowns": []}

    def test_task_and_validation_match_independent_explicit_version(self):
        from schema_v3_task_oracle import expected_task
        from solo_distill.task_v3 import build_task, validate_task
        prepared = self.prepared()
        trusted = self.external_context(prepared)
        candidate = self.candidate(prepared)
        with patch("socket.socket", side_effect=AssertionError("offline only")):
            task = build_task(candidate, prepared.core, self.files, **trusted)
            validation_context = {key: value for key, value in trusted.items() if key != "failures"}
            self.assertEqual(validate_task(task, prepared.core, self.files,
                                          **validation_context), task)
            self.assertEqual(task, expected_task(candidate, self.files, **trusted))
        self.assertEqual(task["status"], "complete")
        self.assertTrue(all(fact["semantic_support"] == "requires_independent_review"
                            for fact in task["facts"]))

    def test_controlled_post_publishes_and_dual_consumes_with_external_version(self):
        from schema_v3_wire_oracle import receive
        from solo_distill.adaptive_wire import TransportCapabilities
        from solo_distill.consumer_v3 import consume_frame
        from solo_distill.gateway import encode_request
        from solo_distill.task_v3 import build_task
        from solo_distill.wire_v3 import Admission, publish_v3
        prepared = self.prepared()
        trusted = self.external_context(prepared)
        trusted["post"] = encode_request(prepared.messages, generation_controls=True)
        candidate = self.candidate(prepared)
        arguments = self.arguments()
        admission = Admission(arguments["protocol_version"], "A", 3,
            arguments["return_bytes"], TransportCapabilities.from_dict(arguments["transport"]))
        with patch("socket.socket", side_effect=AssertionError("offline only")):
            task = build_task(candidate, prepared.core, self.files, **trusted)
            frame = publish_v3(prepared.core, task, self.files, admission,
                               candidate=candidate, **trusted)
            production = consume_frame(frame.data, self.files, admission=admission,
                                       candidate=candidate, **trusted)
            independent = receive(frame.data, self.files, arguments["transport"],
                protocol_version=arguments["protocol_version"], response_id=3,
                return_bytes=arguments["return_bytes"], candidate=candidate, **trusted)
        self.assertEqual(production.status, "complete")
        self.assertEqual(independent["task_status"], "complete")
        self.assertEqual(production.publication, independent["publication"])

    def test_explicit_new_version_rejects_old_core_in_both_task_implementations(self):
        from schema_v3_task_oracle import expected_task
        from solo_distill.task_v3 import build_task
        old = self.prepared(VERSION)
        trusted = {"project_id": "fixture", "snapshot_id": old.report["snapshot_id"],
                   "required_core_unit_ids": old.selection["core_unit_ids"],
                   "selection_id": old.selection["selection_id"],
                   "request_id": "coverage-boundary", "question": self.question,
                   "dimensions": self.dimensions, "post": old.post,
                   "frozen_coverage": old.report["coverage"],
                   "selection_version": COVERAGE_VERSION}
        candidate = self.candidate(old)
        with self.assertRaisesRegex(ValueError, "selection_version_or_trusted_input_conflict"):
            build_task(candidate, old.core, self.files, **trusted)
        with self.assertRaisesRegex(ValueError, "selection_version_or_trusted_input_conflict"):
            expected_task(candidate, self.files, **trusted)

    def test_service_preflight_propagates_version_to_independent_consumption(self):
        from schema_v3_wire_oracle import receive
        prepared = self.prepared()
        trusted = self.external_context(prepared)
        arguments = self.arguments(request_id=trusted["request_id"],
            question=self.question, dimensions=self.dimensions,
            selection_version=COVERAGE_VERSION, frozen_selection=prepared.selection)
        distiller = self.distiller()
        with patch("socket.socket", side_effect=AssertionError("offline only")):
            frame = distiller.distill_frame(arguments, rpc_id=3)
            trace = self.trace(trusted["request_id"])
            independent = receive(frame.data, self.files, arguments["transport"],
                protocol_version=arguments["protocol_version"], response_id=3,
                return_bytes=arguments["return_bytes"], candidate=trace["candidate"], **trusted)
        self.assertEqual(trace["selection"], prepared.selection)
        self.assertEqual(trace["post_utf8"].encode(), trusted["post"])
        self.assertEqual(trace["gateway_requests"], [])
        self.assertEqual(independent["task_status"], "partial")
        self.assertEqual(independent["publication"]["task_evidence"], trace["task"])

    def test_service_unknown_version_rejected_before_preparation(self):
        from solo_distill.extractor import InputError
        for version in ("question-scout-v3", "", None, True, [], {}):
            with self.subTest(version=version), \
                    patch("solo_distill.service_v3.prepare_snapshot",
                          side_effect=AssertionError("invalid version must not prepare")), \
                    self.assertRaises(InputError):
                self.distiller().distill_frame(self.arguments(selection_version=version), rpc_id=3)

    def test_task_unknown_versions_rejected_by_both_implementations(self):
        from schema_v3_task_oracle import expected_task
        from solo_distill.task_v3 import build_task
        prepared = self.prepared()
        trusted = self.external_context(prepared)
        candidate = self.candidate(prepared)
        for version in ("question-scout-v3", "", None, True, [], {}):
            changed = {**trusted, "selection_version": version}
            with self.subTest(version=version, implementation="production"), \
                    self.assertRaisesRegex(ValueError, "unknown_selection_version"):
                build_task(candidate, prepared.core, self.files, **changed)
            with self.subTest(version=version, implementation="independent"), \
                    self.assertRaisesRegex(ValueError, "unknown_selection_version"):
                expected_task(candidate, self.files, **changed)


if __name__ == "__main__":
    unittest.main()
