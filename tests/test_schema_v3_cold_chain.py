"""冷预检完整调用链及内部软观测回归；不以真实等待证明人造截止。

仅隔离夹具，不占用三个真实来源冷预检样本；真实外层期限仍独立。
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

from test_schema_v3_stdio import Fixture


def driver():
    path = Path(__file__).resolve().parents[1] / "verification/.scratch/mainline_a_schema_v3_cold_chain_20261030.py"
    spec = importlib.util.spec_from_file_location("schema_v3_cold_chain_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ColdChainTests(Fixture):
    def test_real_stdio_independent_consumption_six_artifacts_and_exit(self):
        module = driver()
        result = module.run_preflight(self.root, self.arguments(), "preflight-1")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["service_exit_code"], 0)
        self.assertEqual(result["service_cleanup"], "confirmed_natural_exit")
        self.assertEqual(result["gateway_requests"], 0)
        self.assertEqual(result["sample_kind"], "caller_selected_not_automatically_actual_source")
        self.assertEqual(result["save"]["timing"]["internal_soft_target_seconds"], 27)
        self.assertNotIn("hard_limit_seconds", result["save"]["timing"])
        from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT
        target = self.root / PARENT / "preflight-1"
        self.assertTrue(set(ARTIFACT_NAMES) <= {path.name for path in target.iterdir()})
        frozen = json.loads((target / ARTIFACT_NAMES[0]).read_bytes())
        self.assertEqual(set(frozen["raw_files_base64"]), set(self.files))
        self.assertEqual(json.loads((target / "independent-oracle.receipt.json").read_bytes())["status"], "pass")
        self.assertTrue((target / "transport.jsonl").read_bytes().endswith(b"\n"))
        self.assertEqual(json.loads((target / "preflight.receipt.json").read_bytes()), result)
        self.assertEqual((self.root / "source/sample.py").read_bytes(), self.files["sample.py"])

    def test_independent_failure_never_saves_success(self):
        module = driver()
        with patch.object(module, "independent_receive", side_effect=ValueError("independent_negative")):
            with self.assertRaisesRegex(ValueError, "independent_negative"):
                module.run_preflight(self.root, self.arguments(), "preflight-1")
        from solo_distill.preflight_v3 import PARENT
        self.assertFalse((self.root / PARENT / "preflight-1/preflight.receipt.json").exists())

    def test_cancel_and_existing_sample_reject_before_launch(self):
        module = driver()
        from solo_distill.preflight_v3 import PARENT, PreflightBlocked, PreflightGuard
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(module, "StdioClient", side_effect=AssertionError("must not launch")):
            with self.assertRaises(PreflightBlocked):
                module.run_preflight(self.root, self.arguments(), "preflight-1",
                                     guard=PreflightGuard(cancelled=cancelled))
        target = self.root / PARENT / "preflight-1"
        target.mkdir()
        (target / "owner.txt").write_bytes(b"preserve")
        with patch.object(module, "StdioClient", side_effect=AssertionError("must not launch")):
            with self.assertRaises(FileExistsError):
                module.run_preflight(self.root, self.arguments(), "preflight-1")
        self.assertEqual((target / "owner.txt").read_bytes(), b"preserve")

    def test_save_failure_preserves_scene_without_pass_receipt(self):
        module = driver()
        from solo_distill.preflight_v3 import PARENT
        with patch("solo_distill.preflight_v3.os.fsync", side_effect=OSError("save_negative")):
            with self.assertRaisesRegex(OSError, "save_negative"):
                module.run_preflight(self.root, self.arguments(), "preflight-1")
        self.assertTrue((self.root / PARENT / "preflight-1").is_dir())
        self.assertFalse((self.root / PARENT / "preflight-1/preflight.receipt.json").exists())


class InternalSoftTimingTests(Fixture):
    def test_injected_save_overrun_keeps_complete_receipt_without_sleep(self):
        module = driver()
        from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT, PreflightGuard
        started = time.monotonic()
        clock = [started]
        guard = PreflightGuard(clock=lambda: clock[0])
        original = os.fsync

        def overrun_fsync(fd):
            original(fd)
            clock[0] = started + 57.001

        with patch("solo_distill.preflight_v3.os.fsync", side_effect=overrun_fsync):
            result = module.run_preflight(self.root, self.arguments(), "preflight-1", guard=guard)
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["save"]["timing"]["internal_soft_met"])
        self.assertAlmostEqual(result["save"]["timing"]["internal_soft_overrun_seconds"], 30.001)
        target = self.root / PARENT / "preflight-1"
        self.assertTrue(set(ARTIFACT_NAMES) <= {path.name for path in target.iterdir()})
        self.assertEqual(json.loads((target / "preflight.receipt.json").read_bytes()), result)
        self.assertEqual(result["service_exit_code"], 0)
        self.assertEqual(result["gateway_requests"], 0)

    def test_external_60_boundary_and_shorter_caller_window_still_block(self):
        module = driver()
        from solo_distill.preflight_v3 import PreflightBlocked, PreflightGuard
        guard = PreflightGuard(clock=lambda: 100.0)
        with patch.object(module.time, "monotonic", return_value=159.999):
            module.ExitGuard(guard).check()
        with patch.object(module.time, "monotonic", return_value=160.0):
            with self.assertRaisesRegex(PreflightBlocked, "exit_deadline"):
                module.ExitGuard(guard).check()
        with patch.object(module.time, "monotonic", return_value=130.0):
            with self.assertRaisesRegex(PreflightBlocked, "exit_deadline"):
                module.ExitGuard(guard, deadline=130.0).check()
