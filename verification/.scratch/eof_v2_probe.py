"""E5 affected-boundary probe: repeat existing owned-pipe test, offline only.
Question: can receiver EOF race the bounded final write/delivery persistence?
Scope: existing temporary fixture and fake gateway; no sample or real POST.
Prerequisites: current V2 code and tests; acceptance: all 50 runs exit naturally.
"""
import unittest
from test_live_v2 import LiveV2Tests

suite = unittest.TestSuite(LiveV2Tests('test_managed_pipe_capture_and_writer_receipt_after_natural_exit')
                           for _ in range(50))
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
