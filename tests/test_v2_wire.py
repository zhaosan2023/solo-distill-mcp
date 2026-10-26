"""E3 offline complete-frame, static closure and real pipe negative controls."""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import unittest

from solo_distill.core_v2 import (CoreBlocked, SealedCore, authenticate_seal, canonical,
                                 canonical_package, form_core, make_seal, slice_package)
from solo_distill.package_v2 import finalize
from solo_distill.wire_v2 import (Admission, AdmissionRejected, DeliveryBlocked, FrameWriter,
                                 PROTOCOL_VERSION, decode_input, prepare, publish, reserved_size)
from test_v2_contract import fixture
from v2_validator import decode as independent_decode


def admit(scenario="A", request_id=3, budget=22016):
    return Admission(PROTOCOL_VERSION, scenario, request_id, budget)


def independent_frame(frame):
    value = json.loads(frame.data)
    assert set(value) == {"jsonrpc", "id", "result"}
    assert value["jsonrpc"] == "2.0"
    assert value["id"] == frame.admission.request_id
    assert set(value["result"]) == {"content", "isError"}
    report = json.loads(value["result"]["content"][0]["text"])
    assert value["result"]["isError"] == (report["status"] in {"blocked", "failed"})
    assert len(frame.data) <= min({"A": 22016, "B": 12288}[frame.admission.scenario], frame.admission.return_bytes)
    return report


class CoreTests(unittest.TestCase):
    def test_closure_retains_required_fact_role_relation_and_condition(self):
        package, files = fixture(b"if enabled:\n def run(): return Worker()\nelse:\n def fallback(): pass\ndef extra(): pass\n")
        package["facts"] = [{"id": "fact", "text": "Possible assembly; runtime unknown.",
            "level": "sourced_inference", "sources": [1], "relations": [], "conditions": [0]}]
        package["obligations"] = [{"id": "need", "required": True, "evidence": "sourced_inference",
            "role": None, "state": "unknown", "supports": [{"table": "facts", "index": 0}], "reason": "pending"}]
        package = finalize(package)
        selected = slice_package(package, files, [])
        independent_decode(canonical(selected), files)
        self.assertEqual(len(selected["owners"]), 1)
        self.assertEqual({u["qualified_name"] for u in selected["units"]}, {None, "run", "fallback"})
        self.assertEqual(len(selected["conditions"]), 2)
        self.assertEqual(selected["obligations"][0]["state"], "satisfied")
        self.assertEqual(selected["facts"][0]["text"], package["facts"][0]["text"])
        self.assertEqual(selected["coverage"]["scan_files"], len(files))

    def test_static_slice_cannot_omit_required_unknown_or_failures(self):
        package, files = fixture(b"def run(): pass\ndef extra(): pass\n")
        package["obligations"] = [{"id": "unknown", "required": True, "evidence": "direct_static",
            "role": "state", "state": "unknown", "supports": [], "reason": "missing_state"}]
        package["failures"] = [{"kind": "blocked", "reason": "unread_boundary"}]
        package = finalize(package)
        selected = slice_package(package, files, [("sources", package["sources"][0]["id"])])
        independent_decode(canonical(selected), files)
        self.assertEqual(selected["status"], "blocked")
        self.assertEqual(selected["obligations"], package["obligations"])
        self.assertEqual(selected["failures"], package["failures"])
        with self.assertRaises(ValueError):
            slice_package(package, files, [("sources", "invented")])

    def test_immutable_prefix_and_input_policy_invalidation(self):
        package, files = fixture("def café():\n return '否定、条件与边界不删除'\n".encode())
        core = form_core(package, files)
        prefix = core.prefix
        package["project_id"] = "mutated"
        view = core.document
        view["package"]["project_id"] = "also-mutated"
        self.assertEqual(prefix, core.prefix)
        authenticate_seal(core, files, policy_id="lifecycle-v2-1")
        with self.assertRaisesRegex(ValueError, "stale_core_policy"):
            authenticate_seal(core, files, policy_id="new-policy")
        with self.assertRaisesRegex(ValueError, "stale_core_input"):
            authenticate_seal(core, {"sample.py": files["sample.py"] + b"# change\n"})
        original = core.document["package"]
        other = form_core(original, files, policy_id="new-policy")
        self.assertNotEqual(other.core_id, core.core_id)
        for identity, observations in ((1, {"host_root": "/a", "elapsed_ms": 1}),
                                      ("dynamic", {"host_root": "/b", "elapsed_ms": 9})):
            frame = publish(core, admit(request_id=identity), observations=observations)
            self.assertEqual(independent_frame(frame)["core"], core.document)
            self.assertEqual(prefix, core.prefix)
        corrupted = core.document
        corrupted["package"]["project_id"] = "forged"
        with self.assertRaises(ValueError):
            authenticate_seal(SealedCore(canonical(corrupted)), files)

    def test_canonical_registry_remaps_and_does_not_normalize_source_text(self):
        raw = "def z():\n return 'é'\ndef A():\n return 'é'\n".encode()
        package, files = fixture(raw)
        canonical_value = canonical_package(package)
        independent_decode(canonical(canonical_value), files)
        self.assertEqual(canonical_value, canonical_package(canonical_value))
        for seed in ([u["id"] for u in package["units"]], [u["id"] for u in reversed(package["units"])]):
            from solo_distill.package_v2 import build_package
            rebuilt = build_package("fixture", "snapshot", files, seed)
            if seed == [u["id"] for u in package["units"]]:
                first = form_core(rebuilt, files)
            else:
                self.assertEqual(first.prefix, form_core(rebuilt, files).prefix)
        self.assertEqual(files["sample.py"], raw)

    def test_optional_groups_stable_atomic_and_oversize_not_partially_retained(self):
        package, files = fixture(b"def needed(): pass\ndef extra(): pass\n")
        source = package["sources"][0]["id"]
        extra = package["sources"][1]["id"]
        package["facts"] = [{"id": "large", "text": "\\\"\n" * 1000,
            "level": "sourced_inference", "sources": [1], "relations": [], "conditions": []}]
        groups = [("z", [("facts", "large")]), ("a", [("sources", extra)])]
        left = form_core(package, files, required_sources=[source], optional_groups=groups)
        right = form_core(package, files, required_sources=[source], optional_groups=list(reversed(groups)))
        self.assertEqual(left.prefix, right.prefix)
        independent_decode(canonical(left.document["package"]), files)
        self.assertLessEqual(reserved_size(left), 22016)
        with self.assertRaises(ValueError):
            form_core(package, files, required_sources=[source], optional_groups=[("bad", [("facts", "missing")])])


    def test_relations_remap_condition_identity_and_required_support_closure(self):
        from solo_distill.graph_v2 import extract_relations
        package, files = fixture(b"def target(): pass\ndef caller():\n if enabled:\n  if ready:\n   return target()\ndef unrelated(): pass\n")
        package["relations"] = extract_relations(package, files)
        relation = next(r for r in package["relations"] if r["site"]["expression"] == "target")
        package["facts"] = [{"id": "f", "text": "Conditional static call, not execution.",
            "level": "sourced_inference", "sources": relation["sources"],
            "relations": [package["relations"].index(relation)], "conditions": relation["conditions"]}]
        package["obligations"] = [{"id": "need", "required": True, "evidence": "sourced_inference",
            "role": None, "state": "unknown", "supports": [{"table": "facts", "index": 0}], "reason": "pending"}]
        package = finalize(package)
        normalized = canonical_package(package)
        independent_decode(canonical(normalized), files)
        self.assertEqual(normalized, canonical_package(normalized))
        selected = slice_package(package, files, [])
        independent_decode(canonical(selected), files)
        self.assertEqual({u["qualified_name"] for u in selected["units"]}, {"target", "caller"})
        self.assertEqual(len(selected["conditions"]), 2)
        self.assertEqual(len(selected["relations"]), 1)
        self.assertIsNotNone(selected["relations"][0]["to_unit"])
        self.assertEqual(selected["facts"][0]["relations"], [0])
        sealed = form_core(package, files, required_sources=[])
        authenticate_seal(sealed, files)
        independent_decode(canonical(sealed.document["package"]), files)

    def test_oversize_optional_group_is_wholly_excluded(self):
        package, files = fixture(b"def needed(): pass\ndef extra(): pass\n")
        package["facts"] = [{"id": "f" + str(i), "text": "x" * 4000,
            "level": "sourced_inference", "sources": [1], "relations": [], "conditions": []}
            for i in range(7)]
        sealed = form_core(package, files, required_sources=[package["sources"][0]["id"]],
            optional_groups=[("large", [("facts", f["id"]) for f in package["facts"]])])
        selected = sealed.document["package"]
        self.assertEqual(selected["facts"], [])
        self.assertEqual({u["qualified_name"] for u in selected["units"]}, {"needed"})
        independent_decode(canonical(selected), files)
        self.assertEqual(len(package["facts"]), 7)


class WireTests(unittest.TestCase):
    def test_nested_escaping_non_ascii_and_actual_extra_fields_count(self):
        package, files = fixture("def run():\n return '值'\n".encode())
        text = ('"\\\n值' * 300)
        package["facts"] = [{"id": "fact", "text": text, "level": "sourced_inference",
            "sources": [0], "relations": [], "conditions": []}]
        core = form_core(package, files, optional_groups=[("fact", [("facts", "fact")])])
        frame = publish(core, admit(request_id="身份\\\"\n"), observations={"root": "/tmp", "elapsed_ms": 9})
        report = independent_frame(frame)
        independent_decode(canonical(report["core"]["package"]), files)
        self.assertEqual(report["core"]["package"]["facts"][0]["text"], text)
        self.assertGreater(len(frame.data), len(canonical(report)))
        self.assertEqual(frame.data[-1:], b"\n")
        self.assertNotIn(b"\n", frame.data[:-1])

    def test_exact_boundary_smaller_budget_and_dynamic_fields_never_reselect(self):
        package, files = fixture(b"def run(): pass\n")
        core = form_core(package, files)
        frame = publish(core, admit())
        size = len(frame.data)
        self.assertTrue(publish(core, admit(budget=size)).knowledge_delivered)
        smaller = publish(core, admit(budget=size - 1))
        self.assertFalse(smaller.knowledge_delivered)
        self.assertEqual(independent_frame(smaller)["kind"], "publication_blocked")
        observed = publish(core, admit(budget=size), observations={"host_root": "dynamic"})
        self.assertEqual(observed.data, frame.data)
        self.assertEqual(core.prefix, form_core(package, files).prefix)
        self.assertNotIn("compaction", independent_frame(smaller))

    def test_22016_budget_negative_control_even_if_v1_transport_would_fit(self):
        package, files = fixture(b"def run(): pass\n")
        package["facts"] = [{"id": "fact", "text": "x" * 4000, "level": "sourced_inference",
            "sources": [0], "relations": [], "conditions": []} for _ in range(5)]
        for index, fact in enumerate(package["facts"]):
            fact["id"] = "fact-" + str(index)
        # Force all these mandatory facts through required obligations.
        package["obligations"] = [{"id": "need-" + str(i), "required": True, "evidence": "sourced_inference",
            "role": None, "state": "satisfied", "supports": [{"table": "facts", "index": i}], "reason": None}
            for i in range(5)]
        package = finalize(package)
        unconstrained = make_seal(package, files, "lifecycle-v2-1")
        from solo_distill.wire_v2 import core_report, encode_report
        length = len(encode_report(core_report(unconstrained, {}), 3))
        self.assertGreater(length, 22016)
        self.assertLessEqual(length, 24576)
        frame = publish(unconstrained, admit())
        self.assertFalse(frame.knowledge_delivered)
        core, blocked = prepare(package, files, admit())
        self.assertIsNone(core)
        self.assertFalse(blocked.knowledge_delivered)
        self.assertEqual(independent_frame(blocked)["knowledge_delivered"], False)
        self.assertEqual(package["facts"][0]["text"], "x" * 4000)

    def test_resource_admission_long_identity_bool_version_and_observations(self):
        package, files = fixture(b"pass\n")
        core = form_core(package, files)
        for identity in ("x" * 129, "😀" * 129, 10 ** 128, True, None):
            with self.subTest(identity=type(identity).__name__), self.assertRaises(AdmissionRejected):
                admit(request_id=identity)
        worst = admit(request_id="\x00" * 128)
        self.assertLessEqual(len(publish(core, worst).data), 22016)
        for budget in (True, 1, -1):
            with self.assertRaises(AdmissionRejected):
                admit(budget=budget)
        with self.assertRaises(AdmissionRejected):
            Admission("unknown", "A", 1, 22016)
        with self.assertRaises(AdmissionRejected):
            publish(core, admit(), observations={"large": "x" * 512})
        for raw in (b" " * 65537, b'{"jsonrpc":"2.0","id":true}', b'{"id":1,"id":2}', b'{"x":NaN}'):
            with self.assertRaises(AdmissionRejected):
                decode_input(raw)
        raw = canonical({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})
        self.assertEqual(decode_input(raw)["id"], 1)

    def test_b_budget_no_silent_scenario_change(self):
        package, files = fixture(b"def run(): pass\n")
        package["scenario"] = "B"
        core = form_core(package, files)
        frame = publish(core, admit("B", budget=24576))
        self.assertLessEqual(len(frame.data), 12288)
        with self.assertRaises(AdmissionRejected):
            publish(core, admit("A"))
        with self.assertRaises(AdmissionRejected):
            prepare(package, files, admit("A"))


class PipeTests(unittest.TestCase):
    def setUp(self):
        self.read_fd, self.write_fd = os.pipe2(os.O_NONBLOCK)
        self.addCleanup(os.close, self.read_fd)
        self.addCleanup(os.close, self.write_fd)
        package, files = fixture(b"def run(): pass\n")
        self.frame = publish(form_core(package, files), admit())
        self.cancelled = threading.Event()

    def test_real_bytes_capture_equals_frozen_frame_and_single_use(self):
        writer = FrameWriter(self.write_fd)
        receipt = writer.send(self.frame, deadline=time.monotonic() + 1, cancelled=self.cancelled)
        captured = os.read(self.read_fd, 65536)
        self.assertEqual(captured, self.frame.data)
        self.assertEqual(receipt.written, len(captured))
        self.assertEqual(receipt.status, "frame_written_not_host_consumption")
        with self.assertRaisesRegex(DeliveryBlocked, "frame_replay_forbidden"):
            writer.send(self.frame, deadline=time.monotonic() + 1, cancelled=self.cancelled)

    def test_backpressure_and_cancel_before_send_zero_side_effect(self):
        while True:
            try:
                os.write(self.write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        writer = FrameWriter(self.write_fd)
        with self.assertRaises(DeliveryBlocked) as error:
            writer.send(self.frame, deadline=time.monotonic() + .025, cancelled=self.cancelled)
        self.assertEqual(error.exception.written, 0)
        self.assertEqual(error.exception.reason, "output_backpressure_deadline")
        self.cancelled.set()
        with self.assertRaises(DeliveryBlocked) as error:
            FrameWriter(self.write_fd).send(self.frame, deadline=time.monotonic() + 1, cancelled=self.cancelled)
        self.assertEqual(error.exception.written, 0)
        self.assertEqual(error.exception.reason, "cancelled")

    def test_partial_write_timeout_never_replays_entire_frame(self):
        package, files = fixture(b"def run(): pass\n")
        package["facts"] = [{"id": "f" + str(i), "text": "x" * 3000, "level": "sourced_inference",
            "sources": [0], "relations": [], "conditions": []} for i in range(4)]
        core = form_core(package, files, optional_groups=[("facts", [("facts", f["id"]) for f in package["facts"]])])
        frame = publish(core, admit())
        self.assertGreater(len(frame.data), 8192)
        # Linux pipe capacity varies by host; explicitly bound this owned fixture.
        import fcntl
        capacity = fcntl.fcntl(self.write_fd, fcntl.F_SETPIPE_SZ, 4096)
        self.assertLess(capacity, len(frame.data))
        writer = FrameWriter(self.write_fd)
        with self.assertRaises(DeliveryBlocked) as error:
            writer.send(frame, deadline=time.monotonic() + .025, cancelled=self.cancelled)
        captured = os.read(self.read_fd, 65536)
        self.assertGreater(len(captured), 0)
        self.assertLess(len(captured), len(frame.data))
        self.assertEqual(captured, frame.data[:error.exception.written])
        with self.assertRaisesRegex(DeliveryBlocked, "frame_replay_forbidden"):
            writer.send(frame, deadline=time.monotonic() + 1, cancelled=self.cancelled)
        with self.assertRaises(BlockingIOError):
            os.read(self.read_fd, 1)

    def test_partial_write_completes_with_managed_consumer(self):
        package, files = fixture(b"def run(): pass\n")
        package["facts"] = [{"id": "f" + str(i), "text": "x" * 3000, "level": "sourced_inference",
            "sources": [0], "relations": [], "conditions": []} for i in range(4)]
        frame = publish(form_core(package, files, optional_groups=[("all", [("facts", f["id"]) for f in package["facts"]])]), admit())
        captured = bytearray()
        stop = threading.Event()
        def consume():
            until = time.monotonic() + 2
            while not stop.is_set() and len(captured) < len(frame.data) and time.monotonic() < until:
                try:
                    captured.extend(os.read(self.read_fd, 4096))
                except BlockingIOError:
                    time.sleep(.001)
        thread = threading.Thread(target=consume, daemon=False)
        thread.start()
        try:
            receipt = FrameWriter(self.write_fd).send(frame, deadline=time.monotonic() + 1, cancelled=self.cancelled)
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(bytes(captured), frame.data)
            self.assertEqual(receipt.written, len(frame.data))
        finally:
            stop.set()
            thread.join(2)

    def test_midstream_cancel_and_broken_output_fail_without_success(self):
        # Cancellation flag changes from the real write callback, without an owned task thread.
        from unittest.mock import patch
        original = os.write
        def cancelling_write(fd, raw):
            count = original(fd, raw[:100])
            self.cancelled.set()
            return count
        writer = FrameWriter(self.write_fd)
        with patch("solo_distill.wire_v2.os.write", cancelling_write), self.assertRaises(DeliveryBlocked) as error:
            writer.send(self.frame, deadline=time.monotonic() + 1, cancelled=self.cancelled)
        self.assertEqual(error.exception.written, 100)
        self.assertEqual(os.read(self.read_fd, 4096), self.frame.data[:100])
        r, w = os.pipe2(os.O_NONBLOCK)
        os.close(r)
        try:
            self.cancelled.clear()
            with self.assertRaises(DeliveryBlocked) as error:
                FrameWriter(w).send(self.frame, deadline=time.monotonic() + 1, cancelled=self.cancelled)
            self.assertEqual(error.exception.written, 0)
            self.assertEqual(error.exception.reason, "output_failure")
        finally:
            os.close(w)


if __name__ == "__main__":
    unittest.main()
