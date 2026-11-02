"""显式 V3 发布及有界无损传输；不改变旧入口的版本分派。

传输绑定不是来源认证，来源认证不是人工语义或物理消费证明。
只操作调用方给出的内存对象；不读写磁盘、不联网、不执行来源。
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json

from .adaptive_wire import TransportCapabilities
from .core_v3 import SealedCore, inspect_seal
from .macro_v3 import canonical
from .representation_v3 import ENCODING as REPRESENTATION_ENCODING, pack, unpack
from .subtree_v3 import ENCODING as SUBTREE_ENCODING, pack as subtree_pack, unpack as subtree_unpack
from .lz_v3 import ENCODING as LZ_ENCODING, pack as lz_pack, unpack as lz_unpack
from .snapshot_v2 import snapshot_scope
from .task_v3 import build_task
from .wire_v2 import FrozenFrame, FrozenTransfer, PROTOCOL_VERSION, response_id

POLICY = "adaptive-backpressure-v1"
METHOD = "notifications/solo_distill/chunk-v1"
ENCODING = "base64-jsonrpc-wire-v1"
WIRE_ENCODINGS = ("json", REPRESENTATION_ENCODING, SUBTREE_ENCODING, LZ_ENCODING)
CEILINGS = {"max_frame_bytes": 22016, "max_transfer_bytes": 1572864,
            "max_decoded_bytes": 1048576, "max_chunks": 512}
BINDING_KEYS = {"project_id", "snapshot_id", "input_id", "core_id", "selection_id",
                "evidence_id", "task_request_id", "question_id", "post_sha256",
                "policy_id", "contract_version", "encoding", "wire_encoding"}
DIGEST_KEYS = {"input_id", "core_id", "evidence_id", "question_id", "post_sha256"}
REPORT_KEYS = {"contract_version", "kind", "status", "core", "task_evidence", "observations"}
STATUSES = {"complete", "partial", "blocked", "failed"}


@dataclass(frozen=True)
class Admission:
    protocol_version: str
    scenario: str
    request_id: str | int
    return_bytes: int
    transport: TransportCapabilities
    wire_encoding: str = "json"

    def __post_init__(self):
        if type(self.wire_encoding) is not str or self.wire_encoding not in WIRE_ENCODINGS:
            raise ValueError("v3_transport_unnegotiated_wire_encoding")
        if (type(self.protocol_version) is not str
                or self.protocol_version != PROTOCOL_VERSION
                or type(self.scenario) is not str or self.scenario != "A"):
            raise ValueError("v3_transport_protocol_or_scenario")
        response_id(self.request_id)
        if type(self.return_bytes) is not int or self.return_bytes < 1:
            raise ValueError("v3_transport_return_budget")
        if type(self.transport) is not TransportCapabilities:
            raise ValueError("v3_transport_explicit_capabilities_required")
        caps = self.transport
        if (caps.policy != POLICY or caps.authority != "caller_explicit"
                or any(getattr(caps, key) > ceiling for key, ceiling in CEILINGS.items())):
            raise ValueError("v3_transport_profile_ceiling_or_authority")

    @property
    def budget(self):
        return min(self.return_bytes, self.transport.max_frame_bytes)


def _admission(value):
    if type(value) is not Admission:
        raise ValueError("v3_explicit_admission_required")
    value.__post_init__()
    return value


def _load(raw):
    if type(raw) is not bytes:
        raise ValueError("v3_transport_bytes_required")

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("v3_transport_duplicate_json_key")
            value[key] = item
        return value

    def reject(value):
        raise ValueError("v3_transport_noninteger_json_number")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_float=reject, parse_constant=reject)
        canonical(value)
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("v3_transport_invalid_json") from error


def _encode(report, request_id, *, wire_encoding="json", tick=None):
    if type(wire_encoding) is not str or wire_encoding not in WIRE_ENCODINGS:
        raise ValueError("v3_transport_unnegotiated_wire_encoding")
    represented = report
    if report.get("kind") == "sealed_knowledge":
        if wire_encoding == REPRESENTATION_ENCODING:
            represented = pack(report, tick=tick)
        elif wire_encoding == SUBTREE_ENCODING:
            represented = subtree_pack(report, tick=tick)
        elif wire_encoding == LZ_ENCODING:
            represented = lz_pack(report, tick=tick)
    return canonical({"jsonrpc": "2.0", "id": request_id, "result": {
        "content": [{"type": "text", "text": canonical(represented).decode("utf-8")}],
        "isError": report["status"] in {"blocked", "failed"}}}) + b"\n"


def decode_report(raw, admission, *, tick=None):
    """先核对实际帧字节，再按显式协商有界还原；清单保持原表示。"""
    tick = tick if tick is not None else (lambda: None)
    tick()
    _admission(admission)
    if (type(raw) is not bytes or not raw.endswith(b"\n") or b"\n" in raw[:-1]
            or len(raw) > admission.transport.max_decoded_bytes):
        raise ValueError("v3_response_boundary_or_decode_budget")
    envelope = _load(raw)
    if (canonical(envelope) + b"\n" != raw or type(envelope) is not dict
            or set(envelope) != {"jsonrpc", "id", "result"}
            or envelope["jsonrpc"] != "2.0"
            or type(envelope["id"]) is not type(admission.request_id)
            or envelope["id"] != admission.request_id):
        raise ValueError("v3_response_identity_or_encoding")
    result = envelope["result"]
    if (type(result) is not dict or set(result) != {"content", "isError"}
            or type(result["isError"]) is not bool
            or type(result["content"]) is not list or len(result["content"]) != 1):
        raise ValueError("v3_response_result")
    text = result["content"][0]
    if (type(text) is not dict or set(text) != {"type", "text"}
            or text["type"] != "text" or type(text["text"]) is not str):
        raise ValueError("v3_response_text")
    raw_report = text["text"].encode("utf-8")
    report = _load(raw_report)
    if type(report) is not dict or canonical(report) != raw_report:
        raise ValueError("v3_response_report_encoding_or_status")
    if report.get("kind") != "chunk_manifest":
        if admission.wire_encoding == REPRESENTATION_ENCODING:
            report = unpack(report, tick=tick)
        elif admission.wire_encoding == SUBTREE_ENCODING:
            report = subtree_unpack(report, tick=tick)
        elif admission.wire_encoding == LZ_ENCODING:
            report = lz_unpack(report, tick=tick)
    if (type(report) is not dict
            or type(report.get("status")) is not str or report["status"] not in STATUSES
            or result["isError"] != (report["status"] in {"blocked", "failed"})):
        raise ValueError("v3_response_report_encoding_or_status")
    tick()
    return report


def _check_binding(binding, wire_encoding="json"):
    if (type(wire_encoding) is not str or wire_encoding not in WIRE_ENCODINGS
            or type(binding) is not dict or set(binding) != BINDING_KEYS
            or type(binding["contract_version"]) is not int
            or binding["contract_version"] != 3
            or any(type(binding[key]) is not str or not binding[key]
                   for key in BINDING_KEYS - {"contract_version"})
            or binding["policy_id"] != POLICY or binding["encoding"] != ENCODING
            or binding["wire_encoding"] != wire_encoding
            or any(len(binding[key]) != 64
                   or any(char not in "0123456789abcdef" for char in binding[key])
                   for key in DIGEST_KEYS)):
        raise ValueError("v3_transport_binding_fields_or_version")


def publication_binding(report, wire_encoding="json"):
    """检查发布形状及封印身份，不把摘要当作可信源码认证。"""
    if (type(report) is not dict or set(report) != REPORT_KEYS
            or type(report["contract_version"]) is not int or report["contract_version"] != 3
            or report["kind"] != "sealed_knowledge"
            or type(report["status"]) is not str or report["status"] not in STATUSES
            or type(report["observations"]) is not dict
            or len(canonical(report["observations"])) > 512):
        raise ValueError("v3_publication_fields_or_observation_budget")
    core = inspect_seal(SealedCore(canonical(report["core"])))
    task = report["task_evidence"]
    if (type(task) is not dict or report["status"] != task.get("status")
            or any(type(task.get(key)) is not type(value) or task[key] != value
                   for key, value in {"core_id": core["core_id"], "input_id": core["input_id"],
                       "selection_id": core["selection_id"],
                       "project_id": core["package"]["project_id"]}.items())):
        raise ValueError("v3_publication_task_core_binding")
    binding = {"project_id": core["package"]["project_id"],
               "snapshot_id": core["package"]["snapshot_id"],
               "input_id": core["input_id"], "core_id": core["core_id"],
               "selection_id": core["selection_id"], "evidence_id": task.get("evidence_id"),
               "task_request_id": task.get("request_id"), "question_id": task.get("question_id"),
               "post_sha256": task.get("post_sha256"), "policy_id": POLICY,
               "contract_version": 3, "encoding": ENCODING, "wire_encoding": wire_encoding}
    _check_binding(binding, wire_encoding)
    return binding


@snapshot_scope
def build_publication(core, task, raw_files, *, candidate, observations=None, **trusted):
    """与包外原始生成对象及失败记录对账，不从任务反推期望文本。"""
    tick = trusted.get("tick") or (lambda: None)
    tick()
    observations = {} if observations is None else observations
    if type(observations) is not dict or len(canonical(observations)) > 512:
        raise ValueError("v3_publication_observation_budget")
    expected_task = build_task(candidate, core, raw_files, **trusted)
    if canonical(task) != canonical(expected_task):
        raise ValueError("v3_publication_raw_candidate_or_task_conflict")
    report = {"contract_version": 3, "kind": "sealed_knowledge",
              "status": expected_task["status"], "core": inspect_seal(core),
              "task_evidence": expected_task, "observations": observations}
    publication_binding(report)
    tick()
    return _load(canonical(report))


def _chunks(encoded, report, admission, tick):
    caps = admission.transport
    if not caps.chunking:
        raise ValueError("v3_transport_chunking_not_negotiated")
    binding = publication_binding(report, admission.wire_encoding)
    sample = {**binding, "count": caps.max_chunks, "index": caps.max_chunks,
              "chunk_bytes": caps.max_decoded_bytes, "chunk_sha256": "0" * 64, "data": ""}
    overhead = len(canonical({"jsonrpc": "2.0", "method": METHOD, "params": sample})) + 1
    size = (admission.budget - overhead - 16) // 4 * 3
    if size < 1:
        raise ValueError("v3_transport_chunk_metadata_budget")
    count = (len(encoded) + size - 1) // size
    if count > caps.max_chunks:
        raise ValueError("v3_transport_chunk_count_budget")
    frames = []
    total = 0
    for index, offset in enumerate(range(0, len(encoded), size)):
        tick()
        part = encoded[offset:offset + size]
        params = {**binding, "count": count, "index": index, "chunk_bytes": len(part),
                  "chunk_sha256": hashlib.sha256(part).hexdigest(),
                  "data": base64.b64encode(part).decode("ascii")}
        frame = canonical({"jsonrpc": "2.0", "method": METHOD, "params": params}) + b"\n"
        total += len(frame)
        if len(frame) > admission.budget or total > caps.max_transfer_bytes:
            raise ValueError("v3_transport_chunk_frame_or_transfer_budget")
        frames.append(frame)
    manifest = {**binding, "count": count, "decoded_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest()}
    final = _encode({"contract_version": 3, "kind": "chunk_manifest",
                     "status": report["status"], "manifest": manifest}, admission.request_id)
    if len(final) > admission.budget or total + len(final) > caps.max_transfer_bytes:
        raise ValueError("v3_transport_manifest_or_transfer_budget")
    frames.append(final)
    tick()
    return tuple(frames)


@snapshot_scope
def publish_v3(core, task, raw_files, admission, *, candidate, observations=None, **trusted):
    """冻结完整发布；容量不足拒绝，不截断或降级为旧版响应。"""
    _admission(admission)
    tick = trusted.get("tick") or (lambda: None)
    tick()
    report = build_publication(core, task, raw_files, candidate=candidate,
                               observations=observations, **trusted)
    encoded = _encode(report, admission.request_id,
                      wire_encoding=admission.wire_encoding, tick=tick)
    if len(encoded) > admission.transport.max_decoded_bytes:
        raise ValueError("v3_transport_decode_budget")
    if len(encoded) <= admission.budget:
        if len(encoded) > admission.transport.max_transfer_bytes:
            raise ValueError("v3_transport_transfer_budget")
        frame = FrozenFrame(encoded, admission, True)
    else:
        frame = FrozenTransfer(_chunks(encoded, report, admission, tick), admission, True)
    tick()
    return frame


def publish(core, task, raw_files, admission, *, candidate, observations=None, **trusted):
    """保留本模块既有显式 V3 名称；绝不经过 V2 发布或改变旧入口。"""
    return publish_v3(core, task, raw_files, admission, candidate=candidate,
                      observations=observations, **trusted)


def _reassemble_report(raw, admission, *, tick=None):
    """本调用完整核验后返回发布视图；不缓存、不接受外部认证豁免。"""
    tick = tick if tick is not None else (lambda: None)
    tick()
    _admission(admission)
    caps = admission.transport
    if (type(raw) is not bytes or not raw.endswith(b"\n")
            or len(raw) > caps.max_transfer_bytes or raw.count(b"\n") > caps.max_chunks + 1):
        raise ValueError("v3_transport_input_or_transfer_budget")
    frames = raw.split(b"\n")
    frames.pop()
    if not frames or any(len(frame) + 1 > admission.budget for frame in frames):
        raise ValueError("v3_transport_frame_budget")
    manifest_report = None
    if len(frames) == 1:
        decoded = raw
    else:
        if not caps.chunking:
            raise ValueError("v3_transport_chunking_not_negotiated")
        manifest_report = decode_report(frames[-1] + b"\n", admission, tick=tick)
        if (set(manifest_report) != {"contract_version", "kind", "status", "manifest"}
                or type(manifest_report["contract_version"]) is not int
                or manifest_report["contract_version"] != 3
                or manifest_report["kind"] != "chunk_manifest"):
            raise ValueError("v3_transport_manifest_fields_or_version")
        manifest = manifest_report["manifest"]
        if type(manifest) is not dict or set(manifest) != BINDING_KEYS | {"count", "decoded_bytes", "sha256"}:
            raise ValueError("v3_transport_manifest_fields")
        binding = {key: manifest[key] for key in BINDING_KEYS}
        _check_binding(binding, admission.wire_encoding)
        if (type(manifest["count"]) is not int or not 1 <= manifest["count"] <= caps.max_chunks
                or manifest["count"] != len(frames) - 1
                or type(manifest["decoded_bytes"]) is not int
                or not 0 < manifest["decoded_bytes"] <= caps.max_decoded_bytes
                or type(manifest["sha256"]) is not str or len(manifest["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in manifest["sha256"])):
            raise ValueError("v3_transport_manifest_count_or_digest")
        data = bytearray()
        for index, raw_notice in enumerate(frames[:-1]):
            tick()
            notice = _load(raw_notice)
            if (canonical(notice) != raw_notice or type(notice) is not dict
                    or set(notice) != {"jsonrpc", "method", "params"}
                    or notice["jsonrpc"] != "2.0" or notice["method"] != METHOD):
                raise ValueError("v3_transport_notification_fields_or_encoding")
            item = notice["params"]
            if (type(item) is not dict or set(item) != BINDING_KEYS | {
                    "count", "index", "chunk_bytes", "chunk_sha256", "data"}
                    or any(type(item[key]) is not type(value) or item[key] != value
                           for key, value in binding.items())
                    or type(item["index"]) is not int or item["index"] != index
                    or type(item["count"]) is not int or item["count"] != manifest["count"]
                    or type(item["chunk_bytes"]) is not int
                    or not 0 < item["chunk_bytes"] <= manifest["decoded_bytes"] - len(data)
                    or type(item["data"]) is not str or type(item["chunk_sha256"]) is not str):
                raise ValueError("v3_transport_chunk_binding_order_or_length")
            try:
                part = base64.b64decode(item["data"].encode("ascii"), validate=True)
            except (UnicodeError, binascii.Error) as error:
                raise ValueError("v3_transport_chunk_base64") from error
            if (base64.b64encode(part).decode("ascii") != item["data"]
                    or len(part) != item["chunk_bytes"]
                    or hashlib.sha256(part).hexdigest() != item["chunk_sha256"]):
                raise ValueError("v3_transport_chunk_digest")
            data.extend(part)
        if len(data) != manifest["decoded_bytes"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise ValueError("v3_transport_whole_digest")
        decoded = bytes(data)
    tick()
    report = decode_report(decoded, admission, tick=tick)
    binding = publication_binding(report, admission.wire_encoding)
    if manifest_report is not None:
        if (manifest_report["status"] != report["status"]
                or any(type(manifest_report["manifest"][key]) is not type(value)
                       or manifest_report["manifest"][key] != value for key, value in binding.items())):
            raise ValueError("v3_transport_manifest_publication_conflict")
    tick()
    return decoded, manifest_report, report


def reassemble(raw, admission, *, tick=None):
    """先计量再重组；保留公开双返回值，消费端另认证可信源码。"""
    decoded, manifest_report, _ = _reassemble_report(raw, admission, tick=tick)
    return decoded, manifest_report
