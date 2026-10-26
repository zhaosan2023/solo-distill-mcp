"""E5 owned V2 pipe boundaries: fake model only, bounded waits and cleanup."""
from __future__ import annotations

from contextlib import contextmanager
import os
import selectors
import threading
import time
import unittest
from unittest.mock import patch

import test_live_v2 as fixtures
import test_offline as offline
from solo_distill import server_v2
from solo_distill.gateway import GatewayError
from solo_distill.server import encode
from solo_distill.wire_v2 import FrameWriter
from v2_consumer import load


class V2LifecycleTests(unittest.TestCase):
    setUp = fixtures.LiveV2Tests.setUp
    arguments = fixtures.LiveV2Tests.arguments

    def test_consumed_frame_eof_cannot_cancel_final_write_check(self):
        original_write, original_read = os.write, os.read
        ended = threading.Event()
        checked = []

        def observed_read(fd, size):
            raw = original_read(fd, size)
            if not raw:
                ended.set()
            return raw

        class DelayedFinalWriter(FrameWriter):
            def send(writer, frame, *, deadline, cancelled):
                if not frame.knowledge_delivered:
                    return super().send(frame, deadline=deadline, cancelled=cancelled)
                total = 0

                def controlled_write(fd, data):
                    nonlocal total
                    count = original_write(fd, data)
                    if fd == writer.fd:
                        total += count
                        if total == len(frame.data):
                            if not ended.wait(1):
                                raise RuntimeError('fixture EOF was not observed')
                            # Give the intake loop time to acquire the phase lock.
                            cancelled.wait(0.05)
                            checked.append(cancelled.is_set())
                    return count

                with patch('solo_distill.wire_v2.os.write', side_effect=controlled_write):
                    return super().send(frame, deadline=deadline, cancelled=cancelled)

        with patch.object(server_v2, 'FrameWriter', DelayedFinalWriter), \
                patch.object(server_v2.os, 'read', side_effect=observed_read):
            fixtures.LiveV2Tests.test_managed_pipe_capture_and_writer_receipt_after_natural_exit(self)
        self.assertEqual(checked, [False])

    @contextmanager
    def transport(self):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        result = []
        server = server_v2.V2Server({'fixture': self.project}, self.distiller)

        def run():
            try:
                result.append(server_v2.serve_v2(server, input_read, output_write))
            finally:
                os.close(input_read)
                os.close(output_write)

        thread = threading.Thread(target=run, daemon=False)
        buffer = bytearray()

        def send(message):
            raw = encode(message) + b'\n'
            self.assertEqual(os.write(input_write, raw), len(raw))

        def receive():
            deadline = time.monotonic() + 2
            with selectors.DefaultSelector() as selector:
                selector.register(output_read, selectors.EVENT_READ)
                while b'\n' not in buffer:
                    self.assertGreater(deadline, time.monotonic())
                    self.assertTrue(selector.select(max(0, deadline - time.monotonic())))
                    raw = os.read(output_read, 65536)
                    self.assertTrue(raw)
                    buffer.extend(raw)
            line, _, tail = buffer.partition(b'\n')
            buffer[:] = tail
            return load(bytes(line))

        def end_input():
            nonlocal input_write
            if input_write is not None:
                os.close(input_write)
                input_write = None

        thread.start()
        try:
            send(offline.initialization())
            self.assertIn('result', receive())
            send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
            self.assertIn('result', receive())
            yield send, receive, end_input, thread, result
        finally:
            end_input()
            self.distiller.cancel()
            thread.join(3)
            os.close(output_read)
            self.assertFalse(thread.is_alive(), 'owned V2 worker survived fixture')

    def test_eof_during_generation_still_cancels_and_joins(self):
        entered, released = threading.Event(), threading.Event()
        original_cancel = self.gateway.cancel

        def cancel():
            original_cancel()
            released.set()

        def waiting_generate(messages, deadline, cancelled):
            entered.set()
            if not released.wait(1):
                raise GatewayError('fixture_deadline')
            if not cancelled.is_set():
                raise GatewayError('fixture_cancellation_missing')
            raise GatewayError('cancelled')

        with patch.object(self.gateway, 'cancel', side_effect=cancel), \
                patch.object(self.gateway, 'generate', side_effect=waiting_generate):
            with self.transport() as (send, receive, end_input, thread, result):
                send({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                      'params': {'name': fixtures.TOOL_NAME, 'arguments': self.arguments()}})
                self.assertTrue(entered.wait(1))
                end_input()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertTrue(self.gateway.cancelled)
                self.assertEqual(result, [2])
                trace, _ = fixtures.driver.read_trace(self.artifacts, 'offline-v2-1')
                self.assertEqual(trace['failure'], 'cancelled')
                self.assertEqual(trace['status'], 'blocked')

    def test_explicit_cancellation_during_publication_remains_effective(self):
        entered = threading.Event()
        cancellations = []

        class WaitingWriter(FrameWriter):
            def send(writer, frame, *, deadline, cancelled):
                if frame.knowledge_delivered:
                    entered.set()
                    cancellations.append(cancelled.wait(1))
                return super().send(frame, deadline=deadline, cancelled=cancelled)

        with patch.object(server_v2, 'FrameWriter', WaitingWriter):
            with self.transport() as (send, receive, end_input, thread, result):
                send({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                      'params': {'name': fixtures.TOOL_NAME, 'arguments': self.arguments()}})
                self.assertTrue(entered.wait(1))
                send({'jsonrpc': '2.0', 'method': 'notifications/cancelled',
                      'params': {'requestId': 3}})
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(cancellations, [True])
                self.assertEqual(result, [2])
                self.assertTrue(self.gateway.cancelled)
                with self.assertRaises(OSError):
                    fixtures.driver.read_trace(self.artifacts, 'offline-v2-1', '.delivery')
