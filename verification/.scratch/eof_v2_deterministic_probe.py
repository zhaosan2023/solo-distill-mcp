"""E5 offline EOF race reproduction; fake gateway and temporary fixture only.
Question: does EOF after complete frame consumption cancel the final writer check?
Scope: existing owned-pipe regression, one deterministic scheduling intervention.
Prerequisites: current V2 code; acceptance: fixed writer reason proves the race.
No network, sample execution, sample writes or real A authorization consumption.
"""
import os
import unittest
from unittest.mock import patch

from solo_distill import server_v2
from test_live_v2 import LiveV2Tests

OriginalWriter = server_v2.FrameWriter
original_write = os.write

class DelayedFinalWrite(OriginalWriter):
    def send(self, frame, *, deadline, cancelled):
        if not frame.knowledge_delivered:
            return super().send(frame, deadline=deadline, cancelled=cancelled)
        total = 0
        def controlled_write(fd, data):
            nonlocal total
            count = original_write(fd, data)
            if fd == self.fd:
                total += count
                if total == len(frame.data):
                    cancelled.wait(1)
            return count
        with patch('solo_distill.wire_v2.os.write', side_effect=controlled_write):
            return super().send(frame, deadline=deadline, cancelled=cancelled)

with patch.object(server_v2, 'FrameWriter', DelayedFinalWrite):
    suite = unittest.TestSuite([LiveV2Tests('test_managed_pipe_capture_and_writer_receipt_after_natural_exit')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
