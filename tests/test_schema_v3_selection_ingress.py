"""冻结选择入站表示的独立正负控；仅内存，不联网或占用在线接纳。

手工期望和篡改载荷不使用生产实现；容量合成请求只验证编码预算，
不冒充服务授权、真实来源容量、在线运行或人工语义通过。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import random
import unittest
from unittest.mock import patch

import schema_v3_lz_oracle as independent_lz
import schema_v3_selection_ingress_oracle as independent


class SelectionIngressOracleTests(unittest.TestCase):
    codec = independent

    def raw_document(self, raw):
        tokens = independent_lz._compress(raw)
        return {"encoding": "frozen-selection-lz-v3-1", "payload": {
            "encoding": "lz-json-v3-1", "raw_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "data": base64.b64encode(tokens).decode("ascii")}}

    def arguments(self, selection=None):
        return {"contract_version": 3, "operation": "live",
                "frozen_selection": {} if selection is None else selection,
                "question": "interfaces"}

    def assert_rejected(self, document):
        with self.assertRaises(ValueError):
            self.codec.unpack_selection(document)

    def test_manual_empty_object_and_literal_tokens(self):
        raw = b"{}"
        expected = {"encoding": "frozen-selection-lz-v3-1", "payload": {
            "encoding": "lz-json-v3-1", "raw_bytes": 2,
            "sha256": hashlib.sha256(raw).hexdigest(), "data": "AXt9"}}
        self.assertEqual(self.codec.pack_selection({}), expected)
        self.assertEqual(self.codec.unpack_selection(expected), {})
        self.assertEqual(base64.b64decode(expected["payload"]["data"]), b"\x01{}")

    def test_full_values_unicode_and_repeated_records_preserved(self):
        selection = {"selection_id": "frozen", "items": [
            {"text": "中\n文\\\"", "number": -7, "flag": True, "none": None},
            {"text": "中\n文\\\"", "number": -7, "flag": True, "none": None}],
            "zero": 0, "false": False, "empty": []}
        original = copy.deepcopy(selection)
        encoded = self.codec.pack_selection(selection)
        restored = self.codec.unpack_selection(encoded)
        self.assertEqual(independent_lz.canonical(restored), independent_lz.canonical(original))
        self.assertEqual(selection, original)
        restored["items"][0]["text"] = "changed"
        self.assertEqual(restored["items"][1]["text"], original["items"][1]["text"])
        self.assertEqual(selection, original)

    def test_large_complete_selection_not_omitted(self):
        selection = {"selection_id": "external", "witness": {"full": "x" * 100651}}
        self.assertGreater(len(independent_lz.canonical(selection)), 65536)
        arguments = self.arguments(selection)
        original = copy.deepcopy(arguments)
        wire, count = self.codec.live_wire_arguments(arguments, selection)
        self.assertNotIn("frozen_selection", wire)
        self.assertIn("frozen_selection_lz", wire)
        self.assertLessEqual(count, 65536)
        self.assertEqual(self.codec.resolve_selection(wire), selection)
        self.assertEqual(arguments, original)
        message = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "solo_distill_knowledge_v3", "arguments": wire}}
        raw = json.dumps(message, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8") + b"\n"
        self.assertEqual(count, len(raw))

    def test_deterministic_generated_objects_and_independent_cross_decode(self):
        rng = random.Random(20261101)
        for index in range(80):
            selection = {"id": index, "records": [
                {"n": rng.randrange(-1000, 1001), "text": "重复" * rng.randrange(20),
                 "flag": bool(rng.randrange(2)), "null": None}
                for _ in range(rng.randrange(1, 12))]}
            with self.subTest(index=index):
                expected = independent.pack_selection(selection)
                encoded = self.codec.pack_selection(selection)
                self.assertEqual(encoded, expected)
                self.assertEqual(self.codec.unpack_selection(expected), selection)
                self.assertEqual(independent.unpack_selection(encoded), selection)

    def test_wrong_purpose_downstream_shape_and_unknown_keys(self):
        good = self.codec.pack_selection({"id": 1})
        candidates = [good["payload"], {**good, "extra": {}},
                      {"encoding": good["encoding"]},
                      {**good, "encoding": "lz-json-v3-1"},
                      {**good, "encoding": "frozen-selection-lz-v3-2"},
                      {**good, "encoding": None}, {**good, "payload": []}]
        for item in candidates:
            with self.subTest(item=item):
                self.assert_rejected(item)
        for change in ({"extra": 1}, {"encoding": "json"}, {"raw_bytes": True},
                       {"raw_bytes": 0}, {"raw_bytes": 1048577}, {"data": None},
                       {"sha256": "A" * 64}, {"sha256": 1}):
            with self.subTest(change=change):
                self.assert_rejected({**good, "payload": {**good["payload"], **change}})

    def test_base64_digest_length_and_truncated_tokens(self):
        good = self.codec.pack_selection({})
        payload = good["payload"]
        for change in ({"data": "!!!!"}, {"data": payload["data"] + "===="},
                       {"data": "AXt="}, {"data": "gAA="}, {"data": "gAAA"},
                       {"sha256": "0" * 64}, {"raw_bytes": 1}, {"raw_bytes": 3}):
            with self.subTest(change=change):
                self.assert_rejected({**good, "payload": {**payload, **change}})

    def test_noncanonical_json_float_duplicate_keys_and_nonobject(self):
        for raw in (b'{"a":1,"a":1}', b'{"a":1.0}', b'{"a":NaN}',
                    b'{"b":2,"a":1}', b'{ "a":1}', b'[]', b'null',
                    b'{"a":"\\ud800"}', b'{"a":1}x'):
            with self.subTest(raw=raw):
                self.assert_rejected(self.raw_document(raw))
        for value in ([], None, 1, True, "object", {"v": 0.5}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.codec.pack_selection(value)

    def test_alternative_literal_encoding_rejected(self):
        # 两个一字节字面量可还原 {}，但不是唯一规范的两字节字面量。
        document = self.codec.pack_selection({})
        document["payload"]["data"] = base64.b64encode(b"\x00{\x00}").decode("ascii")
        self.assert_rejected(document)

    def test_expansion_byte_limit_before_json_object_construction(self):
        document = self.codec.pack_selection({})
        document["payload"]["raw_bytes"] = 1048577
        with patch.object(independent_lz, "_strict_load", side_effect=AssertionError("json_too_early")):
            self.assert_rejected(document)
        with self.assertRaises(ValueError):
            self.codec.pack_selection({"v": "x" * 1048576})
        # 临时收紧预算只测试等值/超一边界，不改变契约的实际生产上限。
        with patch.object(self.codec, "MAX_SELECTION_BYTES", 8):
            self.assertEqual(self.codec.unpack_selection(self.codec.pack_selection({"v": ""})), {"v": ""})
            with self.assertRaises(ValueError):
                self.codec.pack_selection({"v": "x"})

    def test_preparse_depth_and_node_limits_before_json_load(self):
        raw = b'{"v":' + b'[' * 64 + b'0' + b']' * 64 + b'}'
        document = self.raw_document(raw)
        with patch.object(independent_lz, "_strict_load", side_effect=AssertionError("json_too_early")):
            self.assert_rejected(document)
        nested = {"v": 0}
        for _ in range(64):
            nested = {"v": nested}
        with self.assertRaises(ValueError):
            self.codec.pack_selection(nested)
        # 四键 LZ 头部自身含五个值节点；使用五节点预算，避免先撞到
        # 表示头部预算而掩盖要验证的原对象节点等值及超一边界。
        with patch.object(self.codec, "MAX_SELECTION_NODES", 5):
            self.assertEqual(self.codec.unpack_selection(self.codec.pack_selection({"v": [0, 1, 2]})), {"v": [0, 1, 2]})
            with patch.object(independent_lz, "_strict_load", side_effect=AssertionError("json_too_early")):
                self.assert_rejected(self.raw_document(b'{"v":[0,1,2,3]}'))
            with self.assertRaises(ValueError):
                self.codec.pack_selection({"v": [0, 1, 2, 3]})

    def test_representation_and_token_boundaries(self):
        good = self.codec.pack_selection({})
        length = len(independent_lz.canonical(good))
        with patch.object(self.codec, "MAX_REPRESENTATION_BYTES", length):
            self.assertEqual(self.codec.unpack_selection(good), {})
        with patch.object(self.codec, "MAX_REPRESENTATION_BYTES", length - 1):
            self.assert_rejected(good)
        with patch.object(self.codec, "MAX_TOKEN_BYTES", 3):
            self.assertEqual(self.codec.unpack_selection(self.codec.pack_selection({})), {})
        with patch.object(self.codec, "MAX_TOKEN_BYTES", 2):
            self.assert_rejected(good)
            with self.assertRaises(ValueError):
                self.codec.pack_selection({})
        oversized = copy.deepcopy(good)
        oversized["payload"]["data"] = "A" * 65537
        self.assert_rejected(oversized)

    def test_cancellation_and_deadline_callbacks_propagate(self):
        class Stop(Exception):
            pass

        def stopped():
            raise Stop("cancelled_or_absolute_deadline")

        good = self.codec.pack_selection({"v": "x" * 70000})
        for operation in (lambda: self.codec.pack_selection({}, tick=stopped),
                          lambda: self.codec.unpack_selection(good, tick=stopped),
                          lambda: self.codec.resolve_selection(self.arguments(), tick=stopped),
                          lambda: self.codec.live_wire_arguments(self.arguments(), {}, tick=stopped)):
            with self.assertRaises(Stop):
                operation()
        ticks = []

        def later_stop():
            ticks.append(1)
            if len(ticks) == 20:
                raise Stop("shorter_window_not_reset")

        with self.assertRaises(Stop):
            self.codec.unpack_selection(good, tick=later_stop)
        self.assertEqual(len(ticks), 20)
        with self.assertRaises(ValueError):
            self.codec.pack_selection({}, tick=False)

    def test_live_mutual_exclusion_missing_null_and_type_conflicts(self):
        packed = self.codec.pack_selection({})
        invalid = [self.arguments() | {"frozen_selection_lz": packed},
                   {"operation": "live"}, {"operation": "live", "frozen_selection": None},
                   {"operation": "live", "frozen_selection": []},
                   {"operation": "live", "frozen_selection_lz": None},
                   {"operation": "preflight", "frozen_selection_lz": packed},
                   {"operation": "unknown", "frozen_selection": {}},
                   {"operation": None}, []]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.codec.resolve_selection(arguments)
        self.assertEqual(self.codec.resolve_selection(self.arguments()), {})
        self.assertEqual(self.codec.resolve_selection({"operation": "live", "frozen_selection_lz": packed}), {})
        self.assertIsNone(self.codec.resolve_selection({"operation": "preflight"}))
        self.assertEqual(self.codec.resolve_selection({"operation": "preflight", "frozen_selection": {"id": 1}}), {"id": 1})

    def test_caller_conflict_and_compressed_input_repack(self):
        selection = {"selection_id": "trusted", "witness": {"all": [1, 1, 2]}}
        wire, count = self.codec.live_wire_arguments(self.arguments(selection), selection, rpc_id="rpc")
        again, second_count = self.codec.live_wire_arguments(wire, selection, rpc_id="rpc")
        self.assertEqual(again, wire)
        self.assertEqual(second_count, count)
        for arguments in (self.arguments({"selection_id": "other"}),
                          self.arguments(selection) | {"operation": "preflight"},
                          self.arguments(selection) | {"contract_version": True}):
            with self.assertRaises(ValueError):
                self.codec.live_wire_arguments(arguments, selection)
        with self.assertRaises(ValueError):
            self.codec.live_wire_arguments(self.arguments(selection), selection, rpc_id=True)

    def test_exact_complete_request_budget_includes_lf_and_outer_fields(self):
        # 合成 padding 只用于序列化边界，不是合法服务请求字段。
        arguments = self.arguments()
        _, base = self.codec.live_wire_arguments(arguments | {"padding": ""}, {})
        wire, count = self.codec.live_wire_arguments(arguments | {"padding": "x" * (65536 - base)}, {})
        self.assertEqual(count, 65536)
        self.assertEqual(len(wire["padding"]), 65536 - base)
        with self.assertRaises(ValueError):
            self.codec.live_wire_arguments(arguments | {"padding": "x" * (65537 - base)}, {})


if __name__ == "__main__":
    unittest.main()
