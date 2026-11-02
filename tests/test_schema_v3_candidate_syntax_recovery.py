"""新语法窗口隔离回归；模拟 HTTP，真实服务、投递及双消费，不消耗线上槽。"""
from __future__ import annotations

import copy
import json
import time
from unittest.mock import patch

from schema_v3_candidate_syntax_oracle import SyntaxRejection, parse_candidate
from schema_v3_wire_oracle import receive
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v3 import consume_frame
from solo_distill.gateway import InvalidCandidateSyntax, candidate_json, encode_request, recovery_candidate_json
from solo_distill.preflight_v3 import INSTANCE, PARENT
from solo_distill.task_v3 import InvalidSourceCandidate
from solo_distill.wire_v3 import Admission
from test_distillation import Connection, Response, event
from test_schema_v3_candidate_admission import READY as COVERAGE_READY
from test_schema_v3_candidate_recovery import CandidateRecoveryTests, REQUEST as PREVIOUS_REQUEST
from test_schema_v3_coverage_recovery import VERSION
from test_schema_v3_live_retry import TRANSIENT
from test_schema_v3_minimal_recovery import TIMEOUT, digest
from test_schema_v3_stdio import Fixture

REQUEST = INSTANCE + "-live-syntax-20261102T085352Z"
MARKER = INSTANCE + ".live-syntax-20261102T085352Z"
READY = PARENT + "/phase5-candidate-ready-20261102T070153Z.json"


def completed(output):
    return (event({"choices": [{"index": 0, "delta": {"content": output}, "finish_reason": None}]})
        + event({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        + b"data: [DONE]\n\n")


class CandidateSyntaxRecoveryTests(Fixture):
    fixture = CandidateRecoveryTests.fixture
    live = CandidateRecoveryTests.live
    candidate = CandidateRecoveryTests.candidate
    success = CandidateRecoveryTests.success
    run_streams = CandidateRecoveryTests.run_streams

    def case(self):
        current, arguments, messages, post, prior, good, bad = CandidateRecoveryTests.case(self)
        previous = json.loads((self.root / COVERAGE_READY).read_bytes())
        previous["wire_arguments"] = copy.deepcopy(arguments)
        ready_path = self.root / READY
        ready_path.write_text(json.dumps(previous))
        marker_path = self.root / "verification/.scratch" / (MARKER + ".full.json")
        # 仅本组传输回归的隔离前提；完整接纳链由独立接纳回归覆盖。
        marker_path.write_text(json.dumps({"previous_candidate_ready_sha256": digest(ready_path.read_bytes())}))
        current.gateway.capture_stream = True
        return current, {**arguments, "request_id": REQUEST}, messages, post, prior, good, bad

    def rejection(self, number):
        return self.trace(REQUEST + ".candidate-rejection-" + str(number))

    def reset_fixture(self):
        self.tearDown()
        self.setUp()

    def test_production_parser_matches_independent_without_changing_old_entry(self):
        for output in ('{"a":1}', ' ```json\n{"a":1}\n``` ', '[]', 'null'):
            self.assertEqual(recovery_candidate_json(output), parse_candidate(output))
        for output in ('{"a":1,}', '{a:1}', '{"a":', '{"a":1} extra'):
            with self.subTest(output=output):
                with self.assertRaises(InvalidCandidateSyntax) as production:
                    recovery_candidate_json(output)
                with self.assertRaises(SyntaxRejection) as independent:
                    parse_candidate(output)
                self.assertEqual(production.exception.output, output)
                self.assertEqual(production.exception.details, independent.exception.details)
                with self.assertRaises(json.JSONDecodeError):
                    candidate_json(output)
        for output in ('{"a":1,"a":2}', '{"a":NaN}', '```json', '```python\n{}\n```'):
            with self.subTest(output=output), self.assertRaises(ValueError) as caught:
                recovery_candidate_json(output)
            self.assertNotIsInstance(caught.exception, InvalidCandidateSyntax)

    def test_mixed_types_fourth_success_same_post_deadline_and_trusted_dual_consumption(self):
        for upstream in (TRANSIENT, TIMEOUT):
            with self.subTest(upstream=upstream):
                current, arguments, messages, _, prior, good, bad = self.case()
                before = [path.read_bytes() for path in prior]
                malformed = '{\n"summary":"整份语法拒收",\n}'
                frame, connections, count = self.run_streams(current, arguments,
                    [upstream, completed(malformed), self.success(bad), self.success(good)])
                self.assertEqual(count, 4)
                trace = self.trace(REQUEST)
                self.assertEqual(trace["candidate"], good)
                self.assertEqual(len(trace["model_outputs"]), 1)
                self.assertEqual(json.loads(trace["model_outputs"][0]), good)
                attempts = trace["gateway_requests"]
                self.assertEqual([item["attempt_index"] for item in attempts], [1, 2, 3, 4])
                self.assertEqual([item["client_auto_retries"] for item in attempts], [0, 1, 2, 3])
                self.assertEqual(len({item["absolute_deadline"] for item in attempts}), 1)
                self.assertAlmostEqual(attempts[0]["absolute_deadline"], current.business_deadline - 15)
                rejection = self.rejection(2)
                self.assertEqual(rejection["reason"], InvalidCandidateSyntax.reason)
                self.assertEqual(rejection["output"], malformed)
                self.assertEqual(rejection["parse_error"]["error_type"], "JSONDecodeError")
                self.assertEqual((rejection["parse_error"]["line"], rejection["parse_error"]["column"]), (3, 1))
                self.assertEqual(rejection["transport_attempt_id"], REQUEST + ".gateway-attempt-2")
                self.assertEqual(self.rejection(3)["candidate"], bad)
                for number, connection in enumerate(connections, 1):
                    self.assertTrue(connection.closed)
                    self.assertEqual(connection.calls[0][1]["body"], encode_request(messages, generation_controls=True))
                    self.assertEqual(self.trace(REQUEST + ".gateway-attempt-" + str(number))["post_sha256"], trace["post_sha256"])
                trusted = {**self.trusted(trace, arguments), "selection_version": VERSION}
                admission = Admission(arguments["protocol_version"], "A", 3, arguments["return_bytes"],
                                      TransportCapabilities.from_dict(arguments["transport"]))
                with patch("socket.socket", side_effect=AssertionError("不得联网")):
                    production = consume_frame(frame.data, self.files, admission=admission, candidate=good, **trusted)
                    independent = receive(frame.data, self.files, arguments["transport"],
                        protocol_version=arguments["protocol_version"], response_id=3,
                        return_bytes=arguments["return_bytes"], candidate=good, **trusted)
                self.assertEqual(production.status, "complete")
                self.assertEqual(independent["task_status"], "complete")
                self.assertEqual(production.publication, independent["publication"])
                self.assertEqual([path.read_bytes() for path in prior], before)
                self.reset_fixture()

    def test_all_allowed_failure_types_share_exhaustion_without_success_publication(self):
        current, arguments, _, _, _, _, bad = self.case()
        streams = [TRANSIENT, TIMEOUT, self.success(bad), completed('{"a":1,}')]
        connections = [Connection(Response([stream])) for stream in streams]
        with patch.object(current, "_admit_live"), patch(
                "solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect, patch(
                "solo_distill.service_v3.publish_v3") as publish:
            with self.assertRaises(InvalidCandidateSyntax):
                current.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 4)
        publish.assert_not_called()
        self.assertEqual(self.rejection(3)["reason"], InvalidSourceCandidate.reason)
        self.assertEqual(self.rejection(4)["reason"], InvalidCandidateSyntax.reason)
        for number in range(1, 5):
            self.assertEqual(self.trace(REQUEST + ".gateway-attempt-" + str(number))["observation"]["attempt_index"], number)
        self.assertFalse((self.root / "verification/.scratch" / (REQUEST + ".full.json")).exists())

    def test_bad_fences_duplicates_nonfinite_and_candidate_shape_do_not_retry(self):
        for output in ('```json', '```python\n{}\n```', '{"a":1,"a":2}', '{"a":NaN}',
                       '{"a":Infinity}', '[]', 'null', '{"wrong":1}'):
            with self.subTest(output=output):
                current, arguments, _, _, _, _, _ = self.case()
                connection = Connection(Response([completed(output)]))
                with patch.object(current, "_admit_live"), patch(
                        "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as connect:
                    with self.assertRaises((ValueError, TypeError)) as caught:
                        current.distill_frame(arguments, rpc_id=3)
                self.assertNotIsInstance(caught.exception, InvalidCandidateSyntax)
                self.assertEqual(connect.call_count, 1)
                self.assertFalse((self.root / "verification/.scratch" / (REQUEST + ".candidate-rejection-1.full.json")).exists())
                self.reset_fixture()

    def test_syntax_rejection_save_cancel_deadline_and_source_change_stop_next_send(self):
        for mode in ("transport_save", "rejection_save", "cancel", "deadline", "source"):
            with self.subTest(mode=mode):
                current, arguments, _, _, _, _, _ = self.case()
                persist = current.persist_scratch
                def saving(identity, value):
                    if mode == "transport_save" and ".gateway-attempt-" in identity:
                        raise OSError("隔离传输保存失败")
                    if mode == "rejection_save" and ".candidate-rejection-" in identity:
                        raise OSError("隔离拒收保存失败")
                    result = persist(identity, value)
                    if ".candidate-rejection-" in identity:
                        if mode == "cancel":
                            current.cancelled.set()
                        elif mode == "deadline":
                            current.business_deadline = time.monotonic() - 1
                        elif mode == "source":
                            (self.root / "source/sample.py").write_bytes(b"def interfaces():\n return 2\n")
                    return result
                connection = Connection(Response([completed('{"a":1,}')]))
                with patch.object(current, "_admit_live"), patch.object(current, "persist_scratch", side_effect=saving), patch(
                        "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as connect:
                    with self.assertRaises(Exception):
                        current.distill_frame(arguments, rpc_id=3)
                self.assertEqual(connect.call_count, 1)
                self.assertTrue(connection.closed)
                self.reset_fixture()

    def test_previous_candidate_window_keeps_hard_syntax_failure(self):
        current, arguments, _, _, _, _, _ = CandidateRecoveryTests.case(self)
        self.assertEqual(arguments["request_id"], PREVIOUS_REQUEST)
        connection = Connection(Response([completed('{"a":1,}')]))
        with patch.object(current, "_admit_live"), patch(
                "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as connect:
            with self.assertRaises(json.JSONDecodeError):
                current.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 1)
        self.assertFalse((self.root / "verification/.scratch" / (PREVIOUS_REQUEST + ".candidate-rejection-1.full.json")).exists())
