"""Explicit offline V2 complete-frame encoding and single-use bounded FD writer.

No server default, SDK, network, model, host activation or persistent cache.
"""
from __future__ import annotations

import json
import os
import selectors
import time
from dataclasses import dataclass

from .core_v2 import LIMITS, CoreBlocked, canonical, form_core, inspect_seal

PROTOCOL_VERSION = "2024-11-05"
MAX_INPUT_BYTES = 65536
MAX_OBSERVATION_BYTES = 512


class AdmissionRejected(ValueError):
    pass


def response_id(value):
    if type(value) not in (str, int):
        raise AdmissionRejected("invalid_response_identity")
    text = str(value)
    if len(text) > 128 or len(text.encode("utf-8")) > 512:
        raise AdmissionRejected("response_identity_over_limit")
    return value


def encode_report(report, request_id, *, wire_encoding="json"):
    # Every JSON layer is encoded exactly once; the trailing LF is part of the frame.
    if wire_encoding != "json":
        from .codec_v2 import ENCODING, pack_core
        if (wire_encoding != ENCODING or report.get("contract_version") != 2
                or report.get("kind") != "sealed_knowledge"
                or report["core"]["package"]["scenario"] != "A"):
            raise ValueError("unsupported_wire_encoding")
        report = {**report, "core": pack_core(report["core"])}
    response = {"jsonrpc": "2.0", "id": request_id, "result": {
        "content": [{"type": "text", "text": canonical(report).decode("utf-8")}],
        "isError": report["status"] in {"blocked", "failed"}}}
    return canonical(response) + b"\n"


def blocked_report():
    return {"contract_version": 2, "kind": "publication_blocked", "status": "blocked",
            "reason": "required_knowledge_not_delivered", "knowledge_delivered": False}


@dataclass(frozen=True)
class Admission:
    protocol_version: str
    scenario: str
    request_id: str | int
    return_bytes: int

    def __post_init__(self):
        if self.protocol_version != PROTOCOL_VERSION or self.scenario not in LIMITS:
            raise AdmissionRejected("unsupported_offline_protocol_or_scenario")
        response_id(self.request_id)
        if type(self.return_bytes) is not int or self.return_bytes < 1:
            raise AdmissionRejected("invalid_return_budget")
        if len(encode_report(blocked_report(), self.request_id)) > self.budget:
            raise AdmissionRejected("cannot_respond_within_budget")

    @property
    def budget(self):
        return min(LIMITS[self.scenario], self.return_bytes)


def decode_input(raw):
    """Bounded explicit offline request decoding; never dispatches or runs a tool."""
    if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
        raise AdmissionRejected("input_over_limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AdmissionRejected("duplicate_key")
            result[key] = value
        return result
    def reject(_):
        raise AdmissionRejected("nonfinite_json")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject)
        canonical(value)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise AdmissionRejected("invalid_input") from error
    if type(value) is not dict or set(value) != {"jsonrpc", "id", "method", "params"} or value["jsonrpc"] != "2.0" or value["method"] != "tools/call" or type(value["params"]) is not dict:
        raise AdmissionRejected("invalid_request")
    response_id(value["id"])
    return value


def core_report(core, observations):
    value = inspect_seal(core)
    return {"contract_version": 2, "kind": "sealed_knowledge", "status": value["package"]["status"],
            "core": value, "observations": observations}


def reserved_size(core):
    # 128 NUL characters maximize admitted JSON ID escaping (768 bytes).
    # A <=512-byte observation object can expand to at most 1024 outer bytes.
    return len(encode_report(core_report(core, {}), "\x00" * 128,
                             wire_encoding=core.wire_encoding)) + 1024


@dataclass(frozen=True)
class FrozenFrame:
    data: bytes
    admission: Admission
    knowledge_delivered: bool

    def __post_init__(self):
        if type(self.data) is not bytes or not self.data.endswith(b"\n") or len(self.data) > self.admission.budget:
            raise ValueError("invalid_frozen_frame")


def publish(core, admission, *, observations=None):
    value = inspect_seal(core)
    if value["package"]["scenario"] != admission.scenario:
        raise AdmissionRejected("scenario_conflict")
    observations = {} if observations is None else observations
    if type(observations) is not dict or len(canonical(observations)) > MAX_OBSERVATION_BYTES:
        raise AdmissionRejected("observations_over_limit")
    encoded = encode_report(core_report(core, observations), admission.request_id,
                            wire_encoding=core.wire_encoding)
    if len(encoded) > admission.budget:
        encoded = encode_report(core_report(core, {}), admission.request_id,
                                wire_encoding=core.wire_encoding)
    if len(encoded) > admission.budget:
        return FrozenFrame(encode_report(blocked_report(), admission.request_id), admission, False)
    return FrozenFrame(encoded, admission, True)


def prepare(package, raw_files, admission, **selection):
    """Post-generation authentication and measured minimum-package failure response."""
    if package.get("scenario") != admission.scenario:
        raise AdmissionRejected("scenario_conflict")
    try:
        core = form_core(package, raw_files, **selection)
    except CoreBlocked:
        return None, FrozenFrame(encode_report(blocked_report(), admission.request_id), admission, False)
    return core, publish(core, admission)


class DeliveryBlocked(OSError):
    def __init__(self, reason, written):
        super().__init__(reason)
        self.reason = reason
        self.written = written


@dataclass(frozen=True)
class WriteReceipt:
    written: int
    status: str = "frame_written_not_host_consumption"


class FrameWriter:
    """Owns one attempted frame on an already nonblocking, caller-authorized FD.

    Never changes FD flags, closes the caller's FD, forks, retries a whole frame,
    or treats length as proof of consumer/host delivery. Cancel/deadline polling
    is bounded by 10ms; selector registration and all writes occur foreground.
    """
    def __init__(self, fd):
        if type(fd) is not int or os.get_blocking(fd):
            raise ValueError("nonblocking_fd_required")
        self.fd = fd
        self.used = False

    def send(self, frame, *, deadline, cancelled):
        if self.used:
            raise DeliveryBlocked("frame_replay_forbidden", 0)
        self.used = True
        if type(frame) is not FrozenFrame or type(deadline) not in (float, int) or not 0 < deadline - time.monotonic() <= 30:
            raise DeliveryBlocked("invalid_frame_or_deadline", 0)
        written = 0
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.fd, selectors.EVENT_WRITE)
                while written < len(frame.data):
                    if cancelled.is_set():
                        raise DeliveryBlocked("cancelled", written)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeliveryBlocked("output_backpressure_deadline", written)
                    if not selector.select(min(remaining, 0.01)):
                        continue
                    try:
                        count = os.write(self.fd, memoryview(frame.data)[written:])
                    except (BlockingIOError, InterruptedError):
                        continue
                    if count <= 0:
                        raise DeliveryBlocked("zero_write", written)
                    written += count
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise DeliveryBlocked("cancelled_or_expired_after_write", written)
        except DeliveryBlocked:
            raise
        except (OSError, ValueError) as error:
            raise DeliveryBlocked("output_failure", written) from error
        return WriteReceipt(written)
