"""E5 offline race diagnosis using existing fake-gateway fixtures only.
Question: is full-suite EOF failure the post-write cancellation race?
Scope: two executions of the specified existing suite, no network or sample writes.
Prerequisites: current stdlib tests; acceptance: report fixed writer reason on failure.
"""
import unittest

for run in range(2):
    print('offline_suite_run', run + 1, flush=True)
    suite = unittest.defaultTestLoader.discover('tests', pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
