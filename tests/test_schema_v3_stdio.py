"""新增 V3 服务／客户端边界；只用隔离来源及离线服务，不访问网关。

先保存负控资产再实现服务。真实 stdio 不证明在线发送或宿主加载。
期限长测试单独串行运行，不能塞入同一个 30 秒单测窗口。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from schema_v3_selection_oracle import expected_selection
from schema_v3_wire_oracle import receive as independent_receive
from solo_distill.distillation import load_policy
from solo_distill.extractor import InputError, Project
from test_offline import ROOT, configuration, initialization


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / "source"
        source.mkdir()
        self.files = {"sample.py": b"def interfaces():\n return 1\n", "empty.py": b"# empty\n"}
        for name, raw in self.files.items():
            (source / name).write_bytes(raw)
        self.project = Project.from_config("fixture", configuration(source))
        self.policy = load_policy(str(ROOT / "config/distillation.json"))
        self.policy["authorized_project_ids"] = ["fixture"]
        (self.root / "config").mkdir()
        (self.root / "config/projects.json").write_text(json.dumps({
            "config_version": 1, "projects": {"fixture": configuration(source)}}))
        (self.root / "config/distillation.json").write_text(json.dumps(self.policy))
        (self.root / "src").symlink_to(ROOT / "src", target_is_directory=True)
        from solo_distill.preflight_v3 import PARENT
        (self.root / PARENT).mkdir(parents=True)

    def arguments(self, **changes):
        now = int(time.time() * 1000)
        value = {"contract_version": 3, "protocol_version": "2024-11-05",
            "operation": "preflight", "request_id": "stdio-fixture",
            "project_id": "fixture", "scenario": "A", "question": "interfaces",
            "dimensions": [{"id": "interfaces", "question": "interfaces",
                "required": True, "evidence": "sourced_inference"}],
            "started_at_unix_ms": now, "deadline_unix_ms": now + 30000,
            "host_timeout_seconds": 30, "return_bytes": 2048,
            "transport": {"policy": "adaptive-backpressure-v1", "authority": "caller_explicit",
                "max_frame_bytes": 22016, "max_transfer_bytes": 1572864,
                "max_decoded_bytes": 1048576, "max_chunks": 512, "chunking": True}}
        value.update(changes)
        return value

    def distiller(self, **changes):
        from solo_distill.service_v3 import V3Distiller
        return V3Distiller({"fixture": self.project}, self.policy,
                          artifact_root=self.root, online=False, **changes)

    def trace(self, request_id="stdio-fixture"):
        return json.loads((self.root / "verification/.scratch" /
                           (request_id + ".full.json")).read_bytes())

    def trusted(self, trace, arguments):
        return {"project_id": "fixture", "snapshot_id": trace["report"]["snapshot_id"],
            "required_core_unit_ids": trace["selection"]["core_unit_ids"],
            "selection_id": trace["selection"]["selection_id"],
            "request_id": arguments["request_id"], "question": arguments["question"],
            "dimensions": arguments["dimensions"], "post": trace["post_utf8"].encode(),
            "frozen_coverage": trace["report"]["coverage"], "failures": []}


class ServiceBoundaryTests(Fixture):
    def test_offline_chain_and_independent_publication_without_generation(self):
        from solo_distill.wire_v3 import Admission
        from solo_distill.adaptive_wire import TransportCapabilities
        arguments = self.arguments()
        distiller = self.distiller()
        with patch("socket.socket", side_effect=AssertionError("offline")):
            frame = distiller.distill_frame(arguments, rpc_id=3)
        trace = self.trace()
        trusted = self.trusted(trace, arguments)
        messages, post, selection = expected_selection(self.files, **{
            key: trusted[key] for key in ("project_id", "snapshot_id", "question", "dimensions", "frozen_coverage")})
        self.assertEqual(selection, trace["selection"])
        self.assertEqual(post, trusted["post"])
        result = independent_receive(frame.data, self.files, arguments["transport"],
            protocol_version="2024-11-05", response_id=3, return_bytes=2048,
            candidate=trace["candidate"], **trusted)
        self.assertEqual(result["task_status"], "partial")
        self.assertGreater(result["notification_chunks"], 0)
        self.assertEqual(trace["gateway_requests"], [])
        self.assertEqual(frame.admission, Admission("2024-11-05", "A", 3, 2048,
            TransportCapabilities.from_dict(arguments["transport"])))
        self.assertEqual((self.root / "source/sample.py").read_bytes(), self.files["sample.py"])

    def test_preflight_absolute_window_keeps_60_and_shorter_requests(self):
        for window, age in ((60, 0), (60, 28), (30, 28)):
            with self.subTest(window=window, age=age):
                now = 1000000
                arguments = self.arguments(started_at_unix_ms=now - age * 1000,
                    deadline_unix_ms=now + (window - age) * 1000,
                    host_timeout_seconds=window)
                with patch("solo_distill.service_v3.time.time", return_value=now / 1000), \
                     patch("solo_distill.service_v3.time.monotonic", return_value=100.0):
                    _, _, _, deadline = self.distiller().validate_v3(arguments, 3)
                self.assertEqual(deadline, 100.0 + window - age)
        with self.assertRaises(InputError):
            self.distiller().validate_v3(self.arguments(host_timeout_seconds=61), 3)
        with self.assertRaises(InputError):
            self.distiller().validate_v3(self.arguments(operation="live",
                frozen_selection={}, host_timeout_seconds=241), 3)

    def test_live_600_ceiling_soft_180_and_shorter_absolute_window(self):
        from solo_distill.service_v3 import V3Distiller
        from solo_distill.server_v3 import _host_deadline
        for window, age in ((600, 0), (600, 241), (600, 599), (240, 0), (240, 181), (180, 100), (30, 20)):
            with self.subTest(window=window, age=age):
                now = 1000000
                arguments = self.arguments(operation="live", frozen_selection={},
                    started_at_unix_ms=now - age * 1000,
                    deadline_unix_ms=now + (window - age) * 1000,
                    host_timeout_seconds=window)
                distiller = V3Distiller({"fixture": self.project}, self.policy,
                    artifact_root=self.root, online=True)
                with patch("solo_distill.service_v3.time.time", return_value=now / 1000), \
                     patch("solo_distill.service_v3.time.monotonic", return_value=100.0):
                    _, _, _, deadline = distiller.validate_v3(arguments, 3)
                    self.assertEqual(deadline, 100.0 + window - age)
                    self.assertEqual(_host_deadline(arguments, 100.0, online=True), deadline)
                    distiller.business_deadline = deadline
                    distiller.check()
                    with patch("solo_distill.service_v3.time.monotonic", return_value=deadline):
                        with self.assertRaises(InputError):
                            distiller.check()
        distiller = V3Distiller({"fixture": self.project}, self.policy,
            artifact_root=self.root, online=True)
        now = int(time.time() * 1000)
        with self.assertRaises(InputError):
            distiller.validate_v3(self.arguments(operation="live", frozen_selection={},
                started_at_unix_ms=now, deadline_unix_ms=now + 601000,
                host_timeout_seconds=601), 3)

    def test_production_ingress_complete_independent_differential_and_alias(self):
        import schema_v3_selection_ingress_oracle as independent
        from solo_distill import selection_ingress_v3 as production
        selection = {"selection_id": "external", "witness": {
            "full": "重复正文\\\"\n" * 14000, "records": [{"n": -7, "v": True}, {"n": -7, "v": True}]}}
        original = copy.deepcopy(selection)
        expected = independent.pack_selection(selection)
        actual = production.pack_selection(selection)
        self.assertEqual(actual, expected)
        self.assertEqual(independent.unpack_selection(actual), original)
        self.assertEqual(production.unpack_selection(expected), original)
        self.assertGreater(len(json.dumps(selection, ensure_ascii=False).encode()), 65536)
        resolved = production.resolve_selection({"operation": "live",
            "compressed_frozen_selection": actual})
        self.assertEqual(resolved, original)
        resolved["witness"]["records"][0]["n"] = 0
        self.assertEqual(resolved["witness"]["records"][1]["n"], -7)
        self.assertEqual(selection, original)
        for index in range(80):
            value = {"id": index, "items": [None, False, -index, "中文" * index, {"n": index}]}
            packed = production.pack_selection(value)
            self.assertEqual(packed, independent.pack_selection(value))
            self.assertEqual(production.unpack_selection(packed), value)
            self.assertEqual(independent.unpack_selection(packed), value)

    def test_invalid_compressed_ingress_rejected_before_admission_and_network(self):
        from solo_distill.service_v3 import V3Distiller
        from solo_distill.selection_ingress_v3 import pack_selection
        packed = pack_selection({})
        malformed = [None, packed["payload"], {**packed, "encoding": "lz-json-v3-1"},
            {**packed, "extra": 1},
            {**packed, "payload": {**packed["payload"], "raw_bytes": True}},
            {**packed, "payload": {**packed["payload"], "raw_bytes": 1048577}},
            {**packed, "payload": {**packed["payload"], "sha256": "0" * 64}},
            {**packed, "payload": {**packed["payload"], "data": "!!!!"}},
            {**packed, "payload": {**packed["payload"], "data": "AXt9===="}}]
        variants = [{"operation": "live", "compressed_frozen_selection": item} for item in malformed]
        variants += [{"operation": "live", "frozen_selection": {}, "compressed_frozen_selection": packed},
            {"operation": "live", "frozen_selection_lz": packed, "compressed_frozen_selection": packed},
            {"operation": "preflight", "compressed_frozen_selection": packed}]
        for changes in variants:
            with self.subTest(changes=changes):
                distiller = V3Distiller({"fixture": self.project}, self.policy,
                    artifact_root=self.root, online=True)
                with patch.object(distiller, "persist_scratch") as save, \
                     patch.object(distiller.gateway, "generate") as generate, \
                     patch("solo_distill.service_v3.prepare_snapshot") as prepare:
                    with self.assertRaises(InputError):
                        distiller.distill_frame(self.arguments(**changes), rpc_id=3)
                    save.assert_not_called()
                    generate.assert_not_called()
                    prepare.assert_not_called()
                    self.assertFalse(distiller.used)

    def test_valid_compressed_selection_conflict_never_connects(self):
        from solo_distill.service_v3 import V3Distiller
        from solo_distill.selection_ingress_v3 import pack_selection
        distiller = V3Distiller({"fixture": self.project}, self.policy,
            artifact_root=self.root, online=True)
        with patch.object(distiller.gateway, "generate") as generate:
            with self.assertRaises(Exception):
                distiller.distill_frame(self.arguments(operation="live",
                    compressed_frozen_selection=pack_selection({"selection_id": "wrong"})), rpc_id=3)
            generate.assert_not_called()
        self.assertTrue(distiller.used)
        from solo_distill.preflight_v3 import INSTANCE
        self.assertTrue((self.root / "verification/.scratch" / (INSTANCE + ".live-admission.full.json")).exists())

    def test_compressed_live_fake_generation_after_soft_target_is_not_killed(self):
        from solo_distill.service_v3 import V3Distiller
        from solo_distill.preparation_v3 import prepare_snapshot
        from solo_distill.selection_ingress_v3 import pack_selection
        from test_schema_v3_runtime_gates import FakeGateway
        arguments = self.arguments()
        prepared = prepare_snapshot(project=self.project, include=(".",),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=time.monotonic() + 5, cancelled=threading.Event())
        original_clock = time.monotonic
        offset = [0.0]

        class SlowObservedGateway(FakeGateway):
            def generate(gateway, messages, deadline, cancelled):
                result = super().generate(messages, deadline, cancelled)
                offset[0] = 181.0
                return result

        gateway = SlowObservedGateway()
        distiller = V3Distiller({"fixture": self.project}, self.policy, gateway,
            artifact_root=self.root, online=True)
        now = int(time.time() * 1000)
        arguments.update(operation="live", host_timeout_seconds=240,
            started_at_unix_ms=now, deadline_unix_ms=now + 240000,
            compressed_frozen_selection=pack_selection(prepared.selection))
        with patch("time.monotonic", side_effect=lambda: original_clock() + offset[0]), \
             patch("socket.socket", side_effect=AssertionError("network_forbidden")):
            frame = distiller.distill_frame(arguments, rpc_id=3)
        trace = self.trace()
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(trace["selection"], prepared.selection)
        self.assertEqual(trace["post_utf8"].encode(), prepared.post)
        self.assertFalse(trace["live_timing"]["soft_target_met"])
        self.assertGreaterEqual(trace["live_timing"]["soft_overrun_seconds"], 1)
        self.assertEqual(trace["live_timing"]["soft_behavior"], "observation_only_no_exception")
        received = independent_receive(frame.data, self.files, arguments["transport"],
            protocol_version="2024-11-05", response_id=3, return_bytes=2048,
            candidate=trace["candidate"], **self.trusted(trace, arguments))
        self.assertEqual(received["task_status"], "partial")
        self.assertEqual((self.root / "source/sample.py").read_bytes(), self.files["sample.py"])

    def test_wrong_versions_modes_scope_and_capabilities_fail_before_preparation(self):
        variants = [{"contract_version": 2}, {"contract_version": True},
            {"operation": "live"}, {"protocol_version": "unknown"},
            {"scenario": "B"}, {"include": ["../escape"]},
            {"candidate": {}}, {"transport": None},
            {"transport": {**self.arguments()["transport"], "max_chunks": 513}}]
        for changes in variants:
            with self.subTest(changes=changes):
                with patch("solo_distill.service_v3.prepare_snapshot",
                           side_effect=AssertionError("validation first")):
                    with self.assertRaises(InputError):
                        self.distiller().distill_frame(self.arguments(**changes), rpc_id=3)

    def test_once_only_and_cancelled_no_success(self):
        distiller = self.distiller()
        distiller.distill_frame(self.arguments(), rpc_id=3)
        with self.assertRaises(InputError):
            distiller.distill_frame(self.arguments(), rpc_id=4)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Exception):
            self.distiller().distill_frame(self.arguments(request_id="cancelled"), cancelled, rpc_id=3)
        self.assertFalse((self.root / "verification/.scratch/cancelled.full.json").exists())

    def test_registration_is_explicit_extension_and_legacy_rejects_v3(self):
        from solo_distill.server_v3 import V3Server
        from solo_distill.server_v2 import V2Server
        from solo_distill.server import Server
        from solo_distill.live_v2 import LiveV2Distiller
        server = V3Server({"fixture": self.project}, self.distiller())
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "solo_distill_knowledge_v3", "arguments": self.arguments()}}
        self.assertIn("error", server.dispatch(call))
        server.dispatch(initialization())
        server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([tool["name"] for tool in tools["result"]["tools"]], ["solo_distill_knowledge_v3"])
        legacy = [Server({"fixture": self.project}), V2Server({"fixture": self.project},
            LiveV2Distiller({"fixture": self.project}, self.policy, artifact_root=self.root))]
        for old in legacy:
            old.dispatch(initialization())
            old.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertIn("error", old.dispatch(call))


class StdioIntegrationTests(Fixture):
    def test_real_process_fragmented_call_complete_consumer_and_natural_exit(self):
        from solo_distill.client import StdioClient
        arguments = self.arguments()
        client = StdioClient(self.root, online=False, v3=True, deadline=time.monotonic() + 10)
        try:
            tools = client.initialize()
            self.assertEqual(tools["result"]["tools"][0]["name"], "solo_distill_knowledge_v3")
            # Caller expectation is frozen independently before the service call.
            from solo_distill.preparation_v3 import prepare_snapshot
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
            independent = independent_receive(client.last_frame, self.files, arguments["transport"],
                protocol_version="2024-11-05", response_id=3, return_bytes=2048,
                candidate=candidate, **trusted)
            self.assertEqual(result.publication, independent["publication"])
            self.assertGreater(result.notification_chunks, 0)
        finally:
            client.close()
        self.assertEqual(client.exit_code, 0)
        self.assertEqual(client.cleanup, "confirmed_natural_exit")
        self.assertEqual(client.stderr, b"")
        delivery = json.loads((self.root / "verification/.scratch/stdio-fixture.delivery.full.json").read_bytes())
        self.assertEqual(delivery["written"], len(client.last_frame))

    def test_receive_only_preserves_raw_then_separate_trusted_consumption(self):
        from solo_distill.client import StdioClient
        from solo_distill.consumer_v3 import consume_frame
        from solo_distill.preparation_v3 import prepare_snapshot
        from solo_distill.adaptive_wire import TransportCapabilities
        from solo_distill.wire_v3 import Admission
        arguments = self.arguments()
        client = StdioClient(self.root, online=False, v3=True, deadline=time.monotonic() + 10)
        try:
            client.initialize()
            prepared = prepare_snapshot(project=self.project, include=(".",),
                question=arguments["question"], dimensions=arguments["dimensions"],
                deadline=client.deadline, cancelled=threading.Event())
            raw = client.receive_v3(arguments)
            self.assertEqual(raw, client.last_frame)
            expected_request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "solo_distill_knowledge_v3", "arguments": arguments}}
            self.assertEqual(client.last_request_bytes, json.dumps(expected_request,
                ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n")
        finally:
            client.close()
        self.assertEqual(client.exit_code, 0)
        self.assertEqual(client.cleanup, "confirmed_natural_exit")
        self.assertEqual(client.stderr, b"")
        trace = self.trace()
        trusted = self.trusted(trace, arguments)
        admission = Admission("2024-11-05", "A", 3, 2048,
            TransportCapabilities.from_dict(arguments["transport"]))
        production = consume_frame(raw, self.files, admission=admission,
            candidate=trace["candidate"], **trusted)
        independent = independent_receive(raw, self.files, arguments["transport"],
            protocol_version="2024-11-05", response_id=3, return_bytes=2048,
            candidate=trace["candidate"], **trusted)
        self.assertEqual(production.publication, independent["publication"])
        self.assertEqual(trace["selection"], prepared.selection)

    def test_client_live_wrapper_exact_budget_and_alias_without_launch(self):
        from solo_distill.client import ClientError, StdioClient
        import schema_v3_selection_ingress_oracle as independent
        selection = {"witness": {"full": "完整重复正文" * 14000}, "selection_id": "external"}
        arguments = self.arguments(operation="live", frozen_selection=selection)
        original = copy.deepcopy(arguments)
        client = StdioClient.__new__(StdioClient)
        client.v3 = True
        client.negotiated_protocol = "2024-11-05"
        client.deadline = time.monotonic() + 10
        sent = []

        class Captured(Exception):
            pass

        def capture(message):
            sent.append(message)
            raise Captured()

        client.send = capture
        with self.assertRaises(Captured):
            client.receive_v3(arguments)
        wire = sent[-1]["params"]["arguments"]
        self.assertNotIn("frozen_selection", wire)
        self.assertNotIn("frozen_selection_lz", wire)
        self.assertEqual(independent.unpack_selection(wire["compressed_frozen_selection"]), selection)
        self.assertEqual(arguments, original)
        self.assertLess(len(client.last_request_bytes), 65536)
        base = len(client.last_request_bytes)
        padding_overhead = len(b',"padding":""')
        equal = arguments | {"padding": "x" * (65536 - base - padding_overhead)}
        with self.assertRaises(Captured):
            client.receive_v3(equal)
        self.assertEqual(len(client.last_request_bytes), 65536)
        before = len(sent)
        with self.assertRaises(ClientError):
            client.receive_v3(equal | {"padding": equal["padding"] + "x"})
        self.assertEqual(len(sent), before)

    def test_offline_and_legacy_mode_selection_cannot_grant_live(self):
        from solo_distill.client import ClientError, StdioClient
        with patch("subprocess.Popen") as launch:
            with self.assertRaises(ClientError):
                StdioClient(self.root, online=True, v2=True, v3=True, deadline=time.monotonic() + 5)
            launch.assert_not_called()


class PreparationRemainingTests(Fixture):
    def test_preparation_asset_is_executed_only_as_remaining_gate(self):
        # The dedicated preparation suite is run separately, not inherited here.
        from solo_distill.preparation_v3 import prepare_snapshot
        self.assertTrue(callable(prepare_snapshot))


if __name__ == "__main__":
    unittest.main()
