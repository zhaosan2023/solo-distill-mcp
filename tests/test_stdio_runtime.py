"""Managed stdio lifecycle tests. No live gateway, installs or Roo activation."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import selectors
import threading
import time
import unittest

import test_offline as offline
import test_distillation as contracts
from solo_distill.client import StdioClient
from solo_distill.gateway import GatewayError
from solo_distill.server import Server, encode, serve


class WaitingGateway(contracts.FakeGateway):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def cancel(self):
        super().cancel()
        self.release.set()

    def generate(self, messages, deadline, cancelled):
        self.entered.set()
        while not self.release.wait(0.01):
            if cancelled.is_set():
                raise GatewayError("cancelled")
            if time.monotonic() >= deadline:
                raise GatewayError("gateway_deadline_exhausted")
        if cancelled.is_set():
            raise GatewayError("cancelled")
        return super().generate(messages, deadline, cancelled)


class ManagedTransportTests(unittest.TestCase):
    setUp = offline.ExtractorTests.setUp

    def distiller(self, gateway):
        return contracts.DistillationTests.distiller(self, gateway)

    def start_transport(self, gateway):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        self.input_stream = os.fdopen(input_read, "rb", buffering=0)
        self.writer = os.fdopen(input_write, "wb", buffering=0)
        self.reader = os.fdopen(output_read, "rb", buffering=0)
        self.output_stream = os.fdopen(output_write, "wb", buffering=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.reader, selectors.EVENT_READ)
        self.buffer = bytearray()
        self.responses = {}
        self.received_ids = set()
        self.result = []
        self.gateway = gateway
        self.server = Server({"fixture": self.project}, self.distiller(gateway))
        def run():
            try:
                self.result.append(serve(self.server, self.input_stream, self.output_stream))
            finally:
                self.input_stream.close()
                self.output_stream.close()
        self.thread = threading.Thread(target=run, daemon=False)
        self.thread.start()
        self.addCleanup(self.stop_transport)
        self.send(offline.initialization())
        self.assertIn("result", self.receive(1))
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertIn(contracts.TOOL_NAME, {
            item["name"] for item in self.receive(2)["result"]["tools"]})

    def stop_transport(self):
        if not self.writer.closed:
            self.writer.close()
        self.gateway.release.set()
        self.thread.join(timeout=3)
        try:
            self.assertFalse(self.thread.is_alive(), "managed transport did not exit")
        finally:
            self.selector.close()
            self.reader.close()

    def send(self, message):
        raw = encode(message) + b"\n"
        self.assertEqual(self.writer.write(raw), len(raw))

    def receive(self, identity):
        deadline = time.monotonic() + 3
        while identity not in self.responses:
            self.assertGreater(deadline, time.monotonic(), "stdio response timeout")
            self.assertTrue(self.selector.select(max(0, deadline - time.monotonic())))
            chunk = os.read(self.reader.fileno(), 24577)
            self.assertTrue(chunk, "stdio ended before response")
            self.buffer.extend(chunk)
            while b"\n" in self.buffer:
                line, _, remaining = self.buffer.partition(b"\n")
                self.buffer = bytearray(remaining)
                self.assertLessEqual(len(line) + 1, 24576)
                value = json.loads(line)
                self.assertEqual(value["jsonrpc"], "2.0")
                self.assertNotIn(value["id"], self.received_ids)
                self.received_ids.add(value["id"])
                self.responses[value["id"]] = value
        return self.responses.pop(identity)

    def begin_business(self):
        self.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": contracts.TOOL_NAME, "arguments": contracts.arguments()}})
        self.assertTrue(self.gateway.entered.wait(2))

    def test_running_business_answers_ping_rejects_concurrent_call_and_cancels(self):
        gateway = WaitingGateway()
        self.start_transport(gateway)
        self.begin_business()
        self.send({"jsonrpc": "2.0", "id": 4, "method": "ping"})
        self.assertEqual(self.receive(4)["result"], {})
        self.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": contracts.TOOL_NAME, "arguments": contracts.arguments(request_id="second")}})
        self.assertEqual(self.receive(5)["error"]["code"], -32003)
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {
            "requestId": 3, "reason": "fixture cancellation"}})
        response = self.receive(3)
        report = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(response["result"]["isError"])
        self.assertTrue(any(item["reason"] == "cancelled" for item in report["unfinished"]))
        self.assertTrue(gateway.cancelled)
        self.assertEqual(len(report["observations"]["gateway_requests"]), 1)
        self.writer.close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.result, [0])

    def test_wrong_typed_cancellation_identity_does_not_cancel_business(self):
        gateway = WaitingGateway()
        self.start_transport(gateway)
        self.begin_business()
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {
            "requestId": "3"}})
        self.send({"jsonrpc": "2.0", "id": 4, "method": "ping"})
        self.assertIn("result", self.receive(4))
        self.assertFalse(gateway.cancelled)
        gateway.release.set()
        response = self.receive(3)
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["status"], "complete")

    def test_eof_cancels_and_joins_the_owned_worker(self):
        gateway = WaitingGateway()
        self.start_transport(gateway)
        self.begin_business()
        self.writer.close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive(), "worker survived EOF")
        self.assertTrue(gateway.cancelled)
        self.assertEqual(self.result, [0])
        response = self.receive(3)
        self.assertNotEqual(json.loads(response["result"]["content"][0]["text"])["status"], "complete")

    def test_unicode_response_wire_budget_includes_escaping(self):
        gateway = WaitingGateway()
        gateway.change_candidate = lambda candidate: candidate.update(summary="来源导航：不等于运行证明。")
        self.start_transport(gateway)
        self.begin_business()
        gateway.release.set()
        response = self.receive(3)
        self.assertLessEqual(len(encode(response)) + 1, 12288)
        report = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(report["summary"], "来源导航：不等于运行证明。")


class RealSubprocessLifecycleTests(unittest.TestCase):
    def test_owned_subprocess_discovers_distillation_and_expired_request_never_calls_gateway(self):
        root = offline.ROOT
        client = StdioClient(root, online=True, deadline=time.monotonic() + 10)
        try:
            discovery = client.initialize()
            self.assertIn(contracts.TOOL_NAME, {
                item["name"] for item in discovery["result"]["tools"]})
            old = int(time.time() * 1000) - 200000
            value = contracts.arguments(project_id="fastcode-sample", seeds=[],
                                        started_at_unix_ms=old, deadline_unix_ms=old + 180000)
            value["dimensions"] = [{"id": "navigation", "question": "Locate interfaces",
                                    "required": True, "evidence": "sourced_inference"}]
            client.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": contracts.TOOL_NAME, "arguments": value}})
            response = client.receive(3)
            report = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["observations"]["gateway_requests"], [])
        finally:
            client.close()
        self.assertEqual(client.exit_code, 0)
        self.assertEqual(client.cleanup, "confirmed_natural_exit")
        self.assertEqual(client.stderr, b"")


if __name__ == "__main__":
    unittest.main()
