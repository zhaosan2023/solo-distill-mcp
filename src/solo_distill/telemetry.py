"""Versioned, synchronous soft-budget observations; never grants permissions."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import time

POLICY = "adaptive-backpressure-v1"
TARGETS = {"body_blocks": 32, "body_bytes": 98304, "skeleton_bytes": 240000,
           "frame_bytes": 22016}
STAGES = ("scan", "read", "generate", "encode", "verify", "write")


class BudgetTelemetry:
    def __init__(self, *, run_id=None, project_id=None, snapshot_id=None,
                 contract_version="1", targets=None):
        self.identity = dict(run_id=run_id, project_id=project_id,
                             snapshot_id=snapshot_id, contract_version=contract_version)
        self.targets = dict(TARGETS)
        if targets is not None:
            if (type(targets) is not dict or set(targets) - set(TARGETS)
                    or any(type(v) is not int or v <= 0 for v in targets.values())):
                raise ValueError("invalid_soft_targets")
            self.targets.update(targets)
        self.started = time.monotonic_ns()
        self.elapsed_us = {stage: None for stage in STAGES}
        self.owners = {}
        self.skeleton_count = 0
        self.skeleton_bytes = 0
        self.core_bytes = None
        self.encoded_bytes = None
        self.frames = []
        self.written_bytes = 0
        self.write_result = "unknown"
        self.consumer_result = "unknown"
        self.host_result = "unknown"
        self.capabilities = None
        self.coverage = "unknown"
        self.protected = {}
        self.warnings = {}
        self.strategies = []

    @contextmanager
    def stage(self, name):
        if name not in self.elapsed_us:
            raise ValueError("unknown_telemetry_stage")
        started = time.monotonic_ns()
        try:
            yield
        finally:
            self.elapsed_us[name] = (self.elapsed_us[name] or 0) + (time.monotonic_ns() - started) // 1000

    def warn(self, metric, actual, target=None, *, stage=None):
        key = (metric, stage)
        # One record per metric/stage, updated rather than repeatedly logged.
        self.warnings[key] = dict(level="Warning", metric=metric, actual=actual,
                                  target=target, stage=stage)

    def observe(self, metric, actual, *, stage=None):
        target = self.targets[metric]
        if actual > target:
            self.warn(metric, actual, target, stage=stage)

    def bodies(self, owners):
        values = dict(owners)
        if any(type(k) is not str or type(v) is not int or v < 0 for k, v in values.items()):
            raise ValueError("invalid_body_accounting")
        self.owners = values
        self.observe("body_blocks", len(values), stage="read")
        self.observe("body_bytes", sum(values.values()), stage="read")

    def skeleton(self, count, size):
        if any(type(v) is not int or v < 0 for v in (count, size)):
            raise ValueError("invalid_skeleton_accounting")
        self.skeleton_count, self.skeleton_bytes = count, size
        self.observe("skeleton_bytes", size, stage="navigation")

    def protect(self, identities, reason):
        for key in identities:
            if type(key) is not str or not key:
                raise ValueError("invalid_protected_identity")
            self.protected.setdefault(key, set()).add(reason)
        if "protected_evidence_bypasses_lossy_compression" not in self.strategies:
            self.strategies.append("protected_evidence_bypasses_lossy_compression")

    def frame(self, raw):
        if type(raw) is not bytes:
            raise ValueError("frame_bytes_required")
        self.frames.append(len(raw))
        self.observe("frame_bytes", len(raw), stage="publication")

    def output(self, written, result):
        if type(written) is not int or written < 0:
            raise ValueError("invalid_written_bytes")
        self.written_bytes += written
        self.write_result = result

    def snapshot(self):
        return copy.deepcopy({"telemetry_version": 1, "policy": POLICY,
            "identity": self.identity, "elapsed_us": self.elapsed_us,
            "total_elapsed_us": (time.monotonic_ns() - self.started) // 1000,
            "measurement": "measured_except_explicit_null_or_unknown",
            "body_blocks": len(self.owners), "body_bytes": sum(self.owners.values()),
            "skeleton_count": self.skeleton_count, "skeleton_bytes": self.skeleton_bytes,
            "core_bytes": self.core_bytes, "encoded_bytes": self.encoded_bytes,
            "frame_bytes": list(self.frames), "cumulative_frame_bytes": sum(self.frames),
            "targets": self.targets, "capabilities": self.capabilities,
            "required_coverage": self.coverage,
            "protected": [{"id": key, "reasons": sorted(values)}
                          for key, values in sorted(self.protected.items())],
            "warnings": list(self.warnings.values()), "strategies": self.strategies,
            "written_bytes": self.written_bytes, "write_result": self.write_result,
            "consumer_result": self.consumer_result, "host_result": self.host_result})

    def emit(self, stream):
        # The caller supplies a diagnostic sink, never protocol stdout implicitly.
        raw = json.dumps(self.snapshot(), ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")) + "\n"
        count = stream.write(raw)
        if count != len(raw):
            raise OSError("partial_telemetry_write")
        stream.flush()
