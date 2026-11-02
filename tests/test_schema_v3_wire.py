"""V3 生产发布／重组／消费差分及真实管道边界；不联网。

先保存此测试资产，再实现显式生产接口；旧入口不接管新版本。
管道写入不是宿主消费、选择独立性或人工语义证明。
"""
from __future__ import annotations

import copy
import os
import threading
import time
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import canonical
from schema_v3_wire_oracle import receive as independent_receive
from test_schema_v3_wire_oracle import (
    CAPS, OBSERVATIONS, SOURCES, WireOracleTests, case, encode, manual_chunks,
)
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.core_v3 import SealedCore
from solo_distill.task_v3 import build_task
from solo_distill.wire_v2 import DeliveryBlocked, FrameWriter, FrozenFrame, FrozenTransfer
from solo_distill.wire_v3 import Admission, publish, reassemble
from solo_distill.consumer_v3 import consume_frame


def admission(caps=None, response_id=7, return_bytes=22016):
    return Admission("2024-11-05", "A", response_id, return_bytes,
                     TransportCapabilities.from_dict(CAPS if caps is None else caps))


class ProductionWireTests(WireOracleTests):
    def accept(self, raw, candidate, trusted, capabilities=None, response_id=7):
        # 生产消费者先执行，负控不能被独立接收器提前抛错所遮蔽。
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            actual = consume_frame(raw, SOURCES,
                admission=admission(capabilities, response_id), candidate=candidate,
                observations=OBSERVATIONS, **trusted)
        expected = super().accept(raw, candidate, trusted, capabilities, response_id)
        self.assertEqual(actual.publication, expected["publication"])
        self.assertEqual(actual.status, expected["task_status"])
        self.assertEqual(actual.publication_bytes, canonical(expected["publication"]))
        return expected

    def reject_wire(self, raw, capabilities=None, response_id=7, return_bytes=22016):
        super().reject_wire(raw, capabilities, response_id, return_bytes)
        with self.assertRaises(ValueError):
            reassemble(raw, admission(capabilities, response_id, return_bytes))

    def produce(self, candidate, trusted, report, caps=None, response_id=7):
        core = SealedCore(canonical(report["core"]))
        task = build_task(candidate, core, SOURCES, **trusted)
        before = core.payload
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            frame = publish(core, task, SOURCES, admission(caps, response_id),
                candidate=candidate, observations=OBSERVATIONS, **trusted)
        self.assertEqual(core.payload, before)
        self.assertTrue(frame.knowledge_delivered)
        self.accept(frame.data, candidate, trusted, caps, response_id)
        return frame

    def test_production_single_frame_and_fragmented_publication_match_independent_expected(self):
        candidate, trusted, report = case()
        single = self.produce(candidate, trusted, report)
        self.assertIs(type(single), FrozenFrame)
        self.assertEqual(single.data, encode(report))
        caps = {**CAPS, "max_frame_bytes": 2048}
        split = self.produce(candidate, trusted, report, caps)
        self.assertIs(type(split), FrozenTransfer)
        decoded, manifest = reassemble(split.data, admission(caps))
        self.assertEqual(decoded, encode(report))
        self.assertEqual(manifest["manifest"]["count"], len(split.frames) - 1)
        self.assertLessEqual(max(map(len, split.frames)), 2048)
        self.assertLessEqual(len(split.data), CAPS["max_transfer_bytes"])

    def test_production_partial_and_failure_publications_preserve_complete_structure(self):
        for failures, status in ((None, "partial"),
                ([{"kind": "blocked", "reason": "blocked"}], "blocked"),
                ([{"kind": "failed", "reason": "failed"}], "failed")):
            candidate, trusted, report = case(lambda c: c["conclusions"].pop(), failures)
            for frame_bytes in (22016, 2048):
                with self.subTest(status=status, frame_bytes=frame_bytes):
                    frame = self.produce(candidate, trusted, report,
                        {**CAPS, "max_frame_bytes": frame_bytes})
                    result = consume_frame(frame.data, SOURCES,
                        admission=admission({**CAPS, "max_frame_bytes": frame_bytes}),
                        candidate=candidate, observations=OBSERVATIONS, **trusted)
                    self.assertEqual(result.status, status)
                    self.assertEqual(result.publication["core"]["package"]["status"], "complete")

    def test_production_measured_four_limits_exact_pass_and_lower_fail(self):
        candidate, trusted, report = case()
        frame = self.produce(candidate, trusted, report, {**CAPS, "max_frame_bytes": 2048})
        decoded, _ = reassemble(frame.data, admission({**CAPS, "max_frame_bytes": 2048}))
        sizes = {"max_frame_bytes": max(map(len, frame.frames)),
                 "max_transfer_bytes": len(frame.data), "max_decoded_bytes": len(decoded),
                 "max_chunks": len(frame.frames) - 1}
        exact = {**CAPS, **sizes}
        self.assertEqual(reassemble(frame.data, admission(exact))[0], decoded)
        self.accept(frame.data, candidate, trusted, exact)
        for key, size in sizes.items():
            with self.subTest(metric=key), self.assertRaises(ValueError):
                consume_frame(frame.data, SOURCES, admission=admission({**exact, key: size - 1}),
                    candidate=candidate, observations=OBSERVATIONS, **trusted)
        for changes in ({"max_decoded_bytes": len(decoded) - 1}, {"max_chunks": 1},
                        {"max_transfer_bytes": 2048}, {"chunking": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.produce(candidate, trusted, report,
                    {**CAPS, "max_frame_bytes": 2048, **changes})

    def test_publication_rejects_changed_raw_output_failure_observations_and_task(self):
        candidate, trusted, report = case()
        core = SealedCore(canonical(report["core"]))
        original = build_task(candidate, core, SOURCES, **trusted)
        altered = copy.deepcopy(candidate)
        altered["summary"] = "changed raw output"
        for task, raw_candidate, observations, failures in (
                (original, altered, OBSERVATIONS, None),
                (original, candidate, {"text": "x" * 513}, None),
                (original, candidate, [], None),
                (original, candidate, OBSERVATIONS, [{"kind": "failed", "reason": "changed"}]),
                ({**original, "summary": "forged"}, candidate, OBSERVATIONS, None)):
            with self.subTest(observations=type(observations).__name__), self.assertRaises(ValueError):
                publish(core, task, SOURCES, admission(), candidate=raw_candidate,
                    observations=observations, **{**trusted, "failures": failures})
        with self.assertRaises(ValueError):
            publish(core, original, {"sample.py": SOURCES["sample.py"] + b"# drift\n"},
                admission(), candidate=candidate, observations=OBSERVATIONS, **trusted)

    def test_direct_consumer_rejects_valid_resealed_task_with_different_saved_output(self):
        candidate, trusted, report = case()
        from solo_distill.wire_v3 import build_publication
        core = SealedCore(canonical(report["core"]))
        mutations = [lambda c: c.update(summary="伪造的完整摘要"),
                     lambda c: c["conclusions"][0].update(text="伪造的完整结论"),
                     lambda c: c.update(unknowns=[]),
                     lambda c: c["conclusions"].pop()]
        for number, mutate in enumerate(mutations):
            changed_candidate = copy.deepcopy(candidate)
            mutate(changed_candidate)
            changed_task = build_task(changed_candidate, core, SOURCES, **trusted)
            changed_report = {**report, "task_evidence": changed_task,
                              "status": changed_task["status"]}
            for raw in (encode(changed_report), manual_chunks(changed_report)[0]):
                with self.subTest(number=number, frames=raw.count(b"\n")):
                    # 形状、来源引用和摘要均合法，必须进入生产可信输入认证。
                    self.assertEqual(reassemble(raw, admission())[0], encode(changed_report))
                    with (
                        patch("socket.socket", side_effect=AssertionError("禁止联网")),
                        patch("solo_distill.consumer_v3.build_publication",
                              wraps=build_publication) as authentication,
                    ):
                        with self.assertRaisesRegex(ValueError,
                                "v3_publication_raw_candidate_or_task_conflict"):
                            consume_frame(raw, SOURCES, admission=admission(),
                                candidate=candidate, observations=OBSERVATIONS, **trusted)
                        authentication.assert_called_once()

    def test_direct_consumer_rejects_external_source_scope_selection_post_and_failures(self):
        candidate, trusted, report = case()
        altered_candidate = copy.deepcopy(candidate)
        altered_candidate["summary"] = "不同的原始生成输出"
        altered_dimensions = copy.deepcopy(trusted["dimensions"])
        altered_dimensions[0]["question"] = "不同的维度问题"
        variants = [
            (SOURCES, altered_candidate, trusted, OBSERVATIONS),
            ({"sample.py": SOURCES["sample.py"] + b"# drift\n"}, candidate,
             trusted, OBSERVATIONS),
            ({**SOURCES, "extra.py": b""}, candidate, trusted, OBSERVATIONS),
            ({}, candidate, trusted, OBSERVATIONS),
            (SOURCES, candidate, trusted, {"scope": "different"}),
        ]
        for key, value in (
            ("project_id", "changed"), ("snapshot_id", "changed"),
            ("request_id", "changed"), ("question", "changed"),
            ("selection_id", "changed"), ("required_core_unit_ids", []),
            ("dimensions", altered_dimensions), ("post", trusted["post"] + b"\n"),
            ("frozen_coverage", {}),
            ("failures", [{"kind": "failed", "reason": "external_failure"}]),
        ):
            variants.append((SOURCES, candidate, {**trusted, key: value}, OBSERVATIONS))
        for raw in (encode(report), manual_chunks(report)[0]):
            for number, (files, raw_candidate, inputs, observations) in enumerate(variants):
                with (
                    self.subTest(number=number, frames=raw.count(b"\n")),
                    patch("socket.socket", side_effect=AssertionError("禁止联网")),
                    self.assertRaises(ValueError),
                ):
                    consume_frame(raw, files, admission=admission(),
                        candidate=raw_candidate, observations=observations, **inputs)

    def test_consumption_result_and_publication_views_are_detached(self):
        candidate, trusted, report = case()
        raw = encode(report)
        value = consume_frame(raw, SOURCES, admission=admission(), candidate=candidate,
                              observations=OBSERVATIONS, **trusted)
        first = value.publication
        first["task_evidence"]["facts"].clear()
        candidate["conclusions"].clear()
        self.assertEqual(value.publication, report)
        self.assertEqual(value.publication_bytes, canonical(report))

    def test_explicit_admission_rejects_old_scenario_protocol_and_unnegotiated_transport(self):
        caps = TransportCapabilities.from_dict(CAPS)
        for protocol, scenario, identity, budget, transport in (
                ("unknown", "A", 7, 22016, caps),
                ("2024-11-05", "B", 7, 22016, caps),
                ("2024-11-05", "A", True, 22016, caps),
                ("2024-11-05", "A", 7, True, caps),
                ("2024-11-05", "A", 7, 0, caps),
                ("2024-11-05", "A", 7, 22016, None),
                ("2024-11-05", "A", 7, 22016, CAPS)):
            with self.subTest(scenario=scenario, budget=budget), self.assertRaises(ValueError):
                Admission(protocol, scenario, identity, budget, transport)
        from solo_distill.wire_v2 import Admission as OldAdmission
        candidate, trusted, report = case()
        with self.assertRaises(ValueError):
            consume_frame(encode(report), SOURCES,
                admission=OldAdmission("2024-11-05", "A", 7, 22016, caps),
                candidate=candidate, observations=OBSERVATIONS, **trusted)

    def test_production_does_not_import_or_call_independent_oracles(self):
        candidate, trusted, report = case()
        core = SealedCore(canonical(report["core"]))
        task = build_task(candidate, core, SOURCES, **trusted)
        with (
            patch("schema_v3_wire_oracle.expected_publication", side_effect=AssertionError("禁止 Oracle")),
            patch("schema_v3_core_oracle.expected_core", side_effect=AssertionError("禁止 Oracle")),
            patch("schema_v3_task_oracle.expected_task", side_effect=AssertionError("禁止 Oracle")),
            patch("socket.socket", side_effect=AssertionError("禁止联网")),
        ):
            frame = publish(core, task, SOURCES, admission(), candidate=candidate,
                            observations=OBSERVATIONS, **trusted)
            result = consume_frame(frame.data, SOURCES, admission=admission(), candidate=candidate,
                                   observations=OBSERVATIONS, **trusted)
        self.assertEqual(result.publication, report)

    def test_tick_cancellation_reaches_publish_reassemble_and_consume(self):
        candidate, trusted, report = case()
        core = SealedCore(canonical(report["core"]))
        def stop():
            raise RuntimeError("cancelled_or_expired")
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_expired"):
            publish(core, report["task_evidence"], SOURCES, admission(), candidate=candidate,
                    observations=OBSERVATIONS, **{**trusted, "tick": stop})
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_expired"):
            reassemble(manual_chunks(report)[0], admission(), tick=stop)
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_expired"):
            consume_frame(encode(report), SOURCES, admission=admission(), candidate=candidate,
                          observations=OBSERVATIONS, **{**trusted, "tick": stop})

    def test_real_pipe_lossless_write_single_use_and_joined_reader(self):
        candidate, trusted, report = case()
        frame = self.produce(candidate, trusted, report, {**CAPS, "max_frame_bytes": 2048})
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        captured = bytearray()
        errors = []
        finished = threading.Event()
        def drain():
            end = time.monotonic() + 3
            try:
                while time.monotonic() < end:
                    try:
                        part = os.read(read_fd, 4096)
                    except BlockingIOError:
                        if finished.is_set() and len(captured) == len(frame.data):
                            return
                        time.sleep(0.001)
                        continue
                    if not part:
                        return
                    captured.extend(part)
                errors.append("reader_deadline")
            except OSError as error:
                errors.append(str(error))
        worker = threading.Thread(target=drain, daemon=False)
        worker.start()
        try:
            writer = FrameWriter(write_fd)
            receipt = writer.send(frame, deadline=time.monotonic() + 2,
                                  cancelled=threading.Event())
            self.assertEqual(receipt.written, len(frame.data))
            with self.assertRaises(DeliveryBlocked):
                writer.send(frame, deadline=time.monotonic() + 2, cancelled=threading.Event())
        finally:
            os.close(write_fd)
            finished.set()
            worker.join(4)
            os.close(read_fd)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(bytes(captured), frame.data)
        checked = independent_receive(bytes(captured), SOURCES,
            {**CAPS, "max_frame_bytes": 2048}, protocol_version="2024-11-05",
            response_id=7, return_bytes=22016, candidate=candidate,
            observations=OBSERVATIONS, **trusted)
        self.assertEqual(checked["publication"], report)

    def test_existing_v2_publisher_reassembler_and_consumer_reject_v3(self):
        candidate, trusted, report = case()
        from solo_distill.adaptive_wire import reassemble as old_reassemble
        from solo_distill.consumer_v2 import consume_frame as old_consume
        from solo_distill.wire_v2 import Admission as OldAdmission, publish as old_publish
        core = SealedCore(canonical(report["core"]))
        caps = TransportCapabilities.from_dict(CAPS)
        with self.assertRaises(ValueError):
            old_publish(core, OldAdmission("2024-11-05", "A", 7, 22016, caps))
        with self.assertRaises(ValueError):
            old_reassemble(manual_chunks(report)[0], caps, request_id=7, frame_budget=22016)
        for raw in (encode(report), manual_chunks(report)[0]):
            with self.subTest(frames=raw.count(b"\n")), self.assertRaises(ValueError):
                old_consume(raw, SOURCES, protocol_version="2024-11-05", request_id=7,
                    scenario="A", return_bytes=22016, project_id="fixture",
                    policy_id="adaptive-backpressure-v1", transport=caps)


if __name__ == "__main__":
    unittest.main()
