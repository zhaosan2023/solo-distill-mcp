"""冷预检策略及六份证据保存负控先行；不证明完整冷预检或在线接纳。

只使用隔离临时目录和注入时钟；真实 30 秒进程终止须另行验收。
不访问网关、不执行来源、不导入生产提取器计算结构期望。
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


INSTANCE = "mainline-a-schema-v3-20261030T095713Z"
NAMES = (
    "frozen-source-and-selection.json", "knowledge-package.json",
    "sealed-core.json", "transport.jsonl", "exact-http-post.json",
    "independent-oracle.receipt.json",
)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class PreflightPolicyTests(unittest.TestCase):
    def guard(self):
        from solo_distill.preflight_v3 import PreflightGuard
        self.clock = Clock()
        self.cancelled = threading.Event()
        return PreflightGuard(clock=self.clock, cancelled=self.cancelled)

    def test_soft_targets_equal_and_overrun_are_observations(self):
        for selection, total in ((3.0, 20.0), (3.001, 20.001), (4.0, 26.72)):
            with self.subTest(selection=selection, total=total):
                guard = self.guard()
                guard.observe_selection(selection)
                self.clock.now = 100.0 + total
                result = guard.observations()
                self.assertEqual(result["selection_soft_met"], selection <= 3.0)
                self.assertEqual(result["preflight_soft_met"], total <= 20.0)
                self.assertAlmostEqual(result["selection_soft_overrun_seconds"], max(0, selection - 3))
                self.assertAlmostEqual(result["preflight_soft_overrun_seconds"], max(0, total - 20))
                self.assertEqual(result["internal_soft_target_seconds"], 27)
                self.assertEqual(result["internal_soft_met"], total <= 27)
                self.assertNotIn("hard_limit_seconds", result)
                self.assertEqual(result["evidence_gate"], "not_proven_by_clock_observations")

    def test_internal_27_equal_and_overrun_only_record(self):
        for total in (27.0, 27.000001, 57.001, 120.0):
            with self.subTest(total=total):
                guard = self.guard()
                guard.observe_selection(1)
                self.clock.now = 100.0 + total
                self.assertAlmostEqual(guard.check(), total)
                result = guard.observations()
                self.assertEqual(result["internal_soft_met"], total <= 27)
                self.assertAlmostEqual(result["internal_soft_overrun_seconds"], max(0, total - 27))
                self.assertNotIn("hard_limit_seconds", result)

    def test_good_soft_timing_does_not_override_cancellation(self):
        from solo_distill.preflight_v3 import PreflightBlocked
        guard = self.guard()
        guard.observe_selection(1)
        self.clock.now = 101.0
        self.cancelled.set()
        with self.assertRaisesRegex(PreflightBlocked, "cancelled"):
            guard.observations()

    def test_missing_selection_observation_not_manufactured(self):
        from solo_distill.preflight_v3 import PreflightBlocked
        guard = self.guard()
        with self.assertRaisesRegex(PreflightBlocked, "selection_observation_missing"):
            guard.observations()

    def test_invalid_or_repeated_selection_observation_rejected(self):
        for invalid in (True, -1, float("nan"), float("inf"), "1", None):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ValueError):
                self.guard().observe_selection(invalid)
        guard = self.guard()
        guard.observe_selection(1)
        with self.assertRaises(ValueError):
            guard.observe_selection(1)

    def test_clock_regression_and_nonfinite_values_block(self):
        from solo_distill.preflight_v3 import PreflightBlocked
        for invalid in (99.0, float("nan"), float("inf"), True):
            with self.subTest(invalid=repr(invalid)):
                guard = self.guard()
                self.clock.now = invalid
                with self.assertRaises(PreflightBlocked):
                    guard.check()
        guard = self.guard()
        self.clock.now = 102.0
        guard.check()
        self.clock.now = 101.0
        with self.assertRaises(PreflightBlocked):
            guard.check()


class PreflightPersistenceTests(unittest.TestCase):
    def setUp(self):
        from solo_distill.preflight_v3 import PreflightGuard
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent = self.root / "verification" / INSTANCE
        self.parent.mkdir(parents=True)
        self.clock = Clock()
        self.cancelled = threading.Event()
        self.guard = PreflightGuard(clock=self.clock, cancelled=self.cancelled)
        self.guard.observe_selection(1.0)
        self.artifacts = {name: ("完整原字节：" + name + "\n").encode("utf-8") for name in NAMES}

    def save(self, **changes):
        from solo_distill.preflight_v3 import save_preflight_artifacts
        options = {"artifact_root": self.root, "sample_id": "preflight-1",
                   "artifacts": self.artifacts, "guard": self.guard}
        options.update(changes)
        return save_preflight_artifacts(**options)

    def test_six_exact_files_readback_and_receipt_not_structure_proof(self):
        with patch("socket.socket", side_effect=AssertionError("禁止联网")), \
                patch("subprocess.Popen", side_effect=AssertionError("禁止启动进程")):
            result = self.save()
        target = self.parent / "preflight-1"
        self.assertEqual(set(path.name for path in target.iterdir()), set(NAMES))
        for name, raw in self.artifacts.items():
            self.assertEqual((target / name).read_bytes(), raw)
            self.assertEqual((target / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["artifact_names"], sorted(NAMES))
        self.assertEqual(result["saved_bytes"], sum(map(len, self.artifacts.values())))
        self.assertTrue(result["exact_readback"])
        self.assertEqual(result["status"], "six_artifacts_saved_and_read_back")
        self.assertEqual(result["structure_acceptance"], "not_proven_by_persistence")
        self.assertEqual(result["process_deadline_acceptance"], "requires_actual_process_exit_evidence")

    def test_soft_overrun_with_all_bytes_saved_is_not_failure(self):
        self.clock.now = 126.72
        result = self.save()
        self.assertFalse(result["timing"]["preflight_soft_met"])
        self.assertAlmostEqual(result["timing"]["elapsed_seconds"], 26.72)
        self.assertTrue(result["exact_readback"])

    def test_fsync_overrun_records_soft_timing_and_saves_all_bytes(self):
        original = os.fsync
        def slow_fsync(fd):
            original(fd)
            self.clock.now = 127.001
        with patch("solo_distill.preflight_v3.os.fsync", side_effect=slow_fsync):
            result = self.save()
        self.assertTrue(result["exact_readback"])
        self.assertFalse(result["timing"]["internal_soft_met"])
        self.assertAlmostEqual(result["timing"]["internal_soft_overrun_seconds"], 0.001)
        for name, raw in self.artifacts.items():
            self.assertEqual((self.parent / "preflight-1" / name).read_bytes(), raw)
        with self.assertRaises(FileExistsError):
            self.save(guard=self.fresh_guard())

    def fresh_guard(self):
        from solo_distill.preflight_v3 import PreflightGuard
        clock = Clock()
        guard = PreflightGuard(clock=clock, cancelled=threading.Event())
        guard.observe_selection(1)
        return guard

    def test_exact_27_with_completed_save_passes(self):
        self.clock.now = 127.0
        result = self.save()
        self.assertEqual(result["timing"]["elapsed_seconds"], 27.0)

    def test_missing_extra_empty_or_nonbyte_artifacts_block_before_directory(self):
        variants = []
        missing = dict(self.artifacts)
        missing.pop(NAMES[0])
        variants.extend((missing, {**self.artifacts, "extra.json": b"{}"},
                         {**self.artifacts, NAMES[0]: "not bytes"},
                         {**self.artifacts, NAMES[0]: b""}))
        for artifacts in variants:
            with self.subTest(keys=sorted(artifacts)), self.assertRaises(ValueError):
                self.save(artifacts=artifacts)
            self.assertFalse((self.parent / "preflight-1").exists())

    def test_invalid_sample_identity_rejected_before_writes(self):
        for sample in ("../escape", "/absolute", "preflight-1/child", "", None, True):
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                self.save(sample_id=sample)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_existing_sample_not_overwritten(self):
        target = self.parent / "preflight-1"
        target.mkdir()
        (target / "owner.txt").write_bytes(b"preserve owner bytes")
        with self.assertRaises(FileExistsError):
            self.save()
        self.assertEqual((target / "owner.txt").read_bytes(), b"preserve owner bytes")
        self.assertEqual(len(list(target.iterdir())), 1)

    def test_symlink_parent_rejected_without_outside_writes(self):
        self.parent.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        self.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.save()
        self.assertEqual(list(outside.iterdir()), [])

    def test_fsync_failure_preserves_scene_and_returns_no_success(self):
        with patch("solo_distill.preflight_v3.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.save()
        self.assertTrue((self.parent / "preflight-1").is_dir())

    def test_short_writes_complete_without_whole_file_replay(self):
        original = os.write
        with patch("solo_distill.preflight_v3.os.write", side_effect=lambda fd, raw: original(fd, raw[:7])):
            result = self.save()
        self.assertTrue(result["exact_readback"])
        for name, raw in self.artifacts.items():
            self.assertEqual((self.parent / "preflight-1" / name).read_bytes(), raw)

    def test_cancel_during_write_blocks_before_success(self):
        from solo_distill.preflight_v3 import PreflightBlocked
        original = os.write
        def cancel_write(fd, raw):
            count = original(fd, raw)
            self.cancelled.set()
            return count
        with patch("solo_distill.preflight_v3.os.write", side_effect=cancel_write):
            with self.assertRaisesRegex(PreflightBlocked, "cancelled"):
                self.save()
        self.assertTrue((self.parent / "preflight-1").is_dir())


if __name__ == "__main__":
    unittest.main()
