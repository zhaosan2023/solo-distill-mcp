"""剩余 V3 运行边界门禁；不重跑已通过组，不访问网关或执行来源。

真实管道核对发布期限与取消；不合作 worker 在独立进程内运行。
客户端坏流使用注入读取，测试接收边界，不冒充真实服务或宿主。
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import selectors
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_schema_v3_stdio import Fixture
from solo_distill.client import ClientError, StdioClient
from solo_distill.extractor import InputError
from solo_distill.server import encode
from solo_distill import server_v3
from solo_distill.service_v3 import V3Distiller
from solo_distill.wire_v2 import FrameWriter


class FakeGateway:
    def __init__(self):
        self.calls = 0

    def cancel(self):
        pass

    def generate(self, messages, deadline, cancelled):
        from solo_distill.gateway import encode_request
        import hashlib
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise AssertionError("fake_generation_guard")
        self.calls += 1
        post = encode_request(messages)
        return json.dumps({"summary": "", "conclusions": [], "unknowns": []}), {
            "message_bytes": len(post), "message_sha256": hashlib.sha256(post).hexdigest()}


class RuntimeBoundaryTests(Fixture):
    def test_server_host_window_keeps_60_180_and_shorter_deadlines(self):
        with patch.object(server_v3.time, "time", return_value=1000.0):
            for operation, online, end, expected in (
                    ("preflight", False, 1060000, 160.0),
                    ("preflight", False, 1030000, 130.0),
                    ("preflight", False, 1180000, 160.0),
                    ("live", True, 1180000, 280.0),
                    ("live", True, 1240000, 340.0),
                    ("live", True, 1600000, 700.0),
                    ("live", True, 1601000, 700.0),
                    ("live", True, 1001000, 101.0),
                    ("live", False, 1180000, 130.0)):
                with self.subTest(operation=operation, online=online, end=end):
                    self.assertEqual(server_v3._host_deadline(
                        {"operation": operation, "deadline_unix_ms": end}, 100.0,
                        online=online), expected)
            self.assertEqual(server_v3._host_deadline({}, 100.0, online=False), 130.0)

    @contextmanager
    def transport(self, distiller=None):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        server = server_v3.V3Server({"fixture": self.project}, distiller or self.distiller())
        results, failures = [], []
        buffer = bytearray()
        control = {"input": input_write}

        def run():
            try:
                results.append(server_v3.serve_v3(server, input_read, output_write))
            except BaseException as error:
                failures.append(error)
            finally:
                os.close(input_read)
                os.close(output_write)

        def send(message):
            raw = encode(message) + b"\n"
            self.assertEqual(os.write(control["input"], raw), len(raw))

        def receive():
            stop = time.monotonic() + 2
            with selectors.DefaultSelector() as selector:
                selector.register(output_read, selectors.EVENT_READ)
                while b"\n" not in buffer:
                    self.assertGreater(stop, time.monotonic(), "bounded_pipe_receive")
                    self.assertTrue(selector.select(max(0, stop - time.monotonic())))
                    raw = os.read(output_read, 65536)
                    self.assertTrue(raw, "pipe_eof_before_expected_frame")
                    buffer.extend(raw)
            line, _, tail = buffer.partition(b"\n")
            buffer[:] = tail
            return json.loads(line)

        def end():
            if control["input"] is not None:
                os.close(control["input"])
                control["input"] = None

        thread = threading.Thread(target=run, daemon=False)
        thread.start()
        try:
            from test_offline import initialization
            send(initialization())
            self.assertIn("result", receive())
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual(receive()["result"]["tools"][0]["name"], "solo_distill_knowledge_v3")
            yield send, receive, end, thread, results
        finally:
            end()
            server.distiller.cancel()
            thread.join(3)
            os.close(output_read)
            self.assertFalse(thread.is_alive(), "owned_v3_transport_survived")
            self.assertEqual(failures, [])

    def call_message(self, arguments):
        return {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "solo_distill_knowledge_v3", "arguments": arguments}}

    def test_fast_fake_live_keeps_180_window_and_bounded_writer(self):
        # No network: this gate tests a long authorized declaration with fast generation.
        from solo_distill.preparation_v3 import prepare_snapshot
        prepared = prepare_snapshot(project=self.project, include=(".",),
            question="interfaces", dimensions=self.arguments()["dimensions"],
            deadline=time.monotonic() + 5, cancelled=threading.Event())
        gateway = FakeGateway()
        distiller = V3Distiller({"fixture": self.project}, self.policy, gateway,
            artifact_root=self.root, online=True)
        now = int(time.time() * 1000)
        arguments = self.arguments(operation="live", frozen_selection=prepared.selection,
            started_at_unix_ms=now, deadline_unix_ms=now + 180000,
            host_timeout_seconds=180, return_bytes=22016)
        deadlines = []

        class ObservedWriter(FrameWriter):
            def send(writer, frame, *, deadline, cancelled):
                if distiller.business_deadline is not None:
                    deadlines.append(deadline - time.monotonic())
                return super().send(frame, deadline=deadline, cancelled=cancelled)

        with patch.object(server_v3, "FrameWriter", ObservedWriter), \
                patch("socket.socket", side_effect=AssertionError("network_forbidden")):
            with self.transport(distiller) as (send, receive, end, thread, results):
                send(self.call_message(arguments))
                response = receive()
                while "id" not in response:
                    response = receive()
                self.assertEqual(response["id"], 3)
                self.assertIn("result", response)
                end()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [0])
        self.assertEqual(gateway.calls, 1)
        self.assertTrue(deadlines)
        self.assertTrue(all(0 < remaining <= 30 for remaining in deadlines))
        self.assertGreater(distiller.business_deadline - time.monotonic(), 150)
        self.assertTrue((self.root / "verification/.scratch/stdio-fixture.delivery.full.json").exists())

    def test_eof_during_preparation_cancels_worker_and_saves_no_success(self):
        entered = threading.Event()
        seen = []

        def waiting_prepare(**kwargs):
            entered.set()
            seen.append(kwargs["cancelled"].wait(1))
            raise InputError("preparation_cancelled_fixture")

        with patch("solo_distill.service_v3.prepare_snapshot", side_effect=waiting_prepare):
            with self.transport() as (send, receive, end, thread, results):
                send(self.call_message(self.arguments()))
                self.assertTrue(entered.wait(1))
                end()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [2])
        self.assertEqual(seen, [True])
        self.assertFalse((self.root / "verification/.scratch/stdio-fixture.full.json").exists())

    def test_explicit_cancel_during_publication_blocks_delivery_receipt(self):
        entered = threading.Event()
        seen = []

        class WaitingWriter(FrameWriter):
            def send(writer, frame, *, deadline, cancelled):
                if frame.knowledge_delivered:
                    entered.set()
                    seen.append(cancelled.wait(1))
                return super().send(frame, deadline=deadline, cancelled=cancelled)

        with patch.object(server_v3, "FrameWriter", WaitingWriter):
            with self.transport() as (send, receive, end, thread, results):
                send(self.call_message(self.arguments()))
                self.assertTrue(entered.wait(1))
                send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                      "params": {"requestId": 3}})
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [2])
        self.assertEqual(seen, [True])
        self.assertFalse((self.root / "verification/.scratch/stdio-fixture.delivery.full.json").exists())

    def test_wrong_typed_cancel_does_not_stop_publication(self):
        entered, release = threading.Event(), threading.Event()
        events = []

        class WaitingWriter(FrameWriter):
            def send(writer, frame, *, deadline, cancelled):
                if frame.knowledge_delivered:
                    entered.set()
                    if not release.wait(1):
                        raise AssertionError("fixture_release_missing")
                    events.append(cancelled.is_set())
                return super().send(frame, deadline=deadline, cancelled=cancelled)

        with patch.object(server_v3, "FrameWriter", WaitingWriter):
            with self.transport() as (send, receive, end, thread, results):
                try:
                    send(self.call_message(self.arguments()))
                    self.assertTrue(entered.wait(1))
                    send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                          "params": {"requestId": "3"}})
                    time.sleep(0.05)
                finally:
                    release.set()
                response = receive()
                while "id" not in response:
                    response = receive()
                self.assertEqual(response["id"], 3)
                self.assertIn("result", response)
                end()
                thread.join(2)
                self.assertEqual(results, [0])
        self.assertEqual(events, [False])


class ClientStreamBoundaryTests(Fixture):
    def stream_case(self):
        arguments = self.arguments()
        frame = self.distiller().distill_frame(arguments, rpc_id=3)
        trace = self.trace()
        return arguments, frame.data, self.trusted(trace, arguments)

    def consume_chunks(self, arguments, chunks, trusted, *, stderr=False, deadline=None, tick=None):
        client = StdioClient.__new__(StdioClient)
        client.v3 = True
        client.negotiated_protocol = "2024-11-05"
        client.deadline = time.monotonic() + 5 if deadline is None else deadline
        client.stdout, client.stderr = bytearray(), bytearray()
        client.last_frame = None
        sent = []
        client.send = sent.append
        key = SimpleNamespace(fileobj=SimpleNamespace(fileno=lambda: 123),
                              data="stderr" if stderr else "stdout")
        client.selector = SimpleNamespace(select=lambda timeout: [(key, 1)], unregister=lambda stream: None)
        reads = iter(chunks)
        with patch("solo_distill.client.os.read", side_effect=lambda fd, size: next(reads, b"")):
            result = client.call_v3(arguments, raw_files=self.files,
                candidate={"summary": "", "conclusions": [], "unknowns": []},
                tick=tick, **trusted)
        self.assertEqual(sent[0]["params"]["name"], "solo_distill_knowledge_v3")
        self.assertEqual(sent[0]["id"], 3)
        self.assertEqual(result.status, "partial")
        return result

    def test_fragmented_syscall_reads_complete_exact_transfer(self):
        arguments, stream, trusted = self.stream_case()
        result = self.consume_chunks(arguments, [stream[i:i + 77] for i in range(0, len(stream), 77)], trusted)
        self.assertEqual(result.transfer_bytes, len(stream))
        self.assertGreater(result.notification_chunks, 0)

    def test_client_rejects_early_eof_unexpected_notification_and_response(self):
        arguments, stream, trusted = self.stream_case()
        variants = [([], "stdout_eof_before_v3_manifest"),
            ([b'{"jsonrpc":"2.0","method":"notifications/unexpected"}\n'], "unexpected_v3_notification"),
            ([b'{"jsonrpc":"2.0","id":"3","result":{}}\n'], "unexpected_v3_response_identity"),
            ([b'{"jsonrpc":"2.0","id":3,"error":{}}\n'], "v3_service_call_rejected")]
        for chunks, reason in variants:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ClientError, reason):
                    self.consume_chunks(arguments, chunks, trusted)

    def test_client_rejects_extra_manifest_and_partial_or_oversized_frame(self):
        arguments, stream, trusted = self.stream_case()
        variants = [([stream + stream.splitlines(keepends=True)[-1]], "extra_or_oversized_v3_frame"),
            ([b"x" * arguments["return_bytes"]], "unterminated_or_extra_v3_frame"),
            ([b"x" * arguments["return_bytes"] + b"\n"], "extra_or_oversized_v3_frame")]
        for chunks, reason in variants:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ClientError, reason):
                    self.consume_chunks(arguments, chunks, trusted)

    def test_diagnostic_overflow_deadline_and_upstream_cancel_fail_closed(self):
        arguments, stream, trusted = self.stream_case()
        with self.assertRaisesRegex(ClientError, "diagnostic_budget_exhausted"):
            self.consume_chunks(arguments, [b"x" * 8193], trusted, stderr=True)
        with self.assertRaisesRegex(ClientError, "client_deadline_exhausted"):
            self.consume_chunks(arguments, [stream], trusted, deadline=time.monotonic() - 1)

        def cancelled():
            raise InputError("upstream_cancelled_fixture")

        with self.assertRaisesRegex(InputError, "upstream_cancelled_fixture"):
            self.consume_chunks(arguments, [stream], trusted, tick=cancelled)


class UncooperativeProcessTests(unittest.TestCase):
    def test_service_owned_worker_forces_nonzero_exit_within_declared_window(self):
        from test_offline import ROOT, initialization
        now = int(time.time() * 1000)
        messages = [initialization(), {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "solo_distill_knowledge_v3", "arguments": {
                    "operation": "preflight", "deadline_unix_ms": now + 1000}}}]
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, "-B", "-S", "-m",
            "test_schema_v3_runtime_gates", "--uncooperative-worker"], cwd=ROOT,
            env={"PATH": os.defpath, "PYTHONPATH": "src:tests"},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            output, diagnostic = process.communicate(b"".join(encode(row) + b"\n" for row in messages), timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)
            self.fail("owned_worker_failed_to_exit_before_bounded_probe")
        self.assertEqual(process.returncode, 2)
        self.assertIn(b"uncooperative_worker_entered", diagnostic)
        self.assertLess(time.monotonic() - started, 3)
        responses = [json.loads(line) for line in output.splitlines()]
        self.assertFalse(any(row.get("id") == 3 and "result" in row for row in responses))
        print(json.dumps({"control": "v3_uncooperative_service_worker", "exit_code": process.returncode,
                          "elapsed_seconds": time.monotonic() - started,
                          "declared_window_seconds": 1, "success_response_absent": True,
                          "proof_scope": "short_real_service_window_not_30_second_outer_control"}), flush=True)


def uncooperative_worker():
    class BlockingDistiller:
        online = False

        def cancel(self):
            pass

        def distill_frame(self, arguments, cancelled, *, rpc_id):
            print("uncooperative_worker_entered", file=sys.stderr, flush=True)
            time.sleep(60)
            raise AssertionError("uncooperative_worker_survived")

    return server_v3.serve_v3(server_v3.V3Server({}, BlockingDistiller()),
                              sys.stdin.fileno(), sys.stdout.fileno())


if __name__ == "__main__":
    if sys.argv[1:] == ["--uncooperative-worker"]:
        raise SystemExit(uncooperative_worker())
    unittest.main()
