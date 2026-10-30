"""离线定向验收：软超限正控、独立接收及传输故障负控；不联网。"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import random
import selectors
import threading
import time
import unittest
from unittest.mock import patch

import adaptive_receiver
import test_offline as offline
from test_live_v2 import FakeGateway
from test_v2_scheduler import graph_package, node, required
from solo_distill.adaptive_wire import TransportCapabilities, reassemble
from solo_distill.brownfield import build_review, export_review
from solo_distill.consumer_v2 import consume_frame
from solo_distill.core_v2 import CoreBlocked, form_core, make_seal
from solo_distill.distillation import load_policy, navigation_pages, utf8_json
from solo_distill.evidence import EvidenceBlocked, EvidenceReader
from solo_distill.evidence_v2 import V2EvidenceReader
from solo_distill.extractor import Project, Scanner
from solo_distill.live_v2 import LiveV2Distiller, core_selection, select_inventory
from solo_distill.scheduler_v2 import Scheduler
from solo_distill.server import encode
from solo_distill.server_v2 import V2Server, serve_v2
from solo_distill.telemetry import BudgetTelemetry, POLICY, STAGES
from solo_distill.wire_v2 import Admission, AdmissionRejected, DeliveryBlocked, FrameWriter, publish
import tempfile


def capabilities(frame=22016, *, chunking=True, **changes):
    value = dict(policy=POLICY, max_frame_bytes=frame,
                 max_transfer_bytes=8 * 1024 * 1024,
                 max_decoded_bytes=6 * 1024 * 1024,
                 max_chunks=4096, chunking=chunking, authority="caller_explicit")
    value.update(changes)
    return TransportCapabilities.from_dict(value)


def large_source(count=36, width=3000):
    randomizer = random.Random(20261028)
    names = [f"interface_{i:03d}" for i in range(count)]
    lines = []
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    for index, name in enumerate(names):
        payload = "".join(randomizer.choice(alphabet) for _ in range(width))
        next_name = names[(index + 1) % count]
        lines.append(f"def {name}():\n    value = '{payload}'\n    return {next_name}()\n")
    return "\n".join(lines).encode(), set(names)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.target = tempfile.TemporaryDirectory()
        self.artifacts = tempfile.TemporaryDirectory()
        self.addCleanup(self.target.cleanup)
        self.addCleanup(self.artifacts.cleanup)
        self.root = Path(self.target.name)
        self.output = Path(self.artifacts.name)

    def source(self, raw):
        (self.root / "sample.py").write_bytes(raw)
        self.project = Project.from_config("fixture", offline.configuration(self.root))
        self.scanner = Scanner(self.project, (".",), 10)
        self.report = self.scanner.run()
        self.assertEqual(self.report["status"], "complete")
        return {"sample.py": raw}

    def adaptive_package(self, raw):
        files = self.source(raw)
        telemetry = BudgetTelemetry(run_id="offline-adaptive", project_id="fixture",
                                    snapshot_id=self.report["snapshot_id"])
        reader = V2EvidenceReader(self.scanner, self.report, time.monotonic() + 8,
                                  threading.Event(), budget_policy=POLICY, telemetry=telemetry)
        package, selection = select_inventory(reader, "interface_000", budget_policy=POLICY)
        reader.verify()
        return files, package, selection, reader, telemetry

    def seal(self, raw):
        files, package, selection, reader, telemetry = self.adaptive_package(raw)
        core = form_core(package, files, telemetry=telemetry,
                         **core_selection(package, "interface_000", budget_policy=POLICY))
        return files, package, core, telemetry

    def receive(self, raw, files, caps, names, calls):
        independent = adaptive_receiver.receive(raw, files, caps.document(), request_id=3,
            project_id="fixture", expected_definitions=names, expected_calls=calls)
        production = consume_frame(raw, files, protocol_version="2024-11-05", request_id=3,
            scenario="A", return_bytes=caps.max_frame_bytes, project_id="fixture",
            policy_id=POLICY, transport=caps)
        self.assertEqual(production.package, independent)
        return independent


class ReadingTests(FixtureTests):
    def test_legacy_reader_adaptive_reads_all_36_bodies_and_exact_bytes(self):
        raw, names = large_source()
        self.source(raw)
        telemetry = BudgetTelemetry()
        reader = EvidenceReader(self.scanner, self.report, time.monotonic() + 8,
            threading.Event(), budget_policy=POLICY, telemetry=telemetry)
        identities = [r["source_id"] for r in self.report["entries"][0]["symbols"]]
        reader.read(reader.validate_candidates(identities))
        reader.verify()
        self.assertEqual({s["symbol"] for s in reader.sources}, names)
        self.assertEqual(reader.unfinished, [])
        self.assertEqual(len(reader.sources), 36)
        self.assertGreater(reader.body_bytes, 98304)
        lines = raw.decode().splitlines(keepends=True)
        for body in reader.sources:
            self.assertEqual(body["text"], "".join(lines[body["start_line"] - 1:body["end_line"]]))
        snapshot = telemetry.snapshot()
        self.assertEqual(snapshot["body_bytes"], reader.body_bytes)
        self.assertEqual({w["metric"] for w in snapshot["warnings"]}, {"body_blocks", "body_bytes"})
        print(json.dumps({"check": "KCP-01", "definitions": len(names), "retained": len(reader.sources),
                          "telemetry": snapshot}, ensure_ascii=False))

    def test_nested_owner_is_unique_across_batch_boundary(self):
        raw = ("class Core:\n" + "".join(f"    def method_{i}(self):\n        return {i}\n" for i in range(40))).encode()
        self.source(raw)
        reader = EvidenceReader(self.scanner, self.report, time.monotonic() + 5,
                                threading.Event(), budget_policy=POLICY)
        reader.read(list(reversed(list(reader.candidates))))
        self.assertEqual(len(reader.sources), 1)
        self.assertEqual(reader.sources[0]["text"].encode(), raw)
        self.assertEqual(len([r for r in reader.records if r["record_kind"] == "definition"]), 41)

    def test_security_source_change_secret_cancel_and_deadline_still_block(self):
        for mode in ("change", "secret", "cancel", "deadline"):
            with self.subTest(mode=mode):
                raw = b"def core():\n    return 1\n"
                if mode == "secret":
                    raw = b"def core():\n    password = 'realvalue-9f763d2c'\n    return password\n"
                self.source(raw)
                cancelled = threading.Event()
                deadline = time.monotonic() + 5
                if mode == "change":
                    (self.root / "sample.py").write_bytes(raw + b"# changed\n")
                if mode == "cancel":
                    cancelled.set()
                if mode == "deadline":
                    deadline = time.monotonic() - 1
                reader = V2EvidenceReader(self.scanner, self.report, deadline, cancelled,
                                          budget_policy=POLICY)
                with self.assertRaises((EvidenceBlocked, OSError)):
                    reader.inventory()


class SchedulingTests(FixtureTests):
    def test_complete_registry_includes_low_frequency_definition_after_48(self):
        raw, names = large_source(55, 20)
        files, package, selection, reader, telemetry = self.adaptive_package(raw)
        self.assertEqual({u["qualified_name"] for u in package["units"] if u["kind"] == "definition"}, names)
        self.assertEqual(len(reader.units), 55)
        self.assertEqual(len(selection.required_owners), 55)
        self.assertIn("interface_054", names)
        self.assertTrue(telemetry.protected)

    def test_quota_conflict_all_mandatory_retained_optional_noise_not_exempt(self):
        values = [node(f"s{i}") for i in range(17)] + [node(f"c{i}", "context") for i in range(12)]
        self.assertEqual(Scheduler(values).select(required([v.id for v in values])).reason, "policy_rejected")
        telemetry = BudgetTelemetry()
        result = Scheduler(values, budget_policy=POLICY, telemetry=telemetry).select(required([v.id for v in values]))
        self.assertEqual(set(result.required_owners), {v.id for v in values})
        self.assertEqual(result.counts, (17, 12, 0))
        self.assertTrue(any(w["metric"] == "quota_conflict" for w in telemetry.snapshot()["warnings"]))
        noise = [node(f"noise{i}", "context", path="same.py", route=("body", i), start=i * 20, end=i * 20 + 10) for i in range(5)]
        limited = Scheduler(noise, budget_policy=POLICY).select({}, optional=[v.id for v in noise])
        self.assertEqual(limited.counts, (0, 2, 0))
        self.assertEqual(len(limited.optional_omissions), 3)

    def test_large_skeleton_pages_are_complete_ordered_and_warn_once(self):
        records = [{"id": i, "name": "x" * 1000} for i in range(300)]
        telemetry = BudgetTelemetry()
        pages = navigation_pages(records, telemetry=telemetry)
        self.assertGreater(len(utf8_json(records)), 240000)
        self.assertGreater(len(pages), 1)
        self.assertEqual([r for page in pages for r in page], records)
        self.assertTrue(all(len(utf8_json(page)) <= 240000 for page in pages))
        navigation_pages(records, telemetry=telemetry)
        self.assertEqual(len([w for w in telemetry.snapshot()["warnings"] if w["metric"] == "skeleton_bytes"]), 1)
        giant = [{"id": 0, "name": "y" * 250000}]
        self.assertEqual(navigation_pages(giant, telemetry=telemetry), [giant])

    def test_protection_is_typed_and_not_model_claim_or_all_noise(self):
        raw = b"def core():\n    if enabled:\n        return helper()\ndef helper():\n    return 1\n"
        _, package, _, _, telemetry = self.adaptive_package(raw)
        protected = telemetry.protected
        self.assertTrue(all(u["id"] in protected for u in package["units"] if u["kind"] == "definition"))
        self.assertTrue(all(c["id"] in protected for c in package["conditions"]))
        self.assertTrue(all(r["id"] in protected for r in package["relations"]))
        self.assertNotIn("model-says-progress", protected)


class TelemetryTests(unittest.TestCase):
    def test_exact_escaped_frames_dedup_unknown_and_no_stdout_pollution(self):
        telemetry = BudgetTelemetry(run_id="身份\\\"", targets={"frame_bytes": 10})
        raw = json.dumps({"text": "条件\n\\\""}, ensure_ascii=False).encode() + b"\n"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for _ in range(3):
                telemetry.frame(raw)
            with telemetry.stage("encode"):
                pass
            telemetry.warn("frame_bytes", len(raw), 10, stage="publication")
            sink = io.StringIO()
            telemetry.emit(sink)
        snapshot = json.loads(sink.getvalue())
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(snapshot["frame_bytes"], [len(raw)] * 3)
        self.assertEqual(snapshot["cumulative_frame_bytes"], len(raw) * 3)
        self.assertEqual(len(snapshot["warnings"]), 1)
        self.assertEqual(snapshot["warnings"][0]["level"], "Warning")
        self.assertEqual(set(snapshot["elapsed_us"]), set(STAGES))
        self.assertIsNone(snapshot["elapsed_us"]["write"])
        self.assertEqual(snapshot["write_result"], "unknown")
        self.assertEqual(snapshot["consumer_result"], "unknown")
        self.assertEqual(snapshot["host_result"], "unknown")

    def test_telemetry_sink_failure_is_not_success_or_stdout_fallback(self):
        telemetry = BudgetTelemetry()
        class Partial:
            def write(self, text):
                return len(text) - 1
            def flush(self):
                raise AssertionError("不应刷新不完整输出")
        with self.assertRaises(OSError):
            telemetry.emit(Partial())
        self.assertEqual(telemetry.snapshot()["host_result"], "unknown")


class TransportTests(FixtureTests):
    def test_incompressible_large_core_explicit_large_frame(self):
        raw, names = large_source(4, 50)
        files, package, core, telemetry = self.seal(raw)
        randomizer = random.Random(123)
        source_index = 0
        package["facts"] = [{"id": f"required-fact-{i}", "text": "".join(randomizer.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(3900)),
            "level": "sourced_inference", "sources": [source_index], "relations": [], "conditions": []} for i in range(10)]
        core = form_core(package, files, telemetry=telemetry, **core_selection(package, "", budget_policy=POLICY))
        caps = capabilities(1024 * 1024, chunking=False)
        frame = publish(core, Admission("2024-11-05", "A", 3, caps.max_frame_bytes, caps), telemetry=telemetry)
        self.assertGreater(len(frame.data), 22016)
        checked = self.receive(frame.data, files, caps, names, names)
        self.assertEqual(len(checked["facts"]), 10)
        self.assertTrue(any(w["metric"] == "frame_bytes" for w in telemetry.snapshot()["warnings"]))
        with self.assertRaises((AdmissionRejected, ValueError)):
            publish(core, Admission("2024-11-05", "A", 3, 22016))

    def test_large_body_chunking_real_pipe_independent_receive_and_accounting(self):
        raw, names = large_source()
        files, package, core, telemetry = self.seal(raw)
        caps = capabilities()
        frame = publish(core, Admission("2024-11-05", "A", 3, 22016, caps), telemetry=telemetry)
        self.assertGreater(len(frame.data), 22016)
        self.assertGreater(len(frame.frames), 1)
        self.assertTrue(all(len(part) <= 22016 for part in frame.frames))
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        captured = bytearray()
        def drain():
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    return
                captured.extend(chunk)
        worker = threading.Thread(target=drain, daemon=False)
        worker.start()
        try:
            with telemetry.stage("write"):
                receipt = FrameWriter(write_fd).send(frame, deadline=time.monotonic() + 5, cancelled=threading.Event())
            telemetry.output(receipt.written, receipt.status)
        finally:
            os.close(write_fd)
            worker.join(6)
            os.close(read_fd)
        self.assertFalse(worker.is_alive())
        self.assertEqual(bytes(captured), frame.data)
        checked = self.receive(bytes(captured), files, caps, names, names)
        self.assertEqual(checked["relations"], core.document["package"]["relations"])
        telemetry.consumer_result = "independent_source_authenticated"
        snapshot = telemetry.snapshot()
        self.assertEqual(snapshot["frame_bytes"], list(map(len, frame.frames)))
        self.assertEqual(snapshot["cumulative_frame_bytes"], len(captured))
        self.assertEqual(snapshot["written_bytes"], len(captured))
        self.assertEqual(snapshot["host_result"], "unknown")
        print(json.dumps({"check": "KCP-04", "definitions": len(names), "retained": len(names),
            "relationships": len(checked["relations"]), "actual_pipe_bytes": len(captured),
            "cleanup": "joined", "telemetry": snapshot}, ensure_ascii=False))

    def test_missing_reordered_corrupt_and_mixed_snapshot_chunks_rejected(self):
        raw, names = large_source(8, 50)
        files, _, core, _ = self.seal(raw)
        caps = capabilities(2048)
        frame = publish(core, Admission("2024-11-05", "A", 3, 2048, caps))
        lines = list(frame.frames)
        corrupt = json.loads(lines[0])
        corrupt["params"]["chunk_sha256"] = "0" * 64
        mixed = json.loads(lines[0])
        mixed["params"]["snapshot_id"] = "other-snapshot"
        variants = [b"".join(lines[1:]), b"".join([lines[1], lines[0], *lines[2:]]),
                    b"".join([json.dumps(corrupt).encode() + b"\n", *lines[1:]]),
                    b"".join([json.dumps(mixed).encode() + b"\n", *lines[1:]])]
        for changed in variants:
            with self.subTest(length=len(changed)):
                with self.assertRaises(ValueError):
                    adaptive_receiver.receive(changed, files, caps.document(), request_id=3,
                        project_id="fixture", expected_definitions=names, expected_calls=names)
                with self.assertRaises(ValueError):
                    consume_frame(changed, files, protocol_version="2024-11-05", request_id=3,
                        scenario="A", return_bytes=2048, project_id="fixture", policy_id=POLICY, transport=caps)

    def test_mainline_a_v5_explicit_caps_lossless_and_measured_boundaries(self):
        raw, names = large_source()
        files, _, core, _ = self.seal(raw)
        caps = capabilities(max_transfer_bytes=12582912, max_decoded_bytes=8388608,
                            max_chunks=512)
        frame = publish(core, Admission("2024-11-05", "A", 3, 22016, caps))
        self.assertGreater(len(frame.data), 22016)
        decoded, report = reassemble(frame.data, caps, request_id=3, frame_budget=22016)
        self.assertIsNotNone(report)
        self.assertGreater(len(frame.frames), 2)
        self.assertEqual(report["manifest"]["count"], len(frame.frames) - 1)
        self.assertEqual(report["manifest"]["decoded_bytes"], len(decoded))
        self.assertEqual(report["manifest"]["sha256"], hashlib.sha256(decoded).hexdigest())
        self.assertLessEqual(len(frame.data), 12582912)
        self.assertLessEqual(len(decoded), 8388608)
        self.assertLessEqual(len(frame.frames) - 1, 512)
        self.assertLessEqual(max(map(len, frame.frames)), 22016)
        self.assertEqual(self.receive(frame.data, files, caps, names, names), core.document["package"])
        exact = capabilities(max_transfer_bytes=len(frame.data), max_decoded_bytes=len(decoded),
                             max_chunks=len(frame.frames) - 1)
        self.assertEqual(self.receive(frame.data, files, exact, names, names), core.document["package"])
        for metric, actual in (("max_transfer_bytes", len(frame.data)),
                               ("max_decoded_bytes", len(decoded)),
                               ("max_chunks", len(frame.frames) - 1),
                               ("max_frame_bytes", max(map(len, frame.frames)))):
            lower = {**caps.document(), metric: actual - 1}
            with self.subTest(metric=metric):
                with self.assertRaises(ValueError):
                    adaptive_receiver.receive(frame.data, files, lower, request_id=3,
                        project_id="fixture", expected_definitions=names, expected_calls=names)
                with self.assertRaises(ValueError):
                    consume_frame(frame.data, files, protocol_version="2024-11-05", request_id=3,
                        scenario="A", return_bytes=22016, project_id="fixture", policy_id=POLICY,
                        transport=TransportCapabilities.from_dict(lower))
        for changes in ({"max_transfer_bytes": 22016},
                        {"max_decoded_bytes": len(decoded) - 1}, {"max_chunks": 1}):
            with self.subTest(publisher_limit=changes):
                restricted = TransportCapabilities.from_dict({**caps.document(), **changes})
                with self.assertRaises((ValueError, AdmissionRejected)):
                    publish(core, Admission("2024-11-05", "A", 3, 22016, restricted))

    def test_transport_hard_cap_cancel_and_stale_source_not_softened(self):
        raw, names = large_source(4, 20)
        files, _, core, _ = self.seal(raw)
        caps = capabilities(2048, chunking=False)
        with self.assertRaises((ValueError, AdmissionRejected)):
            publish(core, Admission("2024-11-05", "A", 3, 2048, caps))
        caps = capabilities(100000)
        frame = publish(core, Admission("2024-11-05", "A", 3, 100000, caps))
        cancelled = threading.Event()
        cancelled.set()
        for changed_files, flag in ((files, cancelled), ({"sample.py": raw + b"# change\n"}, None)):
            with self.assertRaises(ValueError):
                consume_frame(frame.data, changed_files, protocol_version="2024-11-05", request_id=3,
                    scenario="A", return_bytes=100000, project_id="fixture", policy_id=POLICY,
                    transport=caps, cancelled=flag)
        with self.assertRaises(ValueError):
            capabilities(max_chunks=True)

    def test_rehashed_missing_relationships_rejected_by_both_consumers(self):
        raw = (b"import external\ndef core():\n    helper()\n    helper()\n"
               b"    return external.unknown()\ndef helper():\n    return 1\n")
        files, package, _, _ = self.seal(raw)
        names = {"core", "helper"}
        caps = capabilities(100000)
        self.assertGreaterEqual(len(package["relations"]), 4)
        for relations in ([], package["relations"][:-1]):
            with self.subTest(retained=len(relations)):
                incomplete = copy.deepcopy(package)
                incomplete["relations"] = relations
                with self.assertRaisesRegex(CoreBlocked, "adaptive_relationship_denominator_incomplete"):
                    form_core(incomplete, files,
                              **core_selection(incomplete, "", budget_policy=POLICY))
                # Bypass policy selection and recompute valid seals: digest/source
                # authentication alone must not accept an incomplete relationship set.
                core = make_seal(incomplete, files, POLICY)
                frame = publish(core, Admission("2024-11-05", "A", 3, 100000, caps))
                with self.assertRaises(ValueError):
                    adaptive_receiver.receive(frame.data, files, caps.document(), request_id=3,
                        project_id="fixture", expected_definitions=names,
                        expected_calls={"helper", "external.unknown"})
                with self.assertRaisesRegex(ValueError, "adaptive_relationship_denominator_incomplete"):
                    consume_frame(frame.data, files, protocol_version="2024-11-05", request_id=3,
                        scenario="A", return_bytes=100000, project_id="fixture",
                        policy_id=POLICY, transport=caps)

    def test_broken_pipe_partial_write_and_replay_are_blocked(self):
        raw, _ = large_source(4, 20)
        _, _, core, _ = self.seal(raw)
        caps = capabilities(100000)
        frame = publish(core, Admission("2024-11-05", "A", 3, 100000, caps))
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        os.close(read_fd)
        try:
            writer = FrameWriter(write_fd)
            with self.assertRaises(DeliveryBlocked):
                writer.send(frame, deadline=time.monotonic() + 1, cancelled=threading.Event())
            with self.assertRaisesRegex(DeliveryBlocked, "frame_replay_forbidden"):
                writer.send(frame, deadline=time.monotonic() + 1, cancelled=threading.Event())
        finally:
            os.close(write_fd)
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        calls = []
        def short_then_fail(fd, data):
            calls.append(len(data))
            if len(calls) == 1:
                return 7
            raise BrokenPipeError()
        try:
            with patch("solo_distill.wire_v2.os.write", side_effect=short_then_fail):
                with self.assertRaises(DeliveryBlocked) as result:
                    FrameWriter(write_fd).send(frame, deadline=time.monotonic() + 1, cancelled=threading.Event())
            self.assertEqual(result.exception.written, 7)
            self.assertEqual(calls[1], calls[0] - 7)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_slow_consumer_deadline_and_precancelled_writer_no_success(self):
        raw, _ = large_source(4, 20)
        _, _, core, _ = self.seal(raw)
        caps = capabilities(100000)
        frame = publish(core, Admission("2024-11-05", "A", 3, 100000, caps))
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        try:
            while True:
                try:
                    os.write(write_fd, b"x" * 4096)
                except BlockingIOError:
                    break
            with self.assertRaisesRegex(DeliveryBlocked, "output_backpressure_deadline"):
                FrameWriter(write_fd).send(frame, deadline=time.monotonic() + 0.03, cancelled=threading.Event())
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(DeliveryBlocked) as result:
                FrameWriter(write_fd).send(frame, deadline=time.monotonic() + 1, cancelled=cancelled)
            self.assertEqual(result.exception.written, 0)
        finally:
            os.close(read_fd)
            os.close(write_fd)


class IntegrationTests(FixtureTests):
    def test_live_v2_fake_gateway_complete_registry_and_relationships(self):
        raw, names = large_source(36, 20)
        files = self.source(raw)
        policy = load_policy(str(offline.ROOT / "config/distillation.json"))
        policy["authorized_project_ids"] = ["fixture"]
        gateway = FakeGateway()
        distiller = LiveV2Distiller({"fixture": self.project}, policy, gateway, artifact_root=self.output)
        now = int(time.time() * 1000)
        caps = capabilities()
        request = {"contract_version": 2, "request_id": "adaptive-live-offline", "project_id": "fixture",
            "scenario": "A", "question": "Describe interfaces without runtime claims.", "seeds": [],
            "dimensions": [{"id": "interfaces", "question": "Describe static interfaces.",
                             "required": True, "evidence": "sourced_inference"}],
            "started_at_unix_ms": now, "deadline_unix_ms": now + 180000,
            "host_timeout_seconds": 180, "return_bytes": 22016,
            "budget_policy": POLICY, "transport": caps.document()}
        with patch("socket.socket", side_effect=AssertionError("不允许联网")):
            frame = distiller.distill_frame(request, rpc_id=3)
        self.assertTrue(frame.knowledge_delivered)
        checked = self.receive(frame.data, files, caps, names, names)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(len(gateway.calls[0]["sources"]), 36)
        self.assertEqual(len(checked["relations"]), 36)
        self.assertEqual(frame.telemetry.snapshot()["host_result"], "unknown")

    def test_managed_stdio_chunking_complete_capture_and_postwrite_telemetry(self):
        raw, names = large_source()
        files = self.source(raw)
        policy = load_policy(str(offline.ROOT / "config/distillation.json"))
        policy["authorized_project_ids"] = ["fixture"]
        gateway = FakeGateway()
        distiller = LiveV2Distiller({"fixture": self.project}, policy, gateway,
                                   artifact_root=self.output)
        caps = capabilities()
        now = int(time.time() * 1000)
        request = {"contract_version": 2, "request_id": "adaptive-stdio-offline",
            "project_id": "fixture", "scenario": "A", "question": "Describe static interfaces.",
            "seeds": [], "dimensions": [{"id": "interfaces", "question": "Describe interfaces.",
                "required": True, "evidence": "sourced_inference"}],
            "started_at_unix_ms": now, "deadline_unix_ms": now + 180000,
            "host_timeout_seconds": 180, "return_bytes": 22016,
            "budget_policy": POLICY, "transport": caps.document()}
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        results, buffer = [], bytearray()
        server = V2Server({"fixture": self.project}, distiller)

        def run():
            try:
                results.append(serve_v2(server, input_read, output_write))
            finally:
                os.close(input_read)
                os.close(output_write)

        def send(value):
            wire = encode(value) + b"\n"
            self.assertEqual(os.write(input_write, wire), len(wire))

        def next_frame():
            deadline = time.monotonic() + 5
            with selectors.DefaultSelector() as selector:
                selector.register(output_read, selectors.EVENT_READ)
                while b"\n" not in buffer:
                    self.assertGreater(deadline, time.monotonic(), "stdio capture deadline")
                    self.assertTrue(selector.select(max(0, deadline - time.monotonic())))
                    part = os.read(output_read, 65536)
                    self.assertTrue(part, "stdio ended before complete response")
                    buffer.extend(part)
            line, _, tail = buffer.partition(b"\n")
            buffer[:] = tail
            wire = bytes(line) + b"\n"
            self.assertLessEqual(len(wire), caps.max_frame_bytes)
            return wire

        worker = threading.Thread(target=run, daemon=False)
        with patch("socket.socket", side_effect=AssertionError("不允许联网")):
            worker.start()
            try:
                send(offline.initialization())
                self.assertEqual(json.loads(next_frame())["result"]["protocolVersion"], "2024-11-05")
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                self.assertEqual([t["name"] for t in json.loads(next_frame())["result"]["tools"]],
                                 ["solo_distill_knowledge_v2"])
                send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "solo_distill_knowledge_v2", "arguments": request}})
                frames = []
                for _ in range(caps.max_chunks + 1):
                    wire = next_frame()
                    frames.append(wire)
                    message = json.loads(wire)
                    if "id" in message:
                        self.assertEqual(message["id"], 3)
                        break
                    self.assertEqual(message["method"], "notifications/solo_distill/chunk-v1")
                else:
                    self.fail("final manifest not received")
                self.assertGreater(len(frames), 1)
                captured = b"".join(frames)
                self.assertGreater(len(captured), 22016)
                checked = self.receive(captured, files, caps, names, names)
                self.assertEqual(len(checked["relations"]), len(names))
                os.close(input_write)
                input_write = None
                worker.join(5)
                self.assertFalse(worker.is_alive(), "stdio worker survived EOF")
                self.assertEqual(results, [0])
                self.assertEqual(os.read(output_read, 65536), b"")
                self.assertFalse(buffer, "undeclared extra response")
                receipts = self.output / "verification" / ".scratch"
                delivery = json.loads((receipts / "adaptive-stdio-offline.delivery.full.json").read_bytes())
                telemetry = json.loads((receipts / "adaptive-stdio-offline.telemetry.full.json").read_bytes())
                self.assertEqual(delivery["written"], len(captured))
                self.assertEqual(delivery["frame_sha256"], hashlib.sha256(captured).hexdigest())
                self.assertEqual(telemetry["frame_bytes"], list(map(len, frames)))
                self.assertEqual(telemetry["cumulative_frame_bytes"], len(captured))
                self.assertEqual(telemetry["written_bytes"], len(captured))
                self.assertTrue(all(telemetry["elapsed_us"][stage] is not None for stage in STAGES))
                self.assertEqual(telemetry["write_result"], "frame_written_not_host_consumption")
                self.assertEqual(telemetry["consumer_result"], "unknown")
                self.assertEqual(telemetry["host_result"], "unknown")
                self.assertTrue(any(w["level"] == "Warning" for w in telemetry["warnings"]))
                self.assertEqual(len(gateway.calls), 1)
                print(json.dumps({"check": "KCP-04-stdio", "definitions": len(names),
                    "retained": len(names), "relationships": len(checked["relations"]),
                    "frames": len(frames), "actual_pipe_bytes": len(captured), "exit": results[0],
                    "cleanup": "joined", "independent_reassembly": "authenticated",
                    "warnings": [w["metric"] for w in telemetry["warnings"]]}, ensure_ascii=False))
            finally:
                if input_write is not None:
                    os.close(input_write)
                distiller.cancel()
                worker.join(5)
                os.close(output_read)
                self.assertFalse(worker.is_alive(), "owned stdio fixture did not terminate")

    def test_adaptive_brownfield_pages_all_definitions_and_authenticated_relations(self):
        raw, names = large_source(55, 20)
        files = self.source(raw)
        caps = capabilities()
        request = {"contract_version": 1, "project_id": "fixture", "return_bytes": 22016,
                   "budget_policy": POLICY, "transport": caps.document()}
        bundle = build_review({"fixture": self.project}, request, self.output / "review", rpc_id=3)
        provenance = json.loads(bundle.documents["analysis/provenance.json"])
        self.assertEqual({r["record"]["qualified_name"] for r in provenance["mandatory_records"] if r["tier"] == "P1"}, names)
        self.assertGreater(len(provenance["module_index"][0]["documents"]), 1)
        checked = self.receive(bundle.documents["analysis/frame.json"], files, caps, names, names)
        self.assertEqual(provenance["relationships"], checked["relations"])
        self.assertEqual(json.loads(bundle.documents["analysis/knowledge-package.json"]), checked)
        self.assertEqual(json.loads(bundle.documents["analysis/telemetry.json"])["host_result"], "unknown")


if __name__ == "__main__":
    unittest.main()
