"""候选恢复接纳正负控；模拟已退出失败窗口，不访问真实网关或接纳资产。"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

from solo_distill.gateway import encode_request
from solo_distill.preflight_v3 import INSTANCE, PARENT
from test_distillation import Connection, Response
from test_schema_v3_coverage_recovery import CoverageRecoveryTests, COVERAGE, VERSION
from test_schema_v3_minimal_recovery import ORIGINAL, digest
from test_schema_v3_stdio import Fixture

REQUEST = INSTANCE + "-live-candidate-20261102T070153Z"
MARKER = INSTANCE + ".live-candidate-20261102T070153Z"
PREVIOUS_MARKER = "verification/.scratch/" + INSTANCE + ".live-coverage-20261102T050022Z.full.json"
PREFIX = PARENT + "/phase5-live-coverage-20261102T050022Z."
READY = PARENT + "/phase5-coverage-ready-20261102T050022Z.json"
DIAGNOSTIC = PARENT + "/phase5-coverage-failure-diagnostic-20261102T064856Z.receipt.json"


class CandidateAdmissionTests(Fixture):
    fixture = CoverageRecoveryTests.fixture
    live = CoverageRecoveryTests.live
    candidate = CoverageRecoveryTests.candidate
    success = CoverageRecoveryTests.success

    def case(self):
        previous, arguments, messages, _, prior = self.fixture()
        source = json.loads(messages[-1]["content"])["sources"][0]["source_id"]
        bad = {"summary": "整份拒收的历史候选", "conclusions": [{
            "dimension_id": "interfaces", "text": "不得猜修未知来源。",
            "source_ids": [source, "unsent-source"]}], "unknowns": []}
        connection = Connection(Response([self.success(bad)]))
        with patch("solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
            with self.assertRaises(ValueError):
                previous.distill_frame(arguments, rpc_id=3)
        marker_path = self.root / PREVIOUS_MARKER
        marker = json.loads(marker_path.read_bytes())
        minimal = json.loads((self.root / PARENT / (
            "phase5-live-minimal-20261102T020451Z.receipt.json")).read_bytes())
        bound = dict(minimal["binding"])
        bound.update({key: value for key, value in marker.items() if key.endswith("_sha256")})
        post = encode_request(messages, generation_controls=True)
        snapshot = self.trace(ORIGINAL)["report"]["snapshot_id"]
        ready = {"status": "ready_not_online_executed", "binding": bound,
            "selection_version": VERSION, "wire_arguments": arguments,
            "snapshot_id": snapshot, "selection_id": arguments["frozen_selection"]["selection_id"],
            "post_bytes": len(post), "post_sha256": digest(post), "listener_inodes": ["fixture"]}
        (self.root / READY).write_text(json.dumps(ready))
        assets = {}
        for key, name, raw in (
                ("admission", "admission.json", marker_path.read_bytes()),
                ("request", "request.json", json.dumps(arguments).encode()),
                ("transport_attempt_1", "transport-attempt-1.json", (
                    self.root / "verification/.scratch" / (
                        COVERAGE + ".gateway-attempt-1.full.json")).read_bytes()),
                ("failure", "failure.json", b'{"reason":"v3_task_invalid_or_unsent_conclusion"}')):
            path = self.root / (PREFIX + name)
            path.write_bytes(raw)
            assets[key] = {"path": str(path.relative_to(self.root)), "bytes": len(raw),
                "sha256": digest(raw), "exact_readback": True}
        receipt_path = self.root / (PREFIX + "receipt.json")
        receipt_path.write_text(json.dumps({"status": "blocked", "execution_instance": INSTANCE,
            "request_id": COVERAGE, "service_exit_code": 2,
            "service_cleanup": "confirmed_nonzero_exit", "business_call_attempts": 1,
            "binding": bound, "assets": assets, "listener_after": ["fixture"]}))
        diagnostic = {"status": "failed_window_diagnosed_not_business_acceptance",
            "execution_instance": INSTANCE, "request_id": COVERAGE,
            "actual_online_outer_exit_code": 2, "service_exit_code": 2,
            "service_cleanup": "confirmed_nonzero_exit", "actual_diagnostic_exit_code": 0,
            "online_receipt": PREFIX + "receipt.json",
            "online_receipt_sha256": digest(receipt_path.read_bytes()),
            "saved_assets_verified": len(assets),
            "saved_asset_bytes": sum(asset["bytes"] for asset in assets.values()),
            "failure_reason": "v3_task_invalid_or_unsent_conclusion",
            "online_tool_result": "isolated-fixture-outer-exit-2",
            "diagnostic_tool_result": "isolated-fixture-diagnostic-exit-0",
            "listener_unchanged": True, "delivery_saved": False, "stdio_saved": False}
        (self.root / DIAGNOSTIC).write_text(json.dumps(diagnostic))
        paths = prior + [marker_path, self.root / READY, receipt_path, self.root / DIAGNOSTIC]
        paths.extend(self.root / asset["path"] for asset in assets.values())
        current = type(previous)({"fixture": self.project}, self.policy,
                                 artifact_root=self.root, online=True)
        return current, {**arguments, "request_id": REQUEST}, paths

    def test_new_slot_links_actual_failed_window_and_preserves_all_old_bytes(self):
        current, arguments, paths = self.case()
        before = [path.read_bytes() for path in paths]
        with patch.object(current.gateway, "generate", side_effect=AssertionError("不得发业务")):
            current._admit_live(arguments)
        target = self.root / "verification/.scratch" / (MARKER + ".full.json")
        raw = target.read_bytes()
        marker = json.loads(raw)
        self.assertEqual(marker["request_id"], REQUEST)
        self.assertEqual(marker["recover_from_request_id"], ORIGINAL)
        self.assertEqual(marker["previous_coverage_request_id"], COVERAGE)
        self.assertEqual(marker["selection_version"], VERSION)
        self.assertEqual(marker["process_seconds"], 600)
        self.assertEqual(marker["max_retries"], 3)
        self.assertEqual(marker["max_total_retries"], 3)
        self.assertEqual(marker["candidate_retryable_reason"], "invalid_or_unsent_source_id")
        self.assertFalse(marker["automatic_replay"])
        for key, path in (("previous_coverage_marker_sha256", PREVIOUS_MARKER),
                          ("previous_coverage_receipt_sha256", PREFIX + "receipt.json"),
                          ("previous_coverage_diagnostic_sha256", DIAGNOSTIC),
                          ("previous_coverage_ready_sha256", READY)):
            self.assertEqual(marker[key], digest((self.root / path).read_bytes()))
        with self.assertRaises(Exception):
            current._admit_live(arguments)
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_missing_or_changed_immediate_evidence_cannot_create_slot(self):
        current, arguments, paths = self.case()
        target = self.root / "verification/.scratch" / (MARKER + ".full.json")
        for path in paths[-8:]:
            raw = path.read_bytes()
            for mode in ("missing", "changed"):
                if mode == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"changed")
                try:
                    with self.subTest(path=path.name, mode=mode), self.assertRaises(Exception):
                        current._admit_live(arguments)
                    self.assertFalse(target.exists())
                finally:
                    path.write_bytes(raw)

    def test_actual_exit_binding_is_not_inferred_from_generic_error_text(self):
        current, arguments, _ = self.case()
        path = self.root / DIAGNOSTIC
        original = json.loads(path.read_bytes())
        for change in ({"actual_online_outer_exit_code": 0}, {"actual_online_outer_exit_code": True},
                       {"service_exit_code": 0}, {"service_cleanup": "unknown"},
                       {"online_receipt_sha256": "0" * 64}, {"online_tool_result": ""}):
            path.write_text(json.dumps({**original, **change}))
            with self.subTest(change=change), self.assertRaises(Exception):
                current._admit_live(arguments)
            self.assertFalse((self.root / "verification/.scratch" / (MARKER + ".full.json")).exists())
        path.write_text(json.dumps(original))

    def test_explicit_identity_version_and_shorter_absolute_window(self):
        current, arguments, _ = self.case()
        current.validate_v3(arguments, 3)
        shorter = copy.deepcopy(arguments)
        shorter["host_timeout_seconds"] = 180
        shorter["deadline_unix_ms"] = shorter["started_at_unix_ms"] + 180000
        current.validate_v3(shorter, 3)
        for change in ({"selection_version": "question-scout-v1"}, {"selection_version": None},
                       {"request_id": REQUEST + "-unknown"}, {"recover_from_request_id": COVERAGE},
                       {"host_timeout_seconds": 601}):
            with self.subTest(change=change), self.assertRaises(Exception):
                current.validate_v3({**arguments, **change}, 3)
        omitted = dict(arguments)
        omitted.pop("selection_version")
        with self.assertRaises(Exception):
            current.validate_v3(omitted, 3)
