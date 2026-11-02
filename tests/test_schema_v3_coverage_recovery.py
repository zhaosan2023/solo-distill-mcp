"""显式新选择恢复的隔离正负控；保留已退出旧窗口，不访问真实网关。"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

from schema_v3_selection_oracle import expected_selection
from schema_v3_wire_oracle import receive
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v3 import consume_frame
from solo_distill.gateway import encode_request
from solo_distill.preflight_v3 import INSTANCE, PARENT
from solo_distill.wire_v3 import Admission
from test_distillation import Connection, Response
from test_schema_v3_live_retry import TRANSIENT
from test_schema_v3_minimal_recovery import (
    MINIMAL, ORIGINAL, TIMEOUT, MinimalRecoveryTests, digest,
)
from test_schema_v3_stdio import Fixture

COVERAGE = INSTANCE + "-live-coverage-20261102T050022Z"
MARKER = INSTANCE + ".live-coverage-20261102T050022Z"
VERSION = "question-scout-v2"
LIVE_PREFIX = "phase5-live-minimal-20261102T020451Z"
ACCEPT_PREFIX = "phase5-minimal-acceptance-20261102T020451Z"


class CoverageRecoveryTests(Fixture):
    live = MinimalRecoveryTests.live
    candidate = MinimalRecoveryTests.candidate
    success = MinimalRecoveryTests.success

    def fixture(self):
        old, arguments, prepared, prior = MinimalRecoveryTests.fixture(self)
        candidate = self.candidate(prepared)
        connection = Connection(Response([self.success(candidate)]))
        with patch("solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
            old.distill_frame(arguments, rpc_id=3)
        parent = self.root / PARENT
        minimal_marker = self.root / "verification/.scratch" / (
            INSTANCE + ".live-minimal-20261102T020451Z.full.json")
        binding = {
            "original_admission_sha256": digest((self.root / "verification/.scratch" / (
                INSTANCE + ".live-admission.full.json")).read_bytes()),
            "original_trace_sha256": digest((self.root / "verification/.scratch" / (
                ORIGINAL + ".full.json")).read_bytes()),
            "previous_extended_marker_sha256": digest(prior[8].read_bytes()),
            "previous_extended_receipt_sha256": digest(prior[9].read_bytes()),
            "previous_extended_postmortem_sha256": digest(prior[10].read_bytes()),
        }
        trace_path = parent / (LIVE_PREFIX + ".trace.json")
        trace_raw = (self.root / "verification/.scratch" / (MINIMAL + ".full.json")).read_bytes()
        trace_path.write_bytes(trace_raw)
        assets = {"trace": {"path": str(trace_path.relative_to(self.root)),
            "bytes": len(trace_raw), "sha256": digest(trace_raw), "exact_readback": True}}
        live_path = parent / (LIVE_PREFIX + ".receipt.json")
        live_path.write_text(json.dumps({
            "status": "online_transport_saved_trusted_consumption_pending", "failures": [],
            "execution_instance": INSTANCE, "request_id": MINIMAL,
            "service_exit_code": 0, "service_cleanup": "confirmed_natural_exit",
            "business_call_attempts": 1, "binding": binding, "assets": assets,
        }))
        package_path = parent / (ACCEPT_PREFIX + ".knowledge-package.json")
        package_path.write_bytes(b'{"fixture":"independently consumed historical asset"}')
        package_asset = {"path": str(package_path.relative_to(self.root)),
            "bytes": len(package_path.read_bytes()), "sha256": digest(package_path.read_bytes()),
            "exact_readback": True}
        consume_path = parent / (ACCEPT_PREFIX + ".consume.receipt.json")
        consume_path.write_text(json.dumps({
            "status": "dual_consumption_pass_semantics_pending", "execution_instance": INSTANCE,
            "request_id": MINIMAL, "binding": binding,
            "live_receipt_sha256": digest(live_path.read_bytes()),
            "production_and_independent_publication_equal": True, "task_status": "complete",
            "assets": {"package": package_asset},
        }))
        review_path = parent / (ACCEPT_PREFIX + ".human-review-inputs.json")
        review_path.write_bytes(b'{"human_semantic_review":"pending"}')
        review_asset = {"path": str(review_path.relative_to(self.root)),
            "bytes": len(review_path.read_bytes()), "sha256": digest(review_path.read_bytes()),
            "exact_readback": True}
        reconcile_path = parent / (ACCEPT_PREFIX + ".reconciliation.receipt.json")
        reconcile_path.write_text(json.dumps({
            "status": "blocked_required_anchor_body_missing", "execution_instance": INSTANCE,
            "request_id": MINIMAL, "binding": binding,
            "missing_anchor_ids": ["fixture-gap"], "human_semantic_review": "not_proven",
            "review_inputs": review_asset,
        }))
        paths = prior + [minimal_marker, trace_path, live_path, package_path,
                         consume_path, review_path, reconcile_path]
        arguments = {**arguments, "request_id": COVERAGE, "selection_version": VERSION}
        messages, post, selection = expected_selection(self.files,
            project_id="fixture", snapshot_id=prepared.report["snapshot_id"],
            question=arguments["question"], dimensions=arguments["dimensions"],
            frozen_coverage=prepared.report["coverage"], selection_version=VERSION)
        arguments["frozen_selection"] = selection
        self.assertNotEqual(selection["selection_id"], prepared.selection["selection_id"])
        distiller = type(old)({"fixture": self.project}, self.policy,
                              artifact_root=self.root, online=True)
        return distiller, arguments, messages, post, paths

    def test_new_admission_preserves_prior_assets_and_is_exclusive(self):
        distiller, arguments, _, _, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        distiller._admit_live(arguments)
        target = self.root / "verification/.scratch" / (MARKER + ".full.json")
        raw = target.read_bytes()
        marker = json.loads(raw)
        self.assertEqual(marker["request_id"], COVERAGE)
        self.assertEqual(marker["recover_from_request_id"], ORIGINAL)
        self.assertEqual(marker["previous_minimal_request_id"], MINIMAL)
        self.assertEqual(marker["selection_version"], VERSION)
        self.assertEqual(marker["process_seconds"], 600)
        self.assertEqual(marker["max_retries"], 3)
        self.assertEqual(marker["generation_controls"], {"temperature": 0.2, "max_tokens": 3072})
        self.assertEqual(marker["retryable_reasons"], ["upstream_stream_error", "upstream_timeout"])
        self.assertFalse(marker["automatic_replay"])
        with self.assertRaises(Exception):
            distiller._admit_live(arguments)
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual([path.read_bytes() for path in prior], before)

    def test_missing_or_changed_immediate_evidence_blocks_before_marker(self):
        distiller, arguments, _, _, prior = self.fixture()
        target = self.root / "verification/.scratch" / (MARKER + ".full.json")
        for path in prior[12:]:
            original = path.read_bytes()
            for mode in ("missing", "changed"):
                if mode == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"changed")
                try:
                    with self.subTest(path=path.name, mode=mode), self.assertRaises(Exception):
                        distiller._admit_live(arguments)
                    self.assertFalse(target.exists())
                finally:
                    path.write_bytes(original)

    def test_explicit_version_identity_and_shorter_window_validation(self):
        distiller, arguments, _, _, _ = self.fixture()
        distiller.validate_v3(arguments, 3)
        for changes in ({"selection_version": "question-scout-v1"},
                        {"selection_version": None}, {"request_id": COVERAGE + "-unknown"},
                        {"recover_from_request_id": MINIMAL}, {"host_timeout_seconds": 601}):
            with self.subTest(changes=changes), self.assertRaises(Exception):
                distiller.validate_v3({**arguments, **changes}, 3)
        omitted = dict(arguments)
        omitted.pop("selection_version")
        with self.assertRaises(Exception):
            distiller.validate_v3(omitted, 3)

    def test_mixed_retry_new_selection_exact_post_and_trusted_dual_consumption(self):
        distiller, arguments, messages, legacy_post, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        source = json.loads(messages[-1]["content"])["sources"][0]["source_id"]
        candidate = {"summary": "隔离来源的新版恢复输出", "conclusions": [{
            "dimension_id": "interfaces", "text": "该函数返回整数。", "source_ids": [source]}],
            "unknowns": []}
        streams = [TRANSIENT, TIMEOUT, TIMEOUT, self.success(candidate)]
        connections = [Connection(Response([stream])) for stream in streams]
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect:
            frame = distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 4)
        trace = self.trace(COVERAGE)
        post = encode_request(messages, generation_controls=True)
        self.assertEqual(post, legacy_post[:-1] + b',"temperature":0.2,"max_tokens":3072}')
        self.assertEqual(trace["post_utf8"].encode(), post)
        self.assertEqual(trace["selection"], arguments["frozen_selection"])
        self.assertEqual(trace["candidate"], candidate)
        self.assertEqual(trace["failures"], [])
        self.assertEqual([item["attempt_index"] for item in trace["gateway_requests"]], [1, 2, 3, 4])
        self.assertEqual(len({item["absolute_deadline"] for item in trace["gateway_requests"]}), 1)
        self.assertAlmostEqual(trace["gateway_requests"][0]["absolute_deadline"],
                               distiller.business_deadline - 15)
        for connection in connections:
            self.assertEqual(connection.calls[0][1]["body"], post)
            self.assertTrue(connection.closed)
        trusted = {**self.trusted(trace, arguments), "selection_version": VERSION}
        admission = Admission(arguments["protocol_version"], "A", 3, arguments["return_bytes"],
                              TransportCapabilities.from_dict(arguments["transport"]))
        with patch("socket.socket", side_effect=AssertionError("不得联网")):
            production = consume_frame(frame.data, self.files, admission=admission,
                                       candidate=candidate, **trusted)
            independent = receive(frame.data, self.files, arguments["transport"],
                protocol_version=arguments["protocol_version"], response_id=3,
                return_bytes=arguments["return_bytes"], candidate=candidate, **trusted)
        self.assertEqual(production.publication, independent["publication"])
        self.assertEqual(production.status, "complete")
        self.assertEqual(independent["task_status"], "complete")
        self.assertEqual([path.read_bytes() for path in prior], before)

    def test_new_question_drift_blocks_without_gateway_send(self):
        distiller, arguments, _, _, _ = self.fixture()
        arguments = copy.deepcopy(arguments)
        arguments["question"] += " Changed authorized question."
        with patch.object(distiller.gateway, "generate", side_effect=AssertionError("must not send")) as generate:
            with self.assertRaises(Exception):
                distiller.distill_frame(arguments, rpc_id=3)
        generate.assert_not_called()
