"""显式 V3 冷预检软性观测与六份原字节证据保存。

27 秒内部软目标包含保存、fsync 和复读；超标仅记录，不中断操作。
本模块不认证结构、不联网、不执行来源，不证明外层进程退出。
保存独占样本目录，失败保留现场；不覆盖、不删除、不自动重试。
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

from .extractor import open_root, open_relative, signature


INSTANCE = "mainline-a-schema-v3-20261030T095713Z"
PARENT = "verification/" + INSTANCE
ARTIFACT_NAMES = (
    "frozen-source-and-selection.json", "knowledge-package.json",
    "sealed-core.json", "transport.jsonl", "exact-http-post.json",
    "independent-oracle.receipt.json",
)
INTERNAL_SOFT_SECONDS = 27
SELECTION_SOFT_SECONDS = 3
PREFLIGHT_SOFT_SECONDS = 20
BLOCK_BYTES = 65536
PREFLIGHT_SAMPLES = tuple("preflight-" + str(index) for index in range(1, 4))
COVERAGE_PREFLIGHT_SAMPLES = tuple(
    "preflight-coverage-20261102T050022Z-" + str(index) for index in range(1, 4))


def validate_preflight_sample(sample_id, *, selection_version="question-scout-v1"):
    """按调用方显式版本核对固定样本命名空间，不从证据自报版本推定。"""
    if type(selection_version) is not str:
        raise ValueError("v3_preflight_selection_version")
    if selection_version == "question-scout-v1":
        allowed = PREFLIGHT_SAMPLES
    elif selection_version == "question-scout-v2":
        allowed = COVERAGE_PREFLIGHT_SAMPLES
    else:
        raise ValueError("v3_preflight_selection_version")
    if type(sample_id) is not str or sample_id not in allowed:
        raise ValueError("v3_preflight_sample_identity_or_version")


class PreflightBlocked(RuntimeError):
    """取消、无效时钟或保存证据不完整；耗时软目标不触发阻塞。"""


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


class PreflightGuard:
    """从构造时刻计时；调用方应在完整冷预检操作开始时创建。

    默认使用单调时钟；注入时钟只用于隔离边界测试，不是实测证据。
    27 秒仅为内部软观测目标；真实外层进程期限由调用方独立执行。
    """

    def __init__(self, *, clock=None, cancelled=None):
        self.clock = time.monotonic if clock is None else clock
        self.cancelled = threading.Event() if cancelled is None else cancelled
        if (not callable(self.clock)
                or not callable(getattr(self.cancelled, "is_set", None))):
            raise ValueError("v3_preflight_guard_context")
        self.started = self.clock()
        if not _finite_number(self.started):
            raise PreflightBlocked("v3_preflight_invalid_clock")
        self._last = self.started
        self._selection_seconds = None
        self.check()

    def check(self):
        if self.cancelled.is_set():
            raise PreflightBlocked("v3_preflight_cancelled")
        now = self.clock()
        if not _finite_number(now) or now < self._last:
            raise PreflightBlocked("v3_preflight_invalid_or_regressed_clock")
        self._last = now
        elapsed = now - self.started
        if not math.isfinite(elapsed):
            raise PreflightBlocked("v3_preflight_invalid_elapsed_clock")
        return elapsed

    def observe_selection(self, seconds):
        if (not _finite_number(seconds) or seconds < 0
                or self._selection_seconds is not None):
            raise ValueError("v3_preflight_invalid_or_repeated_selection_observation")
        self.check()
        self._selection_seconds = seconds

    def observations(self):
        elapsed = self.check()
        if self._selection_seconds is None:
            raise PreflightBlocked("v3_preflight_selection_observation_missing")
        selection = self._selection_seconds
        return {
            "selection_seconds": selection,
            "selection_soft_target_seconds": SELECTION_SOFT_SECONDS,
            "selection_soft_met": selection <= SELECTION_SOFT_SECONDS,
            "selection_soft_overrun_seconds": max(0, selection - SELECTION_SOFT_SECONDS),
            "elapsed_seconds": elapsed,
            "preflight_soft_target_seconds": PREFLIGHT_SOFT_SECONDS,
            "preflight_soft_met": elapsed <= PREFLIGHT_SOFT_SECONDS,
            "preflight_soft_overrun_seconds": max(0, elapsed - PREFLIGHT_SOFT_SECONDS),
            "internal_soft_target_seconds": INTERNAL_SOFT_SECONDS,
            "internal_soft_met": elapsed <= INTERNAL_SOFT_SECONDS,
            "internal_soft_overrun_seconds": max(0, elapsed - INTERNAL_SOFT_SECONDS),
            "evidence_gate": "not_proven_by_clock_observations",
        }


def _save_exact(directory, name, payload, guard):
    guard.check()
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PreflightBlocked("v3_preflight_output_not_regular")
        offset = 0
        view = memoryview(payload)
        while offset < len(payload):
            guard.check()
            count = os.write(descriptor, view[offset:offset + BLOCK_BYTES])
            guard.check()
            if type(count) is not int or not 0 < count <= min(BLOCK_BYTES, len(payload) - offset):
                raise PreflightBlocked("v3_preflight_invalid_or_zero_write")
            offset += count
        os.fsync(descriptor)
        guard.check()
        written_version = signature(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    guard.check()
    descriptor = open_relative(directory, name)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or signature(info) != written_version:
            raise PreflightBlocked("v3_preflight_saved_file_identity_changed")
        offset = 0
        while offset < len(payload):
            guard.check()
            raw = os.read(descriptor, min(BLOCK_BYTES, len(payload) - offset))
            guard.check()
            if not raw or raw != payload[offset:offset + len(raw)]:
                raise PreflightBlocked("v3_preflight_saved_content_mismatch")
            offset += len(raw)
        guard.check()
        extra = os.read(descriptor, 1)
        guard.check()
        if extra or signature(os.fstat(descriptor)) != written_version:
            raise PreflightBlocked("v3_preflight_saved_content_or_version_changed")
    finally:
        os.close(descriptor)
    guard.check()
    return hashlib.sha256(payload).hexdigest()


def save_preflight_artifacts(*, artifact_root, sample_id, artifacts, guard,
                            selection_version="question-scout-v1"):
    """保存调用方提供的六份证据原字节，不以文件存在认证内容语义。

    实例父目录必须已由获准接管建立；仅创建显式版本对应的固定新样本。
    所有耗时检查共享操作起点。阻塞或磁盘失败保留部分目录及文件，
    返回值只是内存保存回执，最终运行回执及退出仍由外层入口取证。
    """
    validate_preflight_sample(sample_id, selection_version=selection_version)
    if (type(artifacts) is not dict or set(artifacts) != set(ARTIFACT_NAMES)
            or any(type(raw) is not bytes or not raw for raw in artifacts.values())
            or type(guard) is not PreflightGuard):
        raise ValueError("v3_preflight_artifact_contract")
    frozen = dict(artifacts)
    guard.observations()
    workspace = Path(artifact_root).absolute()
    descriptors = []
    try:
        guard.check()
        root = open_root(str(workspace))
        descriptors.append(root)
        guard.check()
        parent = open_relative(root, PARENT, directory=True)
        descriptors.append(parent)
        guard.check()
        os.mkdir(sample_id, mode=0o700, dir_fd=parent)
        guard.check()
        leaf = open_relative(parent, sample_id, directory=True)
        descriptors.append(leaf)
        leaf_identity = os.fstat(leaf)
        os.fsync(parent)
        guard.check()
        hashes = {}
        for name in sorted(frozen):
            hashes[name] = _save_exact(leaf, name, frozen[name], guard)
            guard.check()
        os.fsync(leaf)
        guard.check()
        os.fsync(parent)
        guard.check()
        current = os.stat(sample_id, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISDIR(current.st_mode)
                or (current.st_dev, current.st_ino) != (leaf_identity.st_dev, leaf_identity.st_ino)):
            raise PreflightBlocked("v3_preflight_sample_directory_changed")
        timing = guard.observations()
        result = {
            "execution_instance": INSTANCE,
            "sample_id": sample_id,
            "artifact_path": PARENT + "/" + sample_id,
            "artifact_names": sorted(frozen),
            "artifact_sha256": hashes,
            "saved_bytes": sum(map(len, frozen.values())),
            "exact_readback": True,
            "status": "six_artifacts_saved_and_read_back",
            "timing": timing,
            "structure_acceptance": "not_proven_by_persistence",
            "process_deadline_acceptance": "requires_actual_process_exit_evidence",
            "publication": "exclusive_directory_not_atomic",
        }
        if selection_version == "question-scout-v2":
            result["selection_version"] = selection_version
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    guard.check()
    return result
