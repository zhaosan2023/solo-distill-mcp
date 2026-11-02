"""Schema 3.0 独立传输与发布接收器；不导入生产模块。

从调用方可信源码、包外核心清单、冻结 POST 和原始生成对象复算。
分片认证不证明网络发送、选择独立性、人工语义或宿主加载。
共享标准库 AST 的共同失效风险仍保留。这里只处理内存中的字节。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

from schema_v3_core_oracle import expected_core
from schema_v3_macro_oracle import canonical
from schema_v3_task_oracle import expected_task
from schema_v3_representation_oracle import ENCODING as REPRESENTATION_ENCODING, unpack
from schema_v3_subtree_oracle import ENCODING as SUBTREE_ENCODING, unpack as subtree_unpack
from schema_v3_lz_oracle import ENCODING as LZ_ENCODING, unpack as lz_unpack

POLICY = "adaptive-backpressure-v1"
PROTOCOL = "2024-11-05"
METHOD = "notifications/solo_distill/chunk-v1"
ENCODING = "base64-jsonrpc-wire-v1"
CEILINGS = {"max_frame_bytes": 22016, "max_transfer_bytes": 1572864,
            "max_decoded_bytes": 1048576, "max_chunks": 512}
CAPABILITY_KEYS = set(CEILINGS) | {"policy", "authority", "chunking"}
BINDING_KEYS = {"project_id", "snapshot_id", "input_id", "core_id", "selection_id",
                "evidence_id", "task_request_id", "question_id", "post_sha256",
                "policy_id", "contract_version", "encoding", "wire_encoding"}
DIGEST_KEYS = {"input_id", "core_id", "evidence_id", "question_id", "post_sha256"}
PUBLICATION_KEYS = {"contract_version", "kind", "status", "core",
                    "task_evidence", "observations"}
STATUSES = {"complete", "partial", "blocked", "failed"}
STRUCTURE_KEYS = {"project_id", "snapshot_id", "required_core_unit_ids", "selection_id"}


def _tick(tick):
    return tick if tick is not None else (lambda: None)


def _require(condition, reason):
    if not condition:
        raise ValueError("independent_v3_wire_" + reason)


def _load(raw):
    _require(type(raw) is bytes, "bytes_required")

    def pairs(items):
        result = {}
        for key, item in items:
            _require(key not in result, "duplicate_json_key")
            result[key] = item
        return result

    def reject(value):
        raise ValueError("independent_v3_wire_noninteger_json_number")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_float=reject, parse_constant=reject)
        canonical(value)
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("independent_v3_wire_invalid_json") from error


def _identity(value):
    _require(type(value) in (str, int), "response_identity_type")
    text = str(value)
    _require(len(text) <= 128 and len(text.encode("utf-8")) <= 512,
             "response_identity_budget")


def _capabilities(value, return_bytes):
    _require(type(value) is dict and set(value) == CAPABILITY_KEYS,
             "capability_fields")
    _require(type(value["policy"]) is str and value["policy"] == POLICY
             and type(value["authority"]) is str
             and value["authority"] == "caller_explicit"
             and type(value["chunking"]) is bool, "capability_authority")
    _require(all(type(value[key]) is int and 1 <= value[key] <= ceiling
                 for key, ceiling in CEILINGS.items()), "profile_ceiling_or_type")
    _require(1024 <= value["max_frame_bytes"] <= value["max_transfer_bytes"],
             "capability_frame_budget")
    _require(type(return_bytes) is int and return_bytes > 0, "return_budget")
    return min(return_bytes, value["max_frame_bytes"])


def _envelope(raw, response_id, *, wire_encoding="json", tick=None):
    _require(type(wire_encoding) is str
             and wire_encoding in ("json", REPRESENTATION_ENCODING, SUBTREE_ENCODING, LZ_ENCODING), "wire_encoding")
    tick = _tick(tick)
    tick()
    _require(raw.endswith(b"\n") and b"\n" not in raw[:-1], "response_boundary")
    value = _load(raw)
    _require(canonical(value) + b"\n" == raw, "response_canonical_encoding")
    _require(type(value) is dict and set(value) == {"jsonrpc", "id", "result"}
             and value["jsonrpc"] == "2.0"
             and type(value["id"]) is type(response_id)
             and value["id"] == response_id, "response_identity")
    result = value["result"]
    _require(type(result) is dict and set(result) == {"content", "isError"}
             and type(result["isError"]) is bool
             and type(result["content"]) is list and len(result["content"]) == 1,
             "response_result")
    content = result["content"][0]
    _require(type(content) is dict and set(content) == {"type", "text"}
             and content["type"] == "text" and type(content["text"]) is str,
             "response_text")
    report_bytes = content["text"].encode("utf-8")
    report = _load(report_bytes)
    _require(canonical(report) == report_bytes, "report_canonical_encoding")
    if wire_encoding == REPRESENTATION_ENCODING:
        report = unpack(report, tick=tick)
    elif wire_encoding == SUBTREE_ENCODING:
        report = subtree_unpack(report, tick=tick)
    elif wire_encoding == LZ_ENCODING:
        report = lz_unpack(report, tick=tick)
    _require(type(report) is dict and type(report.get("status")) is str
             and report["status"] in STATUSES
             and result["isError"] == (report["status"] in {"blocked", "failed"}),
             "response_status")
    return report


def _binding(core, task, wire_encoding="json"):
    _require(type(core) is dict and type(core.get("package")) is dict
             and type(task) is dict, "publication_binding_shape")
    package = core["package"]
    binding = {"project_id": package.get("project_id"),
               "snapshot_id": package.get("snapshot_id"),
               "input_id": core.get("input_id"), "core_id": core.get("core_id"),
               "selection_id": core.get("selection_id"),
               "evidence_id": task.get("evidence_id"),
               "task_request_id": task.get("request_id"),
               "question_id": task.get("question_id"),
               "post_sha256": task.get("post_sha256"), "policy_id": POLICY,
               "contract_version": 3, "encoding": ENCODING, "wire_encoding": wire_encoding}
    _binding_shape(binding, wire_encoding)
    _require(core.get("policy_id") == POLICY
             and all(type(task.get(key)) is type(binding[key])
                     and task[key] == binding[key]
                     for key in ("core_id", "input_id", "selection_id", "project_id")),
             "task_core_binding")
    return binding


def _binding_shape(binding, wire_encoding="json"):
    _require(type(wire_encoding) is str
             and wire_encoding in ("json", REPRESENTATION_ENCODING, SUBTREE_ENCODING, LZ_ENCODING), "wire_encoding")
    _require(type(binding) is dict and set(binding) == BINDING_KEYS,
             "binding_fields")
    _require(type(binding["contract_version"]) is int
             and binding["contract_version"] == 3
             and all(type(binding[key]) is str and bool(binding[key])
                     for key in BINDING_KEYS - {"contract_version"}), "binding_types")
    _require(binding["policy_id"] == POLICY and binding["encoding"] == ENCODING
             and binding["wire_encoding"] == wire_encoding, "binding_version_or_encoding")
    _require(all(len(binding[key]) == 64
                 and all(character in "0123456789abcdef" for character in binding[key])
                 for key in DIGEST_KEYS), "binding_digest_shape")


def _publication(report, wire_encoding="json"):
    _require(set(report) == PUBLICATION_KEYS
             and type(report["contract_version"]) is int
             and report["contract_version"] == 3
             and report["kind"] == "sealed_knowledge", "publication_fields_or_version")
    _require(type(report["observations"]) is dict
             and len(canonical(report["observations"])) <= 512, "observation_budget")
    binding = _binding(report["core"], report["task_evidence"], wire_encoding)
    _require(report["status"] == report["task_evidence"].get("status"),
             "publication_task_status")
    return binding


def expected_publication(candidate, raw_files, *, observations=None, **trusted):
    """独立生成完整期望；生成文本和失败列表必须来自包外调用方。"""
    tick = _tick(trusted.get("tick"))
    tick()
    observations = {} if observations is None else observations
    _require(type(observations) is dict and len(canonical(observations)) <= 512,
             "observation_budget")
    task = expected_task(candidate, raw_files, **trusted)
    core = expected_core(raw_files, **{key: trusted[key] for key in STRUCTURE_KEYS},
                         tick=tick)
    value = {"contract_version": 3, "kind": "sealed_knowledge",
             "status": task["status"], "core": core, "task_evidence": task,
             "observations": observations}
    _publication(value)
    tick()
    return json.loads(canonical(value))


def _reassemble_report(raw, capabilities, *, response_id, return_bytes, tick=None,
                       wire_encoding="json"):
    """独立实现本调用完整传输核验；不共享生产解码或缓存期望。"""
    tick = _tick(tick)
    tick()
    _require(type(wire_encoding) is str
             and wire_encoding in ("json", REPRESENTATION_ENCODING, SUBTREE_ENCODING, LZ_ENCODING), "wire_encoding")
    _identity(response_id)
    budget = _capabilities(capabilities, return_bytes)
    _require(type(raw) is bytes and raw.endswith(b"\n")
             and len(raw) <= capabilities["max_transfer_bytes"], "transfer_budget_or_boundary")
    _require(raw.count(b"\n") <= capabilities["max_chunks"] + 1, "physical_frame_count")
    frames = [part + b"\n" for part in raw.split(b"\n")[:-1]]
    _require(bool(frames) and all(len(frame) <= budget for frame in frames), "frame_budget")
    manifest_report = None
    if len(frames) == 1:
        _require(len(raw) <= capabilities["max_decoded_bytes"], "decoded_budget")
        decoded = raw
    else:
        _require(capabilities["chunking"], "chunking_not_negotiated")
        manifest_report = _envelope(frames[-1], response_id, tick=tick)
        _require(set(manifest_report) == {"contract_version", "kind", "status", "manifest"}
                 and type(manifest_report["contract_version"]) is int
                 and manifest_report["contract_version"] == 3
                 and manifest_report["kind"] == "chunk_manifest", "manifest_version_or_fields")
        manifest = manifest_report["manifest"]
        _require(type(manifest) is dict
                 and set(manifest) == BINDING_KEYS | {"count", "decoded_bytes", "sha256"},
                 "manifest_fields")
        binding = {key: manifest[key] for key in BINDING_KEYS}
        _binding_shape(binding, wire_encoding)
        _require(type(manifest["count"]) is int
                 and 1 <= manifest["count"] <= capabilities["max_chunks"]
                 and manifest["count"] == len(frames) - 1, "manifest_count")
        _require(type(manifest["decoded_bytes"]) is int
                 and 0 < manifest["decoded_bytes"] <= capabilities["max_decoded_bytes"]
                 and type(manifest["sha256"]) is str
                 and len(manifest["sha256"]) == 64
                 and all(c in "0123456789abcdef" for c in manifest["sha256"]),
                 "manifest_length_or_digest")
        pieces = bytearray()
        for index, frame in enumerate(frames[:-1]):
            tick()
            notice = _load(frame)
            _require(canonical(notice) + b"\n" == frame, "notification_canonical_encoding")
            _require(type(notice) is dict and set(notice) == {"jsonrpc", "method", "params"}
                     and notice["jsonrpc"] == "2.0" and notice["method"] == METHOD,
                     "notification_fields")
            item = notice["params"]
            _require(type(item) is dict and set(item) == BINDING_KEYS | {
                "count", "index", "chunk_bytes", "chunk_sha256", "data"}, "chunk_fields")
            _require(all(type(item[key]) is type(binding[key]) and item[key] == binding[key]
                         for key in binding)
                     and type(item["index"]) is int and item["index"] == index
                     and type(item["count"]) is int and item["count"] == manifest["count"],
                     "chunk_binding_or_order")
            _require(type(item["chunk_bytes"]) is int and item["chunk_bytes"] > 0
                     and item["chunk_bytes"] <= manifest["decoded_bytes"] - len(pieces)
                     and type(item["data"]) is str and type(item["chunk_sha256"]) is str,
                     "chunk_length_or_type")
            try:
                part = base64.b64decode(item["data"].encode("ascii"), validate=True)
            except (UnicodeError, binascii.Error) as error:
                raise ValueError("independent_v3_wire_chunk_base64") from error
            _require(base64.b64encode(part).decode("ascii") == item["data"]
                     and len(part) == item["chunk_bytes"]
                     and hashlib.sha256(part).hexdigest() == item["chunk_sha256"], "chunk_digest")
            pieces.extend(part)
        decoded = bytes(pieces)
        _require(len(decoded) == manifest["decoded_bytes"]
                 and hashlib.sha256(decoded).hexdigest() == manifest["sha256"], "whole_digest")
    tick()
    report = _envelope(decoded, response_id, wire_encoding=wire_encoding, tick=tick)
    actual_binding = _publication(report, wire_encoding)
    if manifest_report is not None:
        _require(all(type(manifest_report["manifest"][key]) is type(value)
                     and manifest_report["manifest"][key] == value
                     for key, value in actual_binding.items())
                 and manifest_report["status"] == report["status"], "manifest_publication_conflict")
    tick()
    return decoded, manifest_report, report


def reassemble(raw, capabilities, *, response_id, return_bytes, tick=None,
               wire_encoding="json"):
    """保留公开双返回值；完整传输及发布绑定仍由独立实现核验。"""
    decoded, manifest_report, _ = _reassemble_report(raw, capabilities,
        response_id=response_id, return_bytes=return_bytes, tick=tick,
        wire_encoding=wire_encoding)
    return decoded, manifest_report


def receive(raw, raw_files, capabilities, *, protocol_version, response_id,
            return_bytes, candidate, observations=None, wire_encoding="json", **trusted):
    """完整规范比较；收到的文本和失败列表不作为自身期望的来源。"""
    _require(type(protocol_version) is str and protocol_version == PROTOCOL, "protocol_version")
    tick = _tick(trusted.get("tick"))
    # 仅复用本次独立传输核验得到的完整发布；包外可信输入的全部
    # 期望仍在下方独立复算，不共享生产对象、不跨调用缓存认证。
    decoded, manifest, report = _reassemble_report(raw, capabilities,
        response_id=response_id, return_bytes=return_bytes, tick=tick,
        wire_encoding=wire_encoding)
    expected = expected_publication(candidate, raw_files, observations=observations, **trusted)
    _require(canonical(report) == canonical(expected), "publication_or_trusted_input_conflict")
    tick()
    return {"status": "pass", "task_status": expected["status"],
            "publication": expected, "decoded_bytes": len(decoded),
            "transfer_bytes": len(raw), "physical_frames": raw.count(b"\n"),
            "notification_chunks": 0 if manifest is None else manifest["manifest"]["count"],
            "proof_scope": "bounded_lossless_transport_and_source_recomputed_publication",
            "actual_network_send": "not_proven_by_this_asset",
            "selection_independence": "not_checked_by_this_asset",
            "business_semantics": "requires_real_independent_human_review",
            "host_loading": "not_proven_by_this_asset",
            "common_failure_risk": "shared_python_standard_library_ast"}
