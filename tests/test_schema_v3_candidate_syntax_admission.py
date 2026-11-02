"""语法恢复独占接纳正负控；仅临时目录及模拟历史 HTTP，不访问真实在线槽。"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

from solo_distill.gateway import InvalidCandidateSyntax, InvalidCandidateSyntax as SyntaxErrorType
from solo_distill.preflight_v3 import INSTANCE, PARENT
from solo_distill.task_v3 import InvalidSourceCandidate
from test_schema_v3_candidate_admission import READY as COVERAGE_READY
from test_schema_v3_candidate_recovery import CandidateRecoveryTests, REQUEST as PREVIOUS_REQUEST
from test_schema_v3_candidate_syntax_recovery import completed, REQUEST, MARKER, READY
from test_schema_v3_live_retry import TRANSIENT
from test_schema_v3_minimal_recovery import ORIGINAL, digest
from test_schema_v3_stdio import Fixture

PREVIOUS_MARKER = "verification/.scratch/" + INSTANCE + ".live-candidate-20261102T070153Z.full.json"
PREFIX = PARENT + "/phase5-live-candidate-20261102T070153Z."
DIAGNOSTIC = PARENT + "/phase5-candidate-failure-diagnostic-20261102T080521Z.receipt.json"


class CandidateSyntaxAdmissionTests(Fixture):
    fixture = CandidateRecoveryTests.fixture
    live = CandidateRecoveryTests.live
    candidate = CandidateRecoveryTests.candidate
    success = CandidateRecoveryTests.success
    run_streams = CandidateRecoveryTests.run_streams

    def case(self):
        previous, arguments, _, _, paths, _, _ = CandidateRecoveryTests.case(self)
        with self.assertRaises(json.JSONDecodeError):
            self.run_streams(previous, arguments, [TRANSIENT, completed('{"summary":"历史拒收",}')])
        marker_path = self.root / PREVIOUS_MARKER
        marker = json.loads(marker_path.read_bytes())
        ready = json.loads((self.root / COVERAGE_READY).read_bytes())
        ready["wire_arguments"] = copy.deepcopy(arguments)
        ready["binding"].update({key: value for key, value in marker.items() if key.endswith("_sha256")})
        ready_path = self.root / READY
        ready_path.write_text(json.dumps(ready))
        assets = {}
        for key, name, raw in (
                ("admission", "admission.json", marker_path.read_bytes()),
                ("request", "request.json", json.dumps(arguments).encode()),
                ("transport_attempt_1", "transport-attempt-1.json", (
                    self.root / "verification/.scratch" / (PREVIOUS_REQUEST + ".gateway-attempt-1.full.json")).read_bytes()),
                ("transport_attempt_2", "transport-attempt-2.json", (
                    self.root / "verification/.scratch" / (PREVIOUS_REQUEST + ".gateway-attempt-2.full.json")).read_bytes()),
                ("failure", "failure.json", b'{"error_type":"JSONDecodeError"}')):
            target = self.root / (PREFIX + name)
            target.write_bytes(raw)
            assets[key] = {"path": str(target.relative_to(self.root)), "bytes": len(raw),
                "sha256": digest(raw), "exact_readback": True}
        receipt_path = self.root / (PREFIX + "receipt.json")
        receipt_path.write_text(json.dumps({"status": "blocked", "execution_instance": INSTANCE,
            "request_id": PREVIOUS_REQUEST, "service_exit_code": 2,
            "service_cleanup": "confirmed_nonzero_exit", "business_call_attempts": 1,
            "binding": ready["binding"], "assets": assets, "listener_after": ready["listener_inodes"]}))
        diagnostic_path = self.root / DIAGNOSTIC
        diagnostic_path.write_text(json.dumps({"status": "failed_window_diagnosed_not_business_acceptance",
            "execution_instance": INSTANCE, "request_id": PREVIOUS_REQUEST,
            "actual_online_outer_exit_code": 2, "service_exit_code": 2,
            "service_cleanup": "confirmed_nonzero_exit", "actual_diagnostic_exit_code": 0,
            "online_receipt": PREFIX + "receipt.json", "online_receipt_sha256": digest(receipt_path.read_bytes()),
            "saved_assets_verified": len(assets), "saved_asset_bytes": sum(item["bytes"] for item in assets.values()),
            "failure_error_type": "JSONDecodeError", "online_tool_result": "isolated-outer-exit-2",
            "diagnostic_tool_result": "isolated-diagnostic-exit-0", "listener_unchanged": True,
            "delivery_saved": False, "stdio_saved": False}))
        paths = paths + [marker_path, ready_path, receipt_path, diagnostic_path]
        paths.extend(self.root / item["path"] for item in assets.values())
        current = type(previous)({"fixture": self.project}, self.policy, artifact_root=self.root, online=True)
        return current, {**arguments, "request_id": REQUEST}, paths

    def target(self):
        return self.root / "verification/.scratch" / (MARKER + ".full.json")

    def test_new_slot_is_exclusive_links_failed_window_and_preserves_old_assets(self):
        current, arguments, paths = self.case()
        before = [path.read_bytes() for path in paths]
        with patch.object(current.gateway, "generate", side_effect=AssertionError("接纳不得发业务")):
            current._admit_live(arguments)
        raw = self.target().read_bytes()
        marker = json.loads(raw)
        self.assertEqual(marker["request_id"], REQUEST)
        self.assertEqual(marker["recover_from_request_id"], ORIGINAL)
        self.assertEqual(marker["previous_candidate_request_id"], PREVIOUS_REQUEST)
        self.assertEqual(marker["selection_version"], "question-scout-v2")
        self.assertEqual(marker["candidate_retryable_reasons"], [InvalidSourceCandidate.reason, InvalidCandidateSyntax.reason])
        self.assertEqual(marker["retryable_reasons"], ["upstream_stream_error", "upstream_timeout"])
        self.assertEqual(marker["generation_controls"], {"temperature": 0.2, "max_tokens": 3072})
        self.assertEqual((marker["process_seconds"], marker["soft_target_seconds"], marker["return_reserve_seconds"]), (600, 180, 15))
        self.assertEqual((marker["max_retries"], marker["max_total_retries"]), (3, 3))
        self.assertFalse(marker["automatic_replay"])
        for key, path in (
                ("previous_candidate_marker_sha256", PREVIOUS_MARKER),
                ("previous_candidate_receipt_sha256", PREFIX + "receipt.json"),
                ("previous_candidate_diagnostic_sha256", DIAGNOSTIC),
                ("previous_candidate_ready_sha256", READY)):
            self.assertEqual(marker[key], digest((self.root / path).read_bytes()))
        with self.assertRaises(Exception):
            current._admit_live(arguments)
        self.assertEqual(self.target().read_bytes(), raw)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_every_missing_or_changed_bound_asset_blocks_without_new_slot(self):
        current, arguments, paths = self.case()
        for path in dict.fromkeys(paths):
            raw = path.read_bytes()
            for mode in ("missing", "changed"):
                if mode == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"changed")
                try:
                    with self.subTest(path=path.name, mode=mode), self.assertRaises(Exception):
                        current._admit_live(arguments)
                    self.assertFalse(self.target().exists())
                finally:
                    path.write_bytes(raw)

    def test_actual_exit_and_typed_diagnostic_are_required(self):
        current, arguments, _ = self.case()
        path = self.root / DIAGNOSTIC
        raw = path.read_bytes()
        original = json.loads(raw)
        for change in (
                {"actual_online_outer_exit_code": 0}, {"actual_online_outer_exit_code": True},
                {"service_exit_code": 0}, {"service_cleanup": "unknown"},
                {"actual_diagnostic_exit_code": 2}, {"failure_error_type": "ValueError"},
                {"online_receipt_sha256": "0" * 64}, {"online_tool_result": ""},
                {"diagnostic_tool_result": ""}, {"listener_unchanged": False},
                {"delivery_saved": True}, {"stdio_saved": True}, {"saved_assets_verified": 0},
                {"saved_asset_bytes": 0}):
            path.write_text(json.dumps({**original, **change}))
            with self.subTest(change=change), self.assertRaises(Exception):
                current._admit_live(arguments)
            self.assertFalse(self.target().exists())
        path.write_bytes(raw)

    def test_frozen_input_and_previous_marker_conflicts_cannot_create_slot(self):
        current, arguments, _ = self.case()
        for change in ({"question": "changed"}, {"include": ["empty.py"]},
                       {"dimensions": []}, {"return_bytes": 1}, {"wire_encoding": "wrong"}):
            with self.subTest(change=change), self.assertRaises(Exception):
                current._admit_live({**arguments, **change})
            self.assertFalse(self.target().exists())
        marker_path = self.root / PREVIOUS_MARKER
        raw = marker_path.read_bytes()
        marker = json.loads(raw)
        for change in ({"max_total_retries": 4}, {"candidate_retryable_reason": SyntaxErrorType.reason},
                       {"request_id": REQUEST}, {"generation_controls": {"temperature": 0.3, "max_tokens": 3072}}):
            marker_path.write_text(json.dumps({**marker, **change}))
            with self.subTest(change=change), self.assertRaises(Exception):
                current._admit_live(arguments)
            self.assertFalse(self.target().exists())
        marker_path.write_bytes(raw)

    def test_read_only_previous_admission_recheck_does_not_recreate_old_slot(self):
        current, arguments, paths = self.case()
        before = [path.read_bytes() for path in paths]
        with patch.object(current, "persist_scratch", side_effect=AssertionError("只读复核不得保存")):
            marker = current._admit_candidate({**arguments, "request_id": PREVIOUS_REQUEST}, ORIGINAL, persist=False)
        self.assertEqual(marker, json.loads((self.root / PREVIOUS_MARKER).read_bytes()))
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertFalse(self.target().exists())

    def test_explicit_identity_version_and_shorter_absolute_window(self):
        current, arguments, _ = self.case()
        current.validate_v3(arguments, 3)
        shorter = {**arguments, "host_timeout_seconds": 180,
            "deadline_unix_ms": arguments["started_at_unix_ms"] + 180000}
        current.validate_v3(shorter, 3)
        for change in ({"selection_version": "question-scout-v1"}, {"selection_version": None},
                       {"request_id": REQUEST + "-unknown"}, {"recover_from_request_id": PREVIOUS_REQUEST},
                       {"host_timeout_seconds": 601}):
            with self.subTest(change=change), self.assertRaises(Exception):
                current.validate_v3({**arguments, **change}, 3)
        omitted = dict(arguments)
        omitted.pop("selection_version")
        with self.assertRaises(Exception):
            current.validate_v3(omitted, 3)
