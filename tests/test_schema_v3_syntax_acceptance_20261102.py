"""当前语法窗口验收支撑的隔离正负控；不联网、不读取真实来源、不保存业务。

只模拟包外输入和依赖边界；不冒充真实双消费或人工审核通过。
"""
from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import mainline_a_schema_v3_syntax_acceptance_20261102 as acceptance


class SyntaxBindingTests(unittest.TestCase):
    def setUp(self):
        support = acceptance.support
        self.namespace = patch.multiple(support, SYNTAX=True,
            REQUEST_ID=acceptance.syntax_support.REQUEST,
            FROZEN_SAMPLE="preflight-coverage-20261102T050022Z-1",
            LIVE="phase5-live-syntax-20261102T085352Z",
            RECOVERY_MARKER="verification/.scratch/fixture-syntax-marker.full.json")
        self.namespace.start()
        self.addCleanup(self.namespace.stop)

    def fixture(self):
        support = acceptance.support
        paths = {"plan_sha256": support.PLAN, "execution_contract_sha256": support.CONTRACT,
            "adr_sha256": "_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md",
            "projects_sha256": "config/projects.json", "policy_sha256": "config/distillation.json"}
        files = {path: ("fixture:" + path).encode() for path in paths.values()}
        bound = {key: support.digest(files[path]) for key, path in paths.items()}
        bound.update(plan_contract_version=6, adr_revision=16,
                     execution_contract_revision=17, session_id=None)
        receipt = {"status": "online_transport_saved_trusted_consumption_pending", "failures": [],
            "execution_instance": support.INSTANCE, "request_id": acceptance.syntax_support.REQUEST,
            "service_exit_code": 0, "service_cleanup": "confirmed_natural_exit",
            "business_call_attempts": 1, "binding": bound}
        return files, receipt

    def verify(self, files, receipt, mutation=None):
        support = acceptance.support
        raw = json.dumps(receipt).encode()
        files = dict(files)
        files[support.PARENT + "/" + support.LIVE + ".receipt.json"] = raw
        files[support.RECOVERY_MARKER] = b'{"fixture_marker":true}'
        if mutation is not None:
            files[mutation] = b"changed"
        with patch.object(acceptance, "LIVE_RECEIPT_SHA256", support.digest(raw)), \
             patch.object(support, "read", side_effect=lambda path: files[path]), \
             patch.object(support, "binding", side_effect=AssertionError("不得重新准入")), \
             patch.object(support, "verify_prior_preservation") as prior, \
             patch.object(acceptance.syntax_support, "verify_marker") as marker:
            result = acceptance.binding()
            prior.assert_called_once_with(receipt["binding"])
            marker.assert_called_once_with(support, {"fixture_marker": True}, receipt["binding"])
            return result

    def test_current_bound_receipt_checks_prior_and_marker_without_readmission(self):
        files, receipt = self.fixture()
        self.assertEqual(self.verify(files, receipt), receipt)

    def test_any_bound_file_change_stops(self):
        files, receipt = self.fixture()
        for path in files:
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "binding_changed"):
                    self.verify(files, receipt, mutation=path)

    def test_old_revision_bool_or_invented_session_cannot_be_rebound(self):
        for field, value in (("plan_contract_version", 5), ("adr_revision", 13),
                ("execution_contract_revision", 14), ("adr_revision", True),
                ("session_id", "invented")):
            with self.subTest(field=field, value=value):
                files, receipt = self.fixture()
                receipt["binding"][field] = value
                with self.assertRaisesRegex(ValueError, "binding_version"):
                    self.verify(files, receipt)

    def test_failure_uncertain_exit_replay_or_wrong_request_stops(self):
        for field, value in (("status", "blocked"), ("failures", ["failure"]),
                ("service_exit_code", 2), ("service_exit_code", False),
                ("service_cleanup", "unknown"), ("business_call_attempts", 2),
                ("business_call_attempts", True), ("request_id", "old-window")):
            with self.subTest(field=field):
                files, receipt = self.fixture()
                receipt[field] = value
                with self.assertRaisesRegex(ValueError, "live_not_complete"):
                    self.verify(files, receipt)

    def test_wrong_namespace_and_receipt_digest_stop_before_acceptance(self):
        with patch.object(acceptance.support, "SYNTAX", False), \
             patch.object(acceptance.support, "read") as read:
            with self.assertRaisesRegex(ValueError, "explicit_syntax_namespace"):
                acceptance.binding()
            read.assert_not_called()
        with patch.object(acceptance.support, "read", return_value=b"changed"):
            with self.assertRaisesRegex(ValueError, "live_receipt_changed"):
                acceptance.binding()


class SyntaxFreshContextTests(unittest.TestCase):
    def fixture(self):
        report = {"snapshot_id": "snapshot", "coverage": {"parsed_files": 1},
                  "observations": {"elapsed_seconds": 1, "source_writes": 0}}
        selection = {"core_unit_ids": ["unit"], "selection_id": "choice"}
        raw_files = {"fixture.py": b"x = 1\n"}
        frozen = {"include": ["fixture.py"], "question": "fixture question",
            "dimensions": [{"id": "fixture", "required": True}], "report": report,
            "selection": selection, "raw_files_base64": {
                path: base64.b64encode(raw).decode() for path, raw in raw_files.items()}}
        arguments = {"project_id": "fixture-project", "selection_version": acceptance.VERSION,
            "include": frozen["include"], "question": frozen["question"],
            "dimensions": frozen["dimensions"], "compressed_frozen_selection": {"fixture": True}}
        core = {"fixture": "core"}
        prepared = SimpleNamespace(report=copy.deepcopy(report), selection=copy.deepcopy(selection),
            raw_files=raw_files, messages=[], post=b'{"model":"fixture"}',
            core=SimpleNamespace(document=core))
        trace = {"report": copy.deepcopy(report), "selection": copy.deepcopy(selection), "core": core}
        return arguments, trace, frozen, prepared

    def run_context(self, arguments, trace, frozen, prepared, unpacked=None):
        guard = SimpleNamespace(deadline=123, check=Mock())
        project = SimpleNamespace(project_id="fixture-project")
        with patch.object(acceptance, "load_projects", return_value={"fixture-project": project}), \
             patch.object(acceptance, "prepare_snapshot", return_value=prepared) as production, \
             patch.object(acceptance, "expected_selection", return_value=(
                 prepared.messages, prepared.post, frozen["selection"])) as independent, \
             patch.object(acceptance, "unpack_selection", return_value=(
                 frozen["selection"] if unpacked is None else unpacked)) as ingress:
            result = acceptance.fresh_context(arguments, trace, frozen, guard)
            self.assertEqual(production.call_args.kwargs["selection_version"], acceptance.VERSION)
            self.assertEqual(independent.call_args.kwargs["selection_version"], acceptance.VERSION)
            self.assertEqual(production.call_args.kwargs["frozen_selection"], frozen["selection"])
            ingress.assert_called_once_with(arguments["compressed_frozen_selection"], tick=guard.check)
            return result

    def test_fixed_caller_version_reaches_both_recomputations_and_full_ingress(self):
        args, trace, frozen, prepared = self.fixture()
        before = copy.deepcopy((args, trace, frozen))
        prepared.report["observations"]["elapsed_seconds"] = 2
        result = self.run_context(args, trace, frozen, prepared)
        self.assertEqual(result[-1], b'{"model":"fixture","temperature":0.2,"max_tokens":3072}')
        self.assertEqual((args, trace, frozen), before)

    def test_missing_old_or_unknown_version_stops_before_scan(self):
        for value in (None, "question-scout-v1", "unknown", True):
            args, trace, frozen, prepared = self.fixture()
            args["selection_version"] = value
            with patch.object(acceptance, "load_projects") as projects:
                with self.assertRaisesRegex(ValueError, "explicit_selection_version"):
                    acceptance.fresh_context(args, trace, frozen, Mock())
                projects.assert_not_called()

    def test_full_source_selection_structure_or_observation_drift_stops(self):
        for field in ("source", "selection", "core", "observation", "include"):
            with self.subTest(field=field):
                args, trace, frozen, prepared = self.fixture()
                if field == "source":
                    prepared.raw_files = {"fixture.py": b"x = 2\n"}
                elif field == "selection":
                    prepared.selection = {"selection_id": "changed"}
                elif field == "core":
                    trace["core"] = {"fixture": "changed"}
                elif field == "observation":
                    prepared.report["observations"]["source_writes"] = 1
                else:
                    args["include"] = ["changed.py"]
                with self.assertRaisesRegex(ValueError, "conflict|changed"):
                    self.run_context(args, trace, frozen, prepared)

    def test_full_ingress_difference_stops(self):
        args, trace, frozen, prepared = self.fixture()
        with self.assertRaisesRegex(ValueError, "independent_ingress_conflict"):
            self.run_context(args, trace, frozen, prepared, unpacked={"selection_id": "changed"})


class SyntaxDualBoundaryTests(unittest.TestCase):
    def test_both_consumers_receive_same_external_version_and_inputs(self):
        trusted = {"selection_version": acceptance.VERSION, "failures": [], "post": b"fixture-post"}
        before = copy.deepcopy(trusted)
        candidate, raw_files, publication = {"fixture": "candidate"}, {"fixture.py": b"x=1"}, {"status": "complete"}
        production = SimpleNamespace(publication=publication, status="complete")
        independent = {"publication": copy.deepcopy(publication), "task_status": "complete"}
        with patch.object(acceptance, "consume_frame", return_value=production) as prod, \
             patch.object(acceptance, "independent_receive", return_value=independent) as oracle:
            result = acceptance.dual_receive(b"fixture-wire", raw_files, candidate, trusted)
            self.assertEqual(result[0], publication)
            for call in (prod.call_args, oracle.call_args):
                self.assertEqual(call.args[:2], (b"fixture-wire", raw_files))
                self.assertEqual(call.kwargs["candidate"], candidate)
                self.assertEqual(call.kwargs["selection_version"], acceptance.VERSION)
                self.assertEqual(call.kwargs["failures"], [])
                self.assertEqual(call.kwargs["post"], b"fixture-post")
            self.assertEqual(trusted, before)

    def test_either_publication_or_required_task_status_mismatch_stops(self):
        for publication, status in (({"changed": True}, "complete"), ({}, "incomplete")):
            with patch.object(acceptance, "consume_frame", return_value=SimpleNamespace(publication={}, status="complete")), \
                 patch.object(acceptance, "independent_receive", return_value={"publication": publication, "task_status": status}):
                with self.assertRaisesRegex(ValueError, "dual_consumers_or_required_task_status"):
                    acceptance.dual_receive(b"wire", {}, {}, {"selection_version": acceptance.VERSION})

    def test_missing_explicit_version_does_not_call_either_consumer(self):
        with patch.object(acceptance, "consume_frame") as prod, \
             patch.object(acceptance, "independent_receive") as oracle:
            with self.assertRaisesRegex(ValueError, "dual_explicit_selection_version"):
                acceptance.dual_receive(b"wire", {}, {}, {})
            prod.assert_not_called()
            oracle.assert_not_called()


class SyntaxAttemptVerificationTests(unittest.TestCase):
    def fixture(self):
        post = b"fixture-post"
        attempts, assets = [], {}
        reasons = ["upstream_stream_error", "invalid_candidate_json_syntax", "invalid_or_unsent_source_id", None]
        for index, reason in enumerate(reasons, 1):
            raw = ("fixture-sse-" + str(index)).encode()
            item = {"raw_sse_base64": base64.b64encode(raw).decode(), "raw_sse_bytes": len(raw),
                "raw_sse_sha256": acceptance.support.digest(raw), "message_bytes": len(post),
                "message_sha256": acceptance.support.digest(post), "absolute_deadline": 123,
                "attempt_index": index, "client_auto_retries": index - 1}
            if index == 1:
                item["error_reason"] = reason
            elif reason is not None:
                item["candidate_error_reason"] = reason
            attempts.append(item)
            assets["sse_attempt_" + str(index)] = raw
            assets["transport_attempt_" + str(index)] = b"fixture-copy"
            if index in (2, 3):
                assets["candidate_rejection_" + str(index)] = b"fixture-rejection"
        assets["sse"] = assets["sse_attempt_4"]
        receipt = {"actual_http_attempts": 4, "client_auto_retries": 3,
                   "assets": {key: {"fixture": key} for key in assets}}
        return receipt, assets, {"gateway_requests": attempts}, post

    def verify(self, receipt, assets, trace, post):
        with patch.object(acceptance.syntax_support, "verify_attempt") as copies, \
             patch.object(acceptance.support, "retry_allowed", return_value=True) as retries, \
             patch.object(acceptance.support, "require_gateway_completion"):
            result = acceptance.verify_attempts(receipt, assets, trace, post, SimpleNamespace(check=Mock()))
            self.assertEqual(copies.call_count, 4)
            self.assertEqual(retries.call_count, 3)
            return result

    def test_mixed_four_attempts_delegate_exact_saved_records_and_keep_inputs(self):
        receipt, assets, trace, post = self.fixture()
        before = copy.deepcopy((receipt, assets, trace))
        self.assertEqual(self.verify(receipt, assets, trace, post), trace["gateway_requests"])
        self.assertEqual((receipt, assets, trace), before)

    def test_shared_count_deadline_post_index_or_raw_sse_conflict_stops(self):
        for field in ("count", "deadline", "post", "index", "raw"):
            with self.subTest(field=field):
                receipt, assets, trace, post = self.fixture()
                item = trace["gateway_requests"][1]
                if field == "count":
                    receipt["client_auto_retries"] = 2
                elif field == "deadline":
                    item["absolute_deadline"] += 1
                elif field == "post":
                    item["message_sha256"] = "changed"
                elif field == "index":
                    item["attempt_index"] = 3
                else:
                    assets["sse_attempt_2"] = b"changed"
                with self.assertRaisesRegex(ValueError, "shared_retry_count|attempt_binding"):
                    self.verify(receipt, assets, trace, post)

    def test_missing_transport_or_rejection_copy_stops_without_saving(self):
        for key in ("transport_attempt_2", "candidate_rejection_2"):
            receipt, assets, trace, post = self.fixture()
            del receipt["assets"][key]
            with patch.object(acceptance.support, "save", side_effect=AssertionError("不得补写原证据")):
                with self.assertRaisesRegex(ValueError, "copy_missing"):
                    self.verify(receipt, assets, trace, post)

    def test_final_rejection_or_extra_attempt_stops(self):
        receipt, assets, trace, post = self.fixture()
        trace["gateway_requests"][-1]["candidate_error_reason"] = "invalid_candidate_json_syntax"
        receipt["assets"]["candidate_rejection_4"] = {"fixture": True}
        with self.assertRaisesRegex(ValueError, "final_candidate_rejected"):
            self.verify(receipt, assets, trace, post)
        receipt, assets, trace, post = self.fixture()
        trace["gateway_requests"].append(copy.deepcopy(trace["gateway_requests"][-1]))
        with self.assertRaisesRegex(ValueError, "bounded_attempts"):
            self.verify(receipt, assets, trace, post)


if __name__ == "__main__":
    unittest.main()
