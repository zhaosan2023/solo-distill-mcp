"""显式无损表示的传输及服务集成门禁；先保存负控，再适配生产。

仅使用隔离源码、手工切片和真实离线 stdio，不访问网关、不占用
三个真实来源样本或唯一在线业务，不证明人工语义或宿主加载。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch

from schema_v3_representation_oracle import ENCODING, pack as independent_pack
from schema_v3_wire_oracle import receive as independent_receive
from test_schema_v3_wire_oracle import CAPS, OBSERVATIONS, SOURCES, case, encode
from test_schema_v3_stdio import Fixture
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v3 import consume_frame
from solo_distill.core_v3 import SealedCore
from solo_distill.macro_v3 import canonical
from solo_distill.task_v3 import build_task
from solo_distill.wire_v3 import Admission, publish_v3, reassemble


def admission(caps=None, encoding=ENCODING):
    return Admission("2024-11-05", "A", 7, 22016,
        TransportCapabilities.from_dict(CAPS if caps is None else caps),
        wire_encoding=encoding)


def compact_response(report, text=None):
    text = canonical(independent_pack(report)).decode("utf-8") if text is None else text
    return canonical({"jsonrpc": "2.0", "id": 7, "result": {
        "content": [{"type": "text", "text": text}],
        "isError": report["status"] in {"blocked", "failed"}}}) + b"\n"


def compact_chunks(report, width=500):
    decoded = compact_response(report)
    core, task = report["core"], report["task_evidence"]
    binding = {"project_id": core["package"]["project_id"],
        "snapshot_id": core["package"]["snapshot_id"], "input_id": core["input_id"],
        "core_id": core["core_id"], "selection_id": core["selection_id"],
        "evidence_id": task["evidence_id"], "task_request_id": task["request_id"],
        "question_id": task["question_id"], "post_sha256": task["post_sha256"],
        "policy_id": "adaptive-backpressure-v1", "contract_version": 3,
        "encoding": "base64-jsonrpc-wire-v1", "wire_encoding": ENCODING}
    pieces = [decoded[offset:offset + width] for offset in range(0, len(decoded), width)]
    frames = []
    for index, piece in enumerate(pieces):
        frames.append(canonical({"jsonrpc": "2.0",
            "method": "notifications/solo_distill/chunk-v1", "params": {
                **binding, "count": len(pieces), "index": index,
                "chunk_bytes": len(piece), "chunk_sha256": hashlib.sha256(piece).hexdigest(),
                "data": base64.b64encode(piece).decode("ascii")}}) + b"\n")
    frames.append(encode({"contract_version": 3, "kind": "chunk_manifest",
        "status": report["status"], "manifest": {**binding, "count": len(pieces),
            "decoded_bytes": len(decoded), "sha256": hashlib.sha256(decoded).hexdigest()}}))
    return b"".join(frames), decoded


class RepresentationWireTests(unittest.TestCase):
    def accept(self, raw, candidate, trusted, caps=None):
        capabilities = CAPS if caps is None else caps
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            production = consume_frame(raw, SOURCES, admission=admission(capabilities),
                candidate=candidate, observations=OBSERVATIONS, **trusted)
            expected = independent_receive(raw, SOURCES, capabilities,
                protocol_version="2024-11-05", response_id=7, return_bytes=22016,
                wire_encoding=ENCODING, candidate=candidate, observations=OBSERVATIONS, **trusted)
        self.assertEqual(production.publication, expected["publication"])
        self.assertEqual(production.decoded_bytes, expected["decoded_bytes"])
        self.assertEqual(production.transfer_bytes, len(raw))
        return production

    def reject(self, raw, candidate, trusted, caps=None, encoding=ENCODING):
        capabilities = CAPS if caps is None else caps
        with self.assertRaises(ValueError):
            consume_frame(raw, SOURCES, admission=admission(capabilities, encoding),
                candidate=candidate, observations=OBSERVATIONS, **trusted)
        with self.assertRaises(ValueError):
            independent_receive(raw, SOURCES, capabilities,
                protocol_version="2024-11-05", response_id=7, return_bytes=22016,
                wire_encoding=encoding, candidate=candidate, observations=OBSERVATIONS, **trusted)

    def test_hand_encoded_single_and_manual_chunks_authenticate_full_publication(self):
        candidate, trusted, report = case()
        raw, decoded = compact_chunks(report)
        self.assertEqual(reassemble(raw, admission())[0], decoded)
        for transport in (compact_response(report), raw):
            result = self.accept(transport, candidate, trusted)
            self.assertEqual(result.publication_bytes, canonical(report))
            self.assertEqual(result.publication["core"]["core_id"], report["core"]["core_id"])

    def test_production_single_and_chunked_encoding_matches_independent_full_object(self):
        candidate, trusted, report = case()
        core = SealedCore(canonical(report["core"]))
        before = core.payload
        task = build_task(candidate, core, SOURCES, **trusted)
        for frame_limit in (22016, 2048):
            caps = {**CAPS, "max_frame_bytes": frame_limit}
            frame = publish_v3(core, task, SOURCES, admission(caps), candidate=candidate,
                observations=OBSERVATIONS, **trusted)
            decoded, _ = reassemble(frame.data, admission(caps))
            self.assertEqual(decoded, compact_response(report))
            self.assertEqual(self.accept(frame.data, candidate, trusted, caps).publication, report)
        legacy = publish_v3(core, task, SOURCES, admission(encoding="json"),
            candidate=candidate, observations=OBSERVATIONS, **trusted)
        self.assertEqual(legacy.data, encode(report))
        self.assertEqual(core.payload, before)
        self.assertEqual(core.wire_encoding, "json")

    def test_partial_blocked_and_failed_keep_full_structure_and_unknowns(self):
        for failures in (None, [{"kind": "blocked", "reason": "blocked"}],
                         [{"kind": "failed", "reason": "failed"}]):
            candidate, trusted, report = case(lambda value: value["conclusions"].pop(), failures)
            for raw in (compact_response(report), compact_chunks(report)[0]):
                result = self.accept(raw, candidate, trusted)
                self.assertEqual(result.status, report["status"])
                self.assertEqual(result.publication["core"]["package"]["status"], "complete")
                self.assertEqual(result.publication["task_evidence"]["unknowns"],
                                 report["task_evidence"]["unknowns"])

    def test_four_actual_limits_equal_pass_and_one_byte_or_chunk_lower_rejects(self):
        candidate, trusted, report = case()
        raw, decoded = compact_chunks(report)
        exact = {**CAPS, "max_frame_bytes": max(map(len, raw.splitlines(keepends=True))),
            "max_transfer_bytes": len(raw), "max_decoded_bytes": len(decoded),
            "max_chunks": raw.count(b"\n") - 1}
        self.accept(raw, candidate, trusted, exact)
        for key in ("max_frame_bytes", "max_transfer_bytes", "max_decoded_bytes", "max_chunks"):
            with self.subTest(key=key):
                self.reject(raw, candidate, trusted, {**exact, key: exact[key] - 1})
        self.reject(raw, candidate, trusted, {**CAPS, "chunking": False})

    def test_encoding_mismatch_and_unknown_admission_are_not_silently_downgraded(self):
        candidate, trusted, report = case()
        self.reject(compact_response(report), candidate, trusted, encoding="json")
        self.reject(encode(report), candidate, trusted)
        self.reject(compact_chunks(report)[0], candidate, trusted, encoding="json")
        for encoding in (None, True, 3, "unknown"):
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                admission(encoding=encoding)

    def test_duplicate_json_keys_numbers_and_noncanonical_representation_reject(self):
        candidate, trusted, report = case()
        document = independent_pack(report)
        original = canonical(document).decode("utf-8")
        invalid_texts = [original.replace('"encoding":', '"encoding":"wrong","encoding":', 1),
            original.replace('"value":', '"extra":0,"value":', 1),
            original.replace('"value":', '"extra":1.0,"value":', 1),
            original.replace('"value":', '"extra":NaN,"value":', 1)]
        unused = copy.deepcopy(document)
        unused["strings"].append(unused["strings"][-1] + "unused")
        invalid_texts.append(canonical(unused).decode("utf-8"))
        for number, text in enumerate(invalid_texts):
            with self.subTest(number=number):
                self.reject(compact_response(report, text), candidate, trusted)

    def test_changed_raw_candidate_and_external_failures_remain_rejected(self):
        candidate, trusted, report = case()
        altered = copy.deepcopy(candidate)
        altered["summary"] = "不同的原始生成输出"
        for raw in (compact_response(report), compact_chunks(report)[0]):
            self.reject(raw, altered, trusted)
            self.reject(raw, candidate, {**trusted,
                "failures": [{"kind": "failed", "reason": "包外失败"}]})

    def test_cancellation_reaches_compact_decode_and_publication(self):
        candidate, trusted, report = case()
        core = SealedCore(canonical(report["core"]))
        def stop():
            raise RuntimeError("cancelled_or_deadline")
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_deadline"):
            reassemble(compact_chunks(report)[0], admission(), tick=stop)
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_deadline"):
            publish_v3(core, report["task_evidence"], SOURCES, admission(), candidate=candidate,
                observations=OBSERVATIONS, **{**trusted, "tick": stop})


class RepresentationServiceTests(Fixture):
    def test_invalid_representation_rejected_before_preparation(self):
        from solo_distill.extractor import InputError
        for encoding in (None, True, 3, "unknown"):
            with self.subTest(encoding=encoding):
                with patch("solo_distill.service_v3.prepare_snapshot",
                           side_effect=AssertionError("先核验协商")):
                    with self.assertRaises(InputError):
                        self.distiller().distill_frame(self.arguments(wire_encoding=encoding), rpc_id=3)

    def test_real_offline_stdio_compact_consumers_and_natural_exit(self):
        from solo_distill.client import StdioClient
        from solo_distill.preparation_v3 import prepare_snapshot
        arguments = self.arguments(wire_encoding=ENCODING)
        client = StdioClient(self.root, online=False, v3=True, deadline=time.monotonic() + 10)
        try:
            tools = client.initialize()
            properties = tools["result"]["tools"][0]["inputSchema"]["properties"]
            self.assertIn(ENCODING, properties["wire_encoding"]["enum"])
            prepared = prepare_snapshot(project=self.project, include=(".",),
                question=arguments["question"], dimensions=arguments["dimensions"],
                deadline=client.deadline, cancelled=threading.Event())
            candidate = {"summary": "", "conclusions": [], "unknowns": []}
            trusted = {"project_id": "fixture", "snapshot_id": prepared.report["snapshot_id"],
                "required_core_unit_ids": prepared.selection["core_unit_ids"],
                "selection_id": prepared.selection["selection_id"], "request_id": arguments["request_id"],
                "question": arguments["question"], "dimensions": arguments["dimensions"],
                "post": prepared.post, "frozen_coverage": prepared.report["coverage"], "failures": []}
            result = client.call_v3(arguments, raw_files=self.files, candidate=candidate, **trusted)
            expected = independent_receive(client.last_frame, self.files, arguments["transport"],
                protocol_version="2024-11-05", response_id=3, return_bytes=2048,
                wire_encoding=ENCODING, candidate=candidate, **trusted)
            self.assertEqual(result.publication, expected["publication"])
            self.assertGreater(result.notification_chunks, 0)
        finally:
            client.close()
        self.assertEqual(client.exit_code, 0)
        self.assertEqual(client.cleanup, "confirmed_natural_exit")
        self.assertEqual(client.stderr, b"")
        trace = self.trace()
        self.assertEqual(trace["gateway_requests"], [])
        self.assertEqual(trace["core"], result.publication["core"])
        delivery = json.loads((self.root / "verification/.scratch/stdio-fixture.delivery.full.json").read_bytes())
        self.assertEqual(delivery["written"], len(client.last_frame))
        for path, raw in self.files.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
