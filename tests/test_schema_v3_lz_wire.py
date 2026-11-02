"""新增确定性字节表示的传输、双消费及真实离线服务定向门禁。

保留既有通用集成断言；格式专属篡改按新增封闭头部及令牌规则覆盖。
只用隔离来源和离线服务，不占用真实冷预检或唯一在线业务。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import test_schema_v3_representation_wire as rules
from schema_v3_lz_oracle import ENCODING, pack as independent_pack
from schema_v3_representation_oracle import ENCODING as COLUMNAR_ENCODING
from schema_v3_representation_oracle import pack as columnar_pack
from schema_v3_subtree_oracle import ENCODING as SUBTREE_ENCODING
from schema_v3_subtree_oracle import pack as subtree_pack
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.wire_v3 import Admission, WIRE_ENCODINGS, publish_v3


def admission(caps=None, encoding=ENCODING):
    return Admission("2024-11-05", "A", 7, 22016,
        TransportCapabilities.from_dict(rules.CAPS if caps is None else caps),
        wire_encoding=encoding)


def install_rules(test):
    for name, value in (("ENCODING", ENCODING),
                        ("independent_pack", independent_pack),
                        ("admission", admission)):
        replacement = patch.object(rules, name, value)
        replacement.start()
        test.addCleanup(replacement.stop)


def text_for(document):
    return rules.canonical(document).decode("utf-8")


class LzWireTests(rules.RepresentationWireTests):
    def setUp(self):
        install_rules(self)

    def reject(self, raw, candidate, trusted, caps=None, encoding=ENCODING):
        # 父方法默认值已绑定前版；必须显式传本组版本，不能靠协商错误假通过。
        return super().reject(raw, candidate, trusted, caps=caps, encoding=encoding)

    def test_duplicate_json_keys_numbers_and_noncanonical_representation_reject(self):
        candidate, trusted, report = rules.case()
        document = independent_pack(report)
        original = text_for(document)
        invalid_texts = [
            original.replace('"encoding":', '"encoding":"wrong","encoding":', 1),
            original.replace('"raw_bytes":', '"extra":0,"raw_bytes":', 1),
            original.replace('"raw_bytes":', '"extra":1.0,"raw_bytes":', 1),
            original.replace('"raw_bytes":', '"extra":NaN,"raw_bytes":', 1),
        ]
        # 完整原对象仍可还原，但故意错过所有复制机会，必须拒绝非规范令牌。
        raw = rules.canonical(report)
        tokens = b"".join(bytes([len(part) - 1]) + part
                           for start in range(0, len(raw), 128)
                           for part in [raw[start:start + 128]])
        alternative = {**document, "data": base64.b64encode(tokens).decode("ascii")}
        self.assertNotEqual(alternative, document)
        invalid_texts.append(text_for(alternative))
        for number, text in enumerate(invalid_texts):
            with self.subTest(number=number):
                self.reject(rules.compact_response(report, text), candidate, trusted)

    def test_all_predecessors_preserve_exact_bytes_and_complete_dual_consumption(self):
        candidate, trusted, report = rules.case()
        core = rules.SealedCore(rules.canonical(report["core"]))
        for encoding, represented in (("json", report),
                (COLUMNAR_ENCODING, columnar_pack(report)),
                (SUBTREE_ENCODING, subtree_pack(report))):
            with self.subTest(encoding=encoding):
                expected = rules.canonical({"jsonrpc": "2.0", "id": 7, "result": {
                    "content": [{"type": "text", "text": text_for(represented)}],
                    "isError": report["status"] in {"blocked", "failed"}}}) + b"\n"
                admitted = admission(encoding=encoding)
                frame = publish_v3(core, report["task_evidence"], rules.SOURCES, admitted,
                    candidate=candidate, observations=rules.OBSERVATIONS, **trusted)
                self.assertEqual(frame.data, expected)
                actual = rules.consume_frame(expected, rules.SOURCES, admission=admitted,
                    candidate=candidate, observations=rules.OBSERVATIONS, **trusted)
                independent = rules.independent_receive(expected, rules.SOURCES, rules.CAPS,
                    protocol_version="2024-11-05", response_id=7, return_bytes=22016,
                    wire_encoding=encoding, candidate=candidate,
                    observations=rules.OBSERVATIONS, **trusted)
                self.assertEqual(actual.publication, report)
                self.assertEqual(actual.publication, independent["publication"])
        self.assertEqual(WIRE_ENCODINGS,
                         ("json", COLUMNAR_ENCODING, SUBTREE_ENCODING, ENCODING))

    def test_cross_version_single_and_chunked_mixes_do_not_fallback(self):
        candidate, trusted, report = rules.case()
        for encoding, represented in (("json", report),
                (COLUMNAR_ENCODING, columnar_pack(report)),
                (SUBTREE_ENCODING, subtree_pack(report))):
            with self.subTest(encoding=encoding):
                self.reject(rules.compact_response(report, text_for(represented)), candidate, trusted)
                self.reject(rules.compact_response(report), candidate, trusted, encoding=encoding)
                self.reject(rules.compact_chunks(report)[0], candidate, trusted, encoding=encoding)

    def test_header_digest_length_base64_and_token_mutations_reach_both_receivers(self):
        candidate, trusted, report = rules.case()
        original = independent_pack(report)
        tokens = base64.b64decode(original["data"])
        alterations = [
            {"raw_bytes": True}, {"raw_bytes": original["raw_bytes"] - 1},
            {"raw_bytes": original["raw_bytes"] + 1}, {"raw_bytes": 8388609},
            {"sha256": "0" * 64}, {"sha256": "A" * 64},
            {"data": original["data"] + "\n"}, {"data": "!!!!"},
            {"data": base64.b64encode(tokens[:-1]).decode("ascii")},
            {"data": base64.b64encode(b"\x80\x00\x00").decode("ascii")},
            {"extra": 0}, {"encoding": SUBTREE_ENCODING},
        ]
        for number, changes in enumerate(alterations):
            with self.subTest(number=number):
                self.reject(rules.compact_response(report, text_for({**original, **changes})),
                            candidate, trusted)

    def test_each_chunk_and_manifest_bind_the_exact_negotiated_encoding(self):
        candidate, trusted, report = rules.case()
        raw, _ = rules.compact_chunks(report)
        frames = [json.loads(line) for line in raw.splitlines()]
        for encoding in ("json", COLUMNAR_ENCODING, SUBTREE_ENCODING):
            for index in range(len(frames)):
                altered = copy.deepcopy(frames)
                if index < len(frames) - 1:
                    altered[index]["params"]["wire_encoding"] = encoding
                else:
                    content = altered[index]["result"]["content"][0]
                    manifest = json.loads(content["text"])
                    manifest["manifest"]["wire_encoding"] = encoding
                    content["text"] = text_for(manifest)
                bad = b"".join(rules.canonical(frame) + b"\n" for frame in altered)
                with self.subTest(encoding=encoding, index=index):
                    self.reject(bad, candidate, trusted)

    def test_recomputed_representation_digest_does_not_authenticate_changed_source(self):
        candidate, trusted, report = rules.case()
        altered = copy.deepcopy(report)
        altered["core"]["core_id"] = "0" * 64
        represented = independent_pack(altered)
        self.assertEqual(represented["sha256"],
                         hashlib.sha256(rules.canonical(altered)).hexdigest())
        self.reject(rules.compact_response(report, text_for(represented)), candidate, trusted)

    def test_correct_negotiation_reaches_the_new_decoder_in_each_receiver(self):
        candidate, trusted, report = rules.case()
        raw = rules.compact_response(report)
        with patch("solo_distill.wire_v3.lz_unpack", side_effect=RuntimeError("production_lz_route")):
            with self.assertRaisesRegex(RuntimeError, "production_lz_route"):
                rules.consume_frame(raw, rules.SOURCES, admission=admission(), candidate=candidate,
                    observations=rules.OBSERVATIONS, **trusted)
        with patch("schema_v3_wire_oracle.lz_unpack", side_effect=RuntimeError("independent_lz_route")):
            with self.assertRaisesRegex(RuntimeError, "independent_lz_route"):
                rules.independent_receive(raw, rules.SOURCES, rules.CAPS,
                    protocol_version="2024-11-05", response_id=7, return_bytes=22016,
                    wire_encoding=ENCODING, candidate=candidate,
                    observations=rules.OBSERVATIONS, **trusted)


class LzServiceTests(rules.RepresentationServiceTests):
    def setUp(self):
        super().setUp()
        install_rules(self)


if __name__ == "__main__":
    unittest.main()
