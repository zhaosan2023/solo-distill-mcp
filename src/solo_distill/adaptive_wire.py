"""Explicit adaptive transport capabilities and lossless notification chunking.

No network, host activation, implicit negotiation or source execution. Limits are
caller-declared hard capabilities, separate from historical planning targets.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json

from .core_v2 import canonical
from .telemetry import POLICY

CHUNK_METHOD = "notifications/solo_distill/chunk-v1"
ENCODING = "base64-jsonrpc-wire-v1"
CAPABILITY_FIELDS = {"policy", "max_frame_bytes", "max_transfer_bytes",
                     "max_decoded_bytes", "max_chunks", "chunking", "authority"}
BINDING_FIELDS = {"project_id", "snapshot_id", "input_id", "core_id",
                  "policy_id", "contract_version", "encoding", "wire_encoding"}
MANIFEST_FIELDS = BINDING_FIELDS | {"count", "decoded_bytes", "sha256"}


@dataclass(frozen=True)
class TransportCapabilities:
    policy: str
    max_frame_bytes: int
    max_transfer_bytes: int
    max_decoded_bytes: int
    max_chunks: int
    chunking: bool
    authority: str

    def __post_init__(self):
        if self.policy != POLICY or self.authority != "caller_explicit":
            raise ValueError("unsupported_transport_authority_or_policy")
        if (any(type(value) is not int or value < 1 for value in
                (self.max_frame_bytes, self.max_transfer_bytes,
                 self.max_decoded_bytes, self.max_chunks))
                or self.max_frame_bytes < 1024
                or self.max_frame_bytes > self.max_transfer_bytes
                or self.max_chunks > 4096 or type(self.chunking) is not bool):
            raise ValueError("invalid_transport_capabilities")

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != CAPABILITY_FIELDS:
            raise ValueError("invalid_transport_capability_fields")
        return cls(**value)

    def document(self):
        return {key: getattr(self, key) for key in sorted(CAPABILITY_FIELDS)}


def strict_load(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_transport_key")
            value[key] = item
        return value
    def reject(_):
        raise ValueError("nonfinite_transport_json")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=reject)
        canonical(value)
        return value
    except (UnicodeError, RecursionError, TypeError) as error:
        raise ValueError("invalid_transport_json") from error


def chunk_frames(encoded, value, admission):
    """Notifications carry fragments; exactly one final response carries a manifest."""
    from .wire_v2 import encode_report
    caps = admission.transport
    if not caps.chunking or len(encoded) > caps.max_decoded_bytes:
        raise ValueError("transport_chunking_unavailable_or_decode_limit")
    binding = {"project_id": value["package"]["project_id"],
               "snapshot_id": value["package"]["snapshot_id"],
               "input_id": value["input_id"], "core_id": value["core_id"],
               "policy_id": value["policy_id"], "contract_version": 2,
               "encoding": ENCODING, "wire_encoding": "json"}
    overhead = len(canonical({"jsonrpc": "2.0", "method": CHUNK_METHOD,
        "params": {**binding, "count": caps.max_chunks, "index": caps.max_chunks,
                   "chunk_bytes": caps.max_decoded_bytes,
                   "chunk_sha256": "0" * 64, "data": ""}})) + 1
    size = (admission.budget - overhead - 16) // 4 * 3
    if size < 1:
        raise ValueError("transport_frame_cannot_hold_chunk_metadata")
    count = (len(encoded) + size - 1) // size
    if count > caps.max_chunks:
        raise ValueError("transport_chunk_count_limit")
    frames = []
    for index, offset in enumerate(range(0, len(encoded), size)):
        part = encoded[offset:offset + size]
        params = {**binding, "count": count, "index": index,
                  "chunk_bytes": len(part), "chunk_sha256": hashlib.sha256(part).hexdigest(),
                  "data": base64.b64encode(part).decode("ascii")}
        frames.append(canonical({"jsonrpc": "2.0", "method": CHUNK_METHOD,
                                 "params": params}) + b"\n")
    manifest = {**binding, "count": count, "decoded_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest()}
    report = {"contract_version": 2, "kind": "chunk_manifest",
              "status": value["package"]["status"], "manifest": manifest}
    frames.append(encode_report(report, admission.request_id))
    if any(len(frame) > admission.budget for frame in frames):
        raise ValueError("transport_chunk_frame_limit")
    if sum(map(len, frames)) > caps.max_transfer_bytes:
        raise ValueError("transport_cumulative_limit")
    return tuple(frames)


def reassemble(raw, caps, *, request_id, frame_budget, cancelled=None):
    """Validate bounded wire/bindings before returning one complete original frame.

    Package/source authentication remains mandatory at the consumer boundary.
    Reassembly itself is not knowledge acceptance or host consumption.
    """
    def tick():
        if cancelled is not None and cancelled.is_set():
            raise ValueError("transport_consumption_cancelled")
    tick()
    if (type(caps) is not TransportCapabilities or type(raw) is not bytes
            or type(frame_budget) is not int or frame_budget < 1
            or len(raw) > caps.max_transfer_bytes or not raw.endswith(b"\n")):
        raise ValueError("transport_input_or_cumulative_limit")
    if raw.count(b"\n") > caps.max_chunks + 1:
        raise ValueError("transport_frame_count_limit")
    frames = raw.splitlines(keepends=True)
    if not frames or any(not line.endswith(b"\n") or len(line) > min(
            frame_budget, caps.max_frame_bytes) for line in frames):
        raise ValueError("transport_frame_limit_or_boundary")
    if len(frames) == 1:
        if len(raw) > caps.max_decoded_bytes:
            raise ValueError("transport_decode_limit")
        return raw, None
    if not caps.chunking:
        raise ValueError("transport_chunking_not_negotiated")
    final = strict_load(frames[-1])
    if (type(final) is not dict or set(final) != {"jsonrpc", "id", "result"}
            or final["jsonrpc"] != "2.0" or type(final["id"]) is not type(request_id)
            or final["id"] != request_id):
        raise ValueError("transport_response_identity")
    result = final["result"]
    if (type(result) is not dict or set(result) != {"content", "isError"}
            or type(result["isError"]) is not bool or type(result["content"]) is not list
            or len(result["content"]) != 1):
        raise ValueError("transport_manifest_result")
    text = result["content"][0]
    if (type(text) is not dict or set(text) != {"type", "text"}
            or text["type"] != "text" or type(text["text"]) is not str):
        raise ValueError("transport_manifest_text")
    report = strict_load(text["text"].encode("utf-8"))
    if (type(report) is not dict or set(report) != {"contract_version", "kind", "status", "manifest"}
            or type(report["contract_version"]) is not int or report["contract_version"] != 2
            or report["kind"] != "chunk_manifest" or report["status"] not in
            {"complete", "partial", "blocked", "failed"}
            or result["isError"] != (report["status"] in {"blocked", "failed"})):
        raise ValueError("transport_manifest_fields")
    manifest = report["manifest"]
    if (type(manifest) is not dict or set(manifest) != MANIFEST_FIELDS
            or manifest["encoding"] != ENCODING or manifest["wire_encoding"] != "json"
            or manifest["policy_id"] != POLICY
            or type(manifest["contract_version"]) is not int or manifest["contract_version"] != 2
            or type(manifest["count"]) is not int or not 1 <= manifest["count"] <= caps.max_chunks
            or manifest["count"] != len(frames) - 1
            or type(manifest["decoded_bytes"]) is not int
            or not 0 < manifest["decoded_bytes"] <= caps.max_decoded_bytes
            or any(type(manifest[key]) is not str or not manifest[key]
                   for key in BINDING_FIELDS - {"contract_version"} | {"sha256"})):
        raise ValueError("transport_manifest_binding_or_count")
    binding = {key: manifest[key] for key in BINDING_FIELDS}
    decoded = bytearray()
    for index, line in enumerate(frames[:-1]):
        tick()
        notice = strict_load(line)
        if (type(notice) is not dict or set(notice) != {"jsonrpc", "method", "params"}
                or notice["jsonrpc"] != "2.0" or notice["method"] != CHUNK_METHOD):
            raise ValueError("transport_chunk_notification")
        item = notice["params"]
        if (type(item) is not dict or set(item) != BINDING_FIELDS | {
                "count", "index", "chunk_bytes", "chunk_sha256", "data"}
                or any(type(item[key]) is not type(binding[key]) or item[key] != binding[key]
                       for key in binding)
                or type(item["count"]) is not int or item["count"] != manifest["count"]
                or type(item["index"]) is not int or item["index"] != index
                or type(item["chunk_bytes"]) is not int or item["chunk_bytes"] <= 0
                or item["chunk_bytes"] > manifest["decoded_bytes"] - len(decoded)
                or type(item["data"]) is not str or type(item["chunk_sha256"]) is not str):
            raise ValueError("transport_chunk_binding_or_order")
        try:
            part = base64.b64decode(item["data"].encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error) as error:
            raise ValueError("transport_chunk_encoding") from error
        if (base64.b64encode(part).decode("ascii") != item["data"]
                or len(part) != item["chunk_bytes"]
                or hashlib.sha256(part).hexdigest() != item["chunk_sha256"]):
            raise ValueError("transport_chunk_digest")
        decoded.extend(part)
    tick()
    if len(decoded) != manifest["decoded_bytes"] or hashlib.sha256(decoded).hexdigest() != manifest["sha256"]:
        raise ValueError("transport_whole_digest")
    return bytes(decoded), report
