"""操作内完整发布解码复用回归；保留公开双返回值和独立认证。

四种协商、普通响应和手工分片均从独立规则构造；不访问网关、
不执行来源、不占用真实三样本或唯一在线业务。次数不是耗时证据。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import unittest
from unittest.mock import patch

import schema_v3_wire_oracle as oracle
from schema_v3_lz_oracle import pack as lz_pack
from schema_v3_representation_oracle import pack as columnar_pack
from schema_v3_subtree_oracle import pack as subtree_pack
from test_schema_v3_wire_oracle import CAPS, OBSERVATIONS, SOURCES, case, encode
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v3 import consume_frame
import solo_distill.wire_v3 as wire

ENCODINGS = ("json", "dictionary-columnar-v3-1", "dictionary-columnar-v3-2", "lz-json-v3-1")
PACKERS = {"dictionary-columnar-v3-1": columnar_pack,
           "dictionary-columnar-v3-2": subtree_pack, "lz-json-v3-1": lz_pack}


def admission(encoding):
    return wire.Admission("2024-11-05", "A", 7, 22016,
                          TransportCapabilities.from_dict(CAPS), wire_encoding=encoding)


def transports(report, encoding):
    represented = report if encoding == "json" else PACKERS[encoding](report)
    decoded = oracle.canonical({"jsonrpc": "2.0", "id": 7, "result": {
        "content": [{"type": "text", "text": oracle.canonical(represented).decode("utf-8")}],
        "isError": report["status"] in {"blocked", "failed"}}}) + b"\n"
    core, task = report["core"], report["task_evidence"]
    binding = {"project_id": core["package"]["project_id"],
        "snapshot_id": core["package"]["snapshot_id"], "input_id": core["input_id"],
        "core_id": core["core_id"], "selection_id": core["selection_id"],
        "evidence_id": task["evidence_id"], "task_request_id": task["request_id"],
        "question_id": task["question_id"], "post_sha256": task["post_sha256"],
        "policy_id": "adaptive-backpressure-v1", "contract_version": 3,
        "encoding": "base64-jsonrpc-wire-v1", "wire_encoding": encoding}
    pieces = [decoded[start:start + 500] for start in range(0, len(decoded), 500)]
    frames = [oracle.canonical({"jsonrpc": "2.0",
        "method": "notifications/solo_distill/chunk-v1", "params": {
            **binding, "count": len(pieces), "index": index,
            "chunk_bytes": len(piece), "chunk_sha256": hashlib.sha256(piece).hexdigest(),
            "data": base64.b64encode(piece).decode("ascii")}}) + b"\n"
        for index, piece in enumerate(pieces)]
    frames.append(encode({"contract_version": 3, "kind": "chunk_manifest",
        "status": report["status"], "manifest": {**binding, "count": len(pieces),
            "decoded_bytes": len(decoded), "sha256": hashlib.sha256(decoded).hexdigest()}}))
    return decoded, (decoded, b"".join(frames))


def independent(raw, encoding, candidate, trusted, sources=SOURCES):
    return oracle.receive(raw, sources, CAPS, protocol_version="2024-11-05",
        response_id=7, return_bytes=22016, wire_encoding=encoding,
        candidate=candidate, observations=OBSERVATIONS, **trusted)


class DecodeReuseTests(unittest.TestCase):
    def test_production_consumer_decodes_publication_once(self):
        candidate, trusted, report = case()
        for encoding in ENCODINGS:
            decoded, forms = transports(report, encoding)
            for raw in forms:
                with self.subTest(encoding=encoding, frames=raw.count(b"\n")):
                    with patch.object(wire, "decode_report", wraps=wire.decode_report) as decoder:
                        # 覆盖消费模块已导入的别名，避免漏计第二次完整解码。
                        with patch("solo_distill.consumer_v3.decode_report",
                                   side_effect=decoder, create=True):
                            result = consume_frame(raw, SOURCES, admission=admission(encoding),
                                candidate=candidate, observations=OBSERVATIONS, **trusted)
                    # 清单仍单独认证；完整发布只有一次有界解码及规范重编码。
                    publication_calls = [call for call in decoder.call_args_list if call.args[0] == decoded]
                    self.assertEqual(len(publication_calls), 1)
                    self.assertEqual(decoder.call_count, 1 if raw is decoded else 2)
                    self.assertEqual(result.publication, report)
                    self.assertEqual(result.decoded_bytes, len(decoded))
                    self.assertEqual(result.transfer_bytes, len(raw))

    def test_independent_consumer_decodes_publication_once(self):
        candidate, trusted, report = case()
        for encoding in ENCODINGS:
            decoded, forms = transports(report, encoding)
            for raw in forms:
                with self.subTest(encoding=encoding, frames=raw.count(b"\n")):
                    with patch.object(oracle, "_envelope", wraps=oracle._envelope) as decoder:
                        result = independent(raw, encoding, candidate, trusted)
                    publication_calls = [call for call in decoder.call_args_list if call.args[0] == decoded]
                    self.assertEqual(len(publication_calls), 1)
                    self.assertEqual(decoder.call_count, 1 if raw is decoded else 2)
                    self.assertEqual(result["publication"], report)
                    self.assertEqual(result["decoded_bytes"], len(decoded))
                    self.assertEqual(result["transfer_bytes"], len(raw))

    def test_public_reassembly_preserves_two_values_and_exact_bytes(self):
        _, _, report = case()
        for encoding in ENCODINGS:
            decoded, forms = transports(report, encoding)
            for raw in forms:
                with self.subTest(encoding=encoding, frames=raw.count(b"\n")):
                    production = wire.reassemble(raw, admission(encoding))
                    expected = oracle.reassemble(raw, CAPS, response_id=7,
                        return_bytes=22016, wire_encoding=encoding)
                    self.assertIs(type(production), tuple)
                    self.assertIs(type(expected), tuple)
                    self.assertEqual(len(production), 2)
                    self.assertEqual(len(expected), 2)
                    self.assertEqual(production, expected)
                    self.assertEqual(production[0], decoded)

    def test_next_call_still_authenticates_external_candidate_sources_post_and_failures(self):
        candidate, trusted, report = case()
        changed = copy.deepcopy(candidate)
        changed["summary"] = "不同的包外原始生成对象"
        variants = [(SOURCES, changed, trusted),
            ({"sample.py": SOURCES["sample.py"] + b"# drift\n"}, candidate, trusted),
            (SOURCES, candidate, {**trusted, "post": trusted["post"] + b"\n"}),
            (SOURCES, candidate, {**trusted, "failures": [{"kind": "failed", "reason": "external"}]})]
        for encoding in ENCODINGS:
            _, forms = transports(report, encoding)
            for raw in forms:
                first = consume_frame(raw, SOURCES, admission=admission(encoding),
                    candidate=candidate, observations=OBSERVATIONS, **trusted)
                self.assertEqual(first.publication, report)
                self.assertEqual(independent(raw, encoding, candidate, trusted)["publication"], report)
                for number, (sources, current_candidate, current_trusted) in enumerate(variants):
                    with self.subTest(encoding=encoding, frames=raw.count(b"\n"), variant=number):
                        with self.assertRaises(ValueError):
                            consume_frame(raw, sources, admission=admission(encoding),
                                candidate=current_candidate, observations=OBSERVATIONS, **current_trusted)
                        with self.assertRaises(ValueError):
                            independent(raw, encoding, current_candidate, current_trusted, sources)

    def test_cancel_after_full_decode_still_prevents_acceptance(self):
        candidate, trusted, report = case()
        for encoding in ENCODINGS:
            decoded, forms = transports(report, encoding)
            for raw in forms:
                for module, name, receiver in ((wire, "decode_report", "production"),
                                              (oracle, "_envelope", "independent")):
                    cancelled = False
                    original = getattr(module, name)

                    def stopped():
                        if cancelled:
                            raise RuntimeError("cancel_after_publication_decode")

                    def decoding(*args, **kwargs):
                        nonlocal cancelled
                        value = original(*args, **kwargs)
                        if args[0] == decoded:
                            cancelled = True
                        return value

                    with self.subTest(encoding=encoding, receiver=receiver, frames=raw.count(b"\n")):
                        with patch.object(module, name, side_effect=decoding):
                            with self.assertRaisesRegex(RuntimeError, "cancel_after_publication_decode"):
                                if receiver == "production":
                                    consume_frame(raw, SOURCES, admission=admission(encoding),
                                        candidate=candidate, observations=OBSERVATIONS,
                                        **{**trusted, "tick": stopped})
                                else:
                                    independent(raw, encoding, candidate, {**trusted, "tick": stopped})


if __name__ == "__main__":
    unittest.main()
