"""新子结构表示的传输、双消费与真实离线服务定向门禁。

保留既有表示集成的全部断言，仅替换本组显式协商与独立编码。
不联网、不执行来源、不占用真实冷预检或唯一在线接纳。
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

import test_schema_v3_representation_wire as rules
from schema_v3_representation_oracle import ENCODING as PREDECESSOR_ENCODING
from schema_v3_representation_oracle import pack as predecessor_pack
from schema_v3_subtree_oracle import ENCODING, pack as independent_pack
from schema_v3_lz_oracle import ENCODING as LZ_ENCODING
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.wire_v3 import Admission, WIRE_ENCODINGS, publish_v3


def admission(caps=None, encoding=ENCODING):
    return Admission("2024-11-05", "A", 7, 22016,
        TransportCapabilities.from_dict(rules.CAPS if caps is None else caps),
        wire_encoding=encoding)


def install_rules(test):
    for name, implementation in (("ENCODING", ENCODING),
                                 ("independent_pack", independent_pack),
                                 ("admission", admission)):
        replacement = patch.object(rules, name, implementation)
        replacement.start()
        test.addCleanup(replacement.stop)


class SubtreeWireTests(rules.RepresentationWireTests):
    def setUp(self):
        install_rules(self)

    def reject(self, raw, candidate, trusted, caps=None, encoding=ENCODING):
        # 父方法的默认参数在定义时绑定旧版本；显式传入本组版本，
        # 防止篡改负控仅因错误协商而被拒绝、掩盖新解码器缺陷。
        return super().reject(raw, candidate, trusted, caps=caps, encoding=encoding)

    def test_predecessor_and_json_publications_keep_exact_previous_bytes(self):
        candidate, trusted, report = rules.case()
        core = rules.SealedCore(rules.canonical(report["core"]))
        for encoding, represented in (("json", report),
                (PREDECESSOR_ENCODING, predecessor_pack(report))):
            expected = rules.canonical({"jsonrpc": "2.0", "id": 7, "result": {
                "content": [{"type": "text", "text": rules.canonical(represented).decode("utf-8")}],
                "isError": report["status"] in {"blocked", "failed"}}}) + b"\n"
            admitted = admission(encoding=encoding)
            frame = publish_v3(core, report["task_evidence"], rules.SOURCES, admitted,
                candidate=candidate, observations=rules.OBSERVATIONS, **trusted)
            self.assertEqual(frame.data, expected)
            result = rules.consume_frame(expected, rules.SOURCES, admission=admitted,
                candidate=candidate, observations=rules.OBSERVATIONS, **trusted)
            independent = rules.independent_receive(expected, rules.SOURCES, rules.CAPS,
                protocol_version="2024-11-05", response_id=7, return_bytes=22016,
                wire_encoding=encoding, candidate=candidate,
                observations=rules.OBSERVATIONS, **trusted)
            self.assertEqual(result.publication, report)
            self.assertEqual(result.publication, independent["publication"])

    def test_old_and_new_encoding_mixes_reject_without_fallback(self):
        candidate, trusted, report = rules.case()
        old_text = rules.canonical(predecessor_pack(report)).decode("utf-8")
        self.reject(rules.compact_response(report, old_text), candidate, trusted)
        self.reject(rules.compact_response(report), candidate, trusted,
                    encoding=PREDECESSOR_ENCODING)
        self.reject(rules.compact_chunks(report)[0], candidate, trusted,
                    encoding=PREDECESSOR_ENCODING)
        self.assertEqual(WIRE_ENCODINGS, ("json", PREDECESSOR_ENCODING, ENCODING, LZ_ENCODING))

    def test_new_reference_and_table_tampering_reaches_both_receivers(self):
        candidate, trusted, report = rules.case()
        original = independent_pack(report)
        mutations = [
            lambda d: d.update(value={"q": True}),
            lambda d: d.update(value={"q": -1}),
            lambda d: d.update(value={"q": len(d["subtrees"])}),
            lambda d: d["subtrees"].append([0] * 40),
            lambda d: d.update(encoding=PREDECESSOR_ENCODING),
        ]
        for number, mutate in enumerate(mutations):
            document = copy.deepcopy(original)
            mutate(document)
            with self.subTest(number=number):
                self.reject(rules.compact_response(report,
                    rules.canonical(document).decode("utf-8")), candidate, trusted)

    def test_each_chunk_and_final_manifest_bind_explicit_new_encoding(self):
        candidate, trusted, report = rules.case()
        raw, _ = rules.compact_chunks(report)
        frames = [json.loads(line) for line in raw.splitlines()]
        for index in (0, len(frames) - 1):
            altered = copy.deepcopy(frames)
            if index == 0:
                altered[index]["params"]["wire_encoding"] = PREDECESSOR_ENCODING
            else:
                content = altered[index]["result"]["content"][0]
                manifest = json.loads(content["text"])
                manifest["manifest"]["wire_encoding"] = PREDECESSOR_ENCODING
                content["text"] = rules.canonical(manifest).decode("utf-8")
            bad = b"".join(rules.canonical(frame) + b"\n" for frame in altered)
            with self.subTest(index=index):
                self.reject(bad, candidate, trusted)


class SubtreeServiceTests(rules.RepresentationServiceTests):
    def setUp(self):
        super().setUp()
        install_rules(self)


if __name__ == "__main__":
    unittest.main()
