"""候选恢复的隔离正负控；真实编解码与模拟HTTP，不消耗在线接纳。"""
from __future__ import annotations

import copy
import json
import time
from unittest.mock import patch

from schema_v3_wire_oracle import receive
from schema_v3_selection_oracle import expected_selection
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v3 import consume_frame
from solo_distill.gateway import encode_request
from solo_distill.preflight_v3 import INSTANCE
from solo_distill.task_v3 import InvalidSourceCandidate
from solo_distill.wire_v3 import Admission
from test_distillation import Connection, Response
from test_schema_v3_coverage_recovery import CoverageRecoveryTests, COVERAGE, VERSION
from test_schema_v3_candidate_admission import CandidateAdmissionTests
from test_schema_v3_minimal_recovery import ORIGINAL
from test_schema_v3_live_retry import TRANSIENT
from test_schema_v3_minimal_recovery import TIMEOUT
from test_schema_v3_stdio import Fixture

REQUEST = INSTANCE + "-live-candidate-20261102T070153Z"


class CandidateRecoveryTests(Fixture):
    fixture = CoverageRecoveryTests.fixture
    live = CoverageRecoveryTests.live
    candidate = CoverageRecoveryTests.candidate
    success = CoverageRecoveryTests.success

    def case(self):
        distiller, arguments, prior = CandidateAdmissionTests.case(self)
        report = self.trace(ORIGINAL)["report"]
        messages, post, selection = expected_selection(self.files,
            project_id=arguments["project_id"], snapshot_id=report["snapshot_id"],
            question=arguments["question"], dimensions=arguments["dimensions"],
            frozen_coverage=report["coverage"], selection_version=VERSION)
        self.assertEqual(selection, arguments["frozen_selection"])
        distiller._admit_live(arguments)
        source = json.loads(messages[-1]["content"])["sources"][0]["source_id"]
        good = {"summary": "完整合法候选", "conclusions": [{
            "dimension_id": "interfaces", "text": "该函数返回整数。",
            "source_ids": [source]}], "unknowns": ["运行时行为未验证"]}
        bad = copy.deepcopy(good)
        bad["summary"] = "拒收候选不得进入发布"
        bad["conclusions"].append({"dimension_id": "interfaces",
            "text": "保留但整份拒收的结论", "source_ids": ["unsent-source"]})
        return distiller, arguments, messages, post, prior, good, bad

    def run_streams(self, distiller, arguments, streams):
        connections = [Connection(Response([stream])) for stream in streams]
        # 接纳链由独立的接纳正负控覆盖；这里不伪造历史窗口通过。
        with patch.object(distiller, "_admit_live"), patch(
                "solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect:
            frame = distiller.distill_frame(arguments, rpc_id=3)
        return frame, connections, connect.call_count

    def rejection(self, number):
        return self.trace(REQUEST + ".candidate-rejection-" + str(number))

    def test_mixed_transport_candidate_retries_share_four_attempts_and_dual_consumption(self):
        distiller, arguments, messages, _, prior, good, bad = self.case()
        before = [path.read_bytes() for path in prior]
        frame, connections, count = self.run_streams(distiller, arguments,
            [TRANSIENT, self.success(bad), TIMEOUT, self.success(good)])
        self.assertEqual(count, 4)
        trace = self.trace(REQUEST)
        self.assertEqual(trace["candidate"], good)
        self.assertEqual(len(trace["model_outputs"]), 1)
        self.assertEqual(json.loads(trace["model_outputs"][0]), good)
        self.assertNotIn(bad["summary"], trace["model_outputs"][0])
        attempts = trace["gateway_requests"]
        self.assertEqual([item["attempt_index"] for item in attempts], [1, 2, 3, 4])
        self.assertEqual([item["client_auto_retries"] for item in attempts], [0, 1, 2, 3])
        self.assertEqual(len({item["absolute_deadline"] for item in attempts}), 1)
        self.assertAlmostEqual(attempts[0]["absolute_deadline"], distiller.business_deadline - 15)
        self.assertEqual(attempts[1]["candidate_error_reason"], InvalidSourceCandidate.reason)
        rejected = self.rejection(2)
        self.assertEqual(rejected["candidate"], bad)
        self.assertEqual(rejected["reason"], InvalidSourceCandidate.reason)
        self.assertEqual(rejected["attempt_index"], 2)
        self.assertEqual(rejected["invalid_references"], [[1, "unsent-source"]])
        self.assertEqual(rejected["transport_attempt_id"], REQUEST + ".gateway-attempt-2")
        for connection in connections:
            self.assertEqual(connection.calls[0][1]["body"], encode_request(messages, generation_controls=True))
            self.assertTrue(connection.closed)
        trusted = {**self.trusted(trace, arguments), "selection_version": VERSION}
        admission = Admission(arguments["protocol_version"], "A", 3, arguments["return_bytes"],
                              TransportCapabilities.from_dict(arguments["transport"]))
        with patch("socket.socket", side_effect=AssertionError("不得联网")):
            production = consume_frame(frame.data, self.files, admission=admission, candidate=good, **trusted)
            independent = receive(frame.data, self.files, arguments["transport"],
                protocol_version=arguments["protocol_version"], response_id=3,
                return_bytes=arguments["return_bytes"], candidate=good, **trusted)
        self.assertEqual(production.publication, independent["publication"])
        self.assertEqual(production.status, "complete")
        self.assertEqual([path.read_bytes() for path in prior], before)

    def test_four_invalid_candidates_exhaust_without_publication_or_success_trace(self):
        distiller, arguments, _, _, _, _, bad = self.case()
        connections = [Connection(Response([self.success(bad)])) for _ in range(4)]
        with patch.object(distiller, "_admit_live"), patch(
                "solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect, patch(
                "solo_distill.service_v3.publish_v3", side_effect=AssertionError("不得发布")) as publish:
            with self.assertRaises(InvalidSourceCandidate):
                distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 4)
        publish.assert_not_called()
        for number in range(1, 5):
            self.assertEqual(self.rejection(number)["candidate"], bad)
            self.assertEqual(self.trace(REQUEST + ".gateway-attempt-" + str(number))["status"],
                             "transport_completed_not_business_acceptance")
        self.assertFalse((self.root / "verification/.scratch" / (REQUEST + ".full.json")).exists())

    def test_later_shape_error_is_not_retried_or_saved_as_invalid_source(self):
        for change in ({"source_ids": []}, {"dimension_id": "unknown"}, {"text": " "}):
            with self.subTest(change=change):
                distiller, arguments, _, _, _, _, bad = self.case()
                bad["conclusions"].append({"dimension_id": "interfaces", "text": "合法正文",
                                           "source_ids": ["unsent-source"], **change})
                connection = Connection(Response([self.success(bad)]))
                with patch.object(distiller, "_admit_live"), patch(
                        "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as connect:
                    with self.assertRaises(ValueError) as caught:
                        distiller.distill_frame(arguments, rpc_id=3)
                self.assertNotIsInstance(caught.exception, InvalidSourceCandidate)
                self.assertEqual(connect.call_count, 1)
                self.assertFalse((self.root / "verification/.scratch" / (
                    REQUEST + ".candidate-rejection-1.full.json")).exists())
                # 同一测试实例下一子例不复用已占用的隔离传输资产。
                self.tearDown()
                self.setUp()

    def test_rejection_save_failure_cancel_or_deadline_prevents_next_send(self):
        for mode in ("save", "cancel", "deadline"):
            with self.subTest(mode=mode):
                distiller, arguments, _, _, _, _, bad = self.case()
                persist = distiller.persist_scratch
                def saving(identity, value):
                    if ".candidate-rejection-" in identity and mode == "save":
                        raise OSError("isolated rejection save failed")
                    result = persist(identity, value)
                    if ".candidate-rejection-" in identity:
                        if mode == "cancel":
                            distiller.cancelled.set()
                        elif mode == "deadline":
                            distiller.business_deadline = time.monotonic() - 1
                    return result
                connection = Connection(Response([self.success(bad)]))
                with patch.object(distiller, "_admit_live"), patch.object(
                        distiller, "persist_scratch", side_effect=saving), patch(
                        "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as connect:
                    with self.assertRaises(Exception):
                        distiller.distill_frame(arguments, rpc_id=3)
                self.assertEqual(connect.call_count, 1)
                self.assertTrue(connection.closed)
                self.tearDown()
                self.setUp()

    def test_previous_coverage_window_does_not_gain_candidate_retries(self):
        # 使用尚未发送的独立旧版本夹具，不复活或删除已保存的覆盖窗口资产。
        distiller, arguments, messages, _, _ = self.fixture()
        self.assertEqual(arguments["request_id"], COVERAGE)
        source = json.loads(messages[-1]["content"])["sources"][0]["source_id"]
        bad = {"summary": "旧窗口不得获得候选重试", "conclusions": [{
            "dimension_id": "interfaces", "text": "非法引用必须拒收。",
            "source_ids": [source, "unsent-source"]}], "unknowns": []}
        connection = Connection(Response([self.success(bad)]))
        with patch.object(distiller, "_admit_live"), patch(
                "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as connect:
            with self.assertRaises(InvalidSourceCandidate):
                distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 1)
