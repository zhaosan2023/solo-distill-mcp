"""显式 Schema 3.0 服务链；旧服务、发布和消费者不参与版本转换。

离线 preflight 不建连；live 仅在显式服务授权及外部冻结选择下运行。
生产链不导入独立 Oracle；离线独立验收和六份证据由冷预检驱动完成。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

from .adaptive_wire import TransportCapabilities
from .distillation import DIMENSION_SCHEMA, Distiller, utf8_json
from .extractor import InputError, open_root, open_relative, relative_path, within
from .gateway import (Gateway, GatewayError, InvalidCandidateSyntax, candidate_json,
                      encode_request, recovery_candidate_json, strict_json)
from .preparation_v3 import prepare_snapshot
from .selection_ingress_v3 import ENCODING as SELECTION_INGRESS_ENCODING, resolve_selection
from .scouting_v3 import COVERAGE_VERSION, VERSION
from .snapshot_v2 import snapshot_scope
from .task_v3 import InvalidSourceCandidate, _question_identity, build_task
from .wire_v3 import Admission, WIRE_ENCODINGS, publish_v3

TOOL_NAME = "solo_distill_knowledge_v3"
LIVE_PROCESS_SECONDS = 600
MINIMAL_RECOVERY_SUFFIX = "-live-minimal-20261102T020451Z"
COVERAGE_RECOVERY_SUFFIX = "-live-coverage-20261102T050022Z"
CANDIDATE_RECOVERY_SUFFIX = "-live-candidate-20261102T070153Z"
SYNTAX_RECOVERY_SUFFIX = "-live-syntax-20261102T085352Z"
INPUT_SCHEMA = {
    "type": "object", "properties": {
        "contract_version": {"type": "integer", "enum": [3]},
        "protocol_version": {"enum": ["2024-11-05"]},
        "operation": {"enum": ["preflight", "live"]},
        "request_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
        "project_id": {"type": "string"}, "scenario": {"enum": ["A"]},
        "question": {"type": "string", "minLength": 1, "maxLength": 4096},
        "selection_version": {"type": "string", "enum": [VERSION, COVERAGE_VERSION]},
        "dimensions": {"type": "array", "minItems": 1, "maxItems": 16,
            "items": {**DIMENSION_SCHEMA, "properties": {
                key: value for key, value in DIMENSION_SCHEMA["properties"].items()
                if key != "selector"}}},
        "include": {"type": "array", "minItems": 1, "maxItems": 32,
                    "items": {"type": "string"}},
        "started_at_unix_ms": {"type": "integer"},
        "deadline_unix_ms": {"type": "integer"},
        "host_timeout_seconds": {"type": "integer", "minimum": 1, "maximum": LIVE_PROCESS_SECONDS},
        "return_bytes": {"type": "integer", "minimum": 1, "maximum": 22016},
        "wire_encoding": {"type": "string", "enum": list(WIRE_ENCODINGS)},
        "transport": {"type": "object", "properties": {
            "policy": {"enum": ["adaptive-backpressure-v1"]},
            "authority": {"enum": ["caller_explicit"]},
            "max_frame_bytes": {"type": "integer", "minimum": 1, "maximum": 22016},
            "max_transfer_bytes": {"type": "integer", "minimum": 1, "maximum": 1572864},
            "max_decoded_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576},
            "max_chunks": {"type": "integer", "minimum": 1, "maximum": 512},
            "chunking": {"type": "boolean"}},
            "required": ["policy", "authority", "max_frame_bytes", "max_transfer_bytes",
                         "max_decoded_bytes", "max_chunks", "chunking"],
            "additionalProperties": False},
        "recover_from_request_id": {"type": "string"},
        "frozen_selection": {"type": "object"},
        "frozen_selection_lz": {
            "type": "object", "properties": {
                "encoding": {"type": "string", "enum": [SELECTION_INGRESS_ENCODING]},
                "payload": {"type": "object", "properties": {
                    "encoding": {"type": "string", "enum": ["lz-json-v3-1"]},
                    "raw_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "data": {"type": "string", "minLength": 1, "maxLength": 65536}},
                    "required": ["encoding", "raw_bytes", "sha256", "data"],
                    "additionalProperties": False}},
            "required": ["encoding", "payload"], "additionalProperties": False},
    }, "required": ["contract_version", "protocol_version", "operation", "request_id",
        "project_id", "scenario", "question", "dimensions", "started_at_unix_ms",
        "deadline_unix_ms", "host_timeout_seconds", "return_bytes", "transport"],
    "additionalProperties": False,
    "allOf": [{"if": {"properties": {"operation": {"const": "live"}}},
        "then": {"oneOf": [
            {"required": ["frozen_selection"]},
            {"required": ["frozen_selection_lz"]},
            {"required": ["compressed_frozen_selection"]}]},
        "else": {"not": {"anyOf": [
            {"required": ["frozen_selection_lz"]},
            {"required": ["compressed_frozen_selection"]}]}}}],
}
# 新名称沿用相同用途、封闭头部与保护；旧显式字段仅作兼容输入。
INPUT_SCHEMA["properties"]["compressed_frozen_selection"] = json.loads(
    json.dumps(INPUT_SCHEMA["properties"]["frozen_selection_lz"]))


class V3Distiller(Distiller):
    def __init__(self, projects, policy, gateway=None, *, artifact_root=None, online=False):
        super().__init__(projects, policy, gateway or Gateway(absolute_timeout=True), artifact_root=artifact_root)
        self.online = online is True
        self.used = False
        self.business_deadline = None
        self.cancelled = threading.Event()

    def check(self):
        if self.cancelled.is_set() or (self.business_deadline is not None
                                     and time.monotonic() >= self.business_deadline):
            raise InputError("v3_service_cancelled_or_deadline")

    def persist_scratch(self, request_id, value):
        self.check()
        raw = utf8_json(value)
        path = super().persist_scratch(request_id, value)
        self.check()
        root = open_root(str(self.artifact_root))
        try:
            descriptor = open_relative(root, path)
            try:
                chunks = bytearray()
                while len(chunks) < len(raw):
                    self.check()
                    part = __import__("os").read(descriptor, min(65536, len(raw) - len(chunks)))
                    if not part:
                        raise InputError("v3_service_scratch_short_read")
                    chunks.extend(part)
                if bytes(chunks) != raw or __import__("os").read(descriptor, 1):
                    raise InputError("v3_service_scratch_readback_conflict")
            finally:
                __import__("os").close(descriptor)
        finally:
            __import__("os").close(root)
        self.check()
        return path

    def _read_scratch(self, request_id, maximum=16777216):
        return self._read_evidence("verification/.scratch/" + request_id + ".full.json", maximum)

    def _read_evidence(self, path, maximum):
        return strict_json(self._read_evidence_bytes(path, maximum).decode("utf-8"))

    def _read_evidence_bytes(self, path, maximum):
        import os
        root = open_root(str(self.artifact_root))
        try:
            fd = open_relative(root, path)
            try:
                data = bytearray()
                while True:
                    self.check()
                    chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise InputError("v3_recovery_evidence_budget")
                return bytes(data)
            finally:
                os.close(fd)
        finally:
            os.close(root)

    def _admit_live(self, arguments):
        from .preflight_v3 import INSTANCE
        source = arguments.get("recover_from_request_id")
        if source is None:
            self.persist_scratch(INSTANCE + ".live-admission", {
                "request_id": arguments["request_id"], "automatic_replay": False})
            return
        original = self._read_scratch(INSTANCE + ".live-admission", maximum=4096)
        if (type(original) is not dict or original.get("request_id") != source
                or original.get("automatic_replay") is not False):
            raise InputError("v3_recovery_original_admission_conflict")
        if arguments["request_id"] == INSTANCE + SYNTAX_RECOVERY_SUFFIX:
            self._admit_syntax(arguments, source)
            return
        if arguments["request_id"] == INSTANCE + CANDIDATE_RECOVERY_SUFFIX:
            self._admit_candidate(arguments, source)
            return
        if arguments["request_id"] == INSTANCE + COVERAGE_RECOVERY_SUFFIX:
            self._admit_coverage(arguments, source)
            return
        if arguments["request_id"] == INSTANCE + MINIMAL_RECOVERY_SUFFIX:
            self._admit_minimal(arguments, source)
            return
        if arguments["request_id"] == INSTANCE + "-live-extended-20261101T233105Z":
            self._admit_extended(arguments, source)
            return
        if arguments["request_id"] == INSTANCE + "-live-diagnostic-20261101T133705Z":
            previous_id = INSTANCE + "-live-recovery-20261101"
            previous = self._read_scratch(INSTANCE + ".live-recovery-20261101", maximum=4096)
            from .preflight_v3 import PARENT
            receipt = self._read_evidence(
                PARENT + "/phase5-live-recovery-20261101.receipt.json", 16384)
            if (type(previous) is not dict or previous.get("request_id") != previous_id
                    or previous.get("recover_from_request_id") != source
                    or previous.get("automatic_replay") is not False
                    or type(previous.get("max_retries")) is not int or previous["max_retries"] != 3
                    or type(receipt) is not dict or receipt.get("request_id") != previous_id
                    or receipt.get("execution_instance") != INSTANCE
                    or receipt.get("status") != "blocked"
                    or type(receipt.get("service_exit_code")) is not int
                    or receipt["service_exit_code"] != 2
                    or receipt.get("service_cleanup") != "confirmed_nonzero_exit"):
                raise InputError("v3_diagnostic_previous_recovery_conflict")
            self.persist_scratch(INSTANCE + ".live-diagnostic-20261101T133705Z", {
                "request_id": arguments["request_id"], "recover_from_request_id": source,
                "previous_recovery_request_id": previous_id,
                "automatic_replay": False, "max_retries": 3,
                "authorization": "2026-11-01T13:37:05Z explicit one diagnostic recovery window"})
            return
        self.persist_scratch(INSTANCE + ".live-recovery-20261101", {
            "request_id": arguments["request_id"], "recover_from_request_id": source,
            "automatic_replay": False, "max_retries": 3,
            "authorization": "2026-11-01 explicit user recovery; one shared absolute window"})

    def _admit_extended(self, arguments, source):
        """Verify the exited diagnostic and preserved assets before a new exclusive slot."""
        from .preflight_v3 import INSTANCE, PARENT
        previous_id = INSTANCE + "-live-recovery-20261101"
        diagnostic_id = INSTANCE + "-live-diagnostic-20261101T133705Z"
        prefix = PARENT + "/phase5-live-diagnostic-20261101T133705Z."
        original = self._read_scratch(source)
        previous = self._read_scratch(INSTANCE + ".live-recovery-20261101", 4096)
        first_path = PARENT + "/phase5-live-recovery-20261101.receipt.json"
        first = self._read_evidence(first_path, 16384)
        diagnostic = self._read_scratch(INSTANCE + ".live-diagnostic-20261101T133705Z", 4096)
        receipt_raw = self._read_evidence_bytes(prefix + "receipt.json", 16384)
        receipt = strict_json(receipt_raw.decode("utf-8"))
        audit_raw = self._read_evidence_bytes(
            PARENT + "/phase5-diagnostic-postmortem-20261101T135839Z.receipt.json", 16384)
        audit = strict_json(audit_raw.decode("utf-8"))
        if (type(original) is not dict or original.get("request_id") != source
                or original.get("failures") != [{"kind": "blocked", "reason": "upstream_stream_error"}]
                or type(previous) is not dict or previous.get("request_id") != previous_id
                or previous.get("recover_from_request_id") != source
                or previous.get("automatic_replay") is not False
                or type(previous.get("max_retries")) is not int or previous["max_retries"] != 3
                or type(first) is not dict or first.get("request_id") != previous_id
                or first.get("execution_instance") != INSTANCE or first.get("status") != "blocked"
                or type(first.get("service_exit_code")) is not int or first["service_exit_code"] != 2
                or first.get("service_cleanup") != "confirmed_nonzero_exit"
                or type(diagnostic) is not dict or diagnostic.get("request_id") != diagnostic_id
                or diagnostic.get("recover_from_request_id") != source
                or diagnostic.get("previous_recovery_request_id") != previous_id
                or diagnostic.get("automatic_replay") is not False
                or type(diagnostic.get("max_retries")) is not int or diagnostic["max_retries"] != 3
                or type(receipt) is not dict or receipt.get("request_id") != diagnostic_id
                or receipt.get("execution_instance") != INSTANCE or receipt.get("status") != "blocked"
                or type(receipt.get("service_exit_code")) is not int or receipt["service_exit_code"] != 0
                or receipt.get("service_cleanup") != "confirmed_natural_exit"
                or type(receipt.get("business_call_attempts")) is not int
                or receipt["business_call_attempts"] != 1
                or type(audit) is not dict or audit.get("execution_instance") != INSTANCE
                or audit.get("online_acceptance") != "blocked"):
            raise InputError("v3_extended_previous_window_conflict")
        actual = audit.get("online_result")
        if (type(actual) is not dict or type(actual.get("actual_exit_code")) is not int
                or actual["actual_exit_code"] != 2
                or type(actual.get("service_exit_code")) is not int or actual["service_exit_code"] != 0
                or actual.get("service_cleanup") != "confirmed_natural_exit"
                or actual.get("original_receipt") != prefix + "receipt.json"
                or type(actual.get("original_receipt_bytes")) is not int
                or actual["original_receipt_bytes"] != len(receipt_raw)
                or actual.get("original_receipt_sha256") != hashlib.sha256(receipt_raw).hexdigest()):
            raise InputError("v3_extended_exit_evidence_conflict")
        binding = receipt.get("binding")
        old_paths = {
            "original_admission_sha256": "verification/.scratch/" + INSTANCE + ".live-admission.full.json",
            "original_trace_sha256": "verification/.scratch/" + source + ".full.json",
            "previous_marker_sha256": "verification/.scratch/" + INSTANCE + ".live-recovery-20261101.full.json",
            "previous_receipt_sha256": first_path}
        if type(binding) is not dict:
            raise InputError("v3_extended_preservation_binding_missing")
        for key, path in old_paths.items():
            if binding.get(key) != hashlib.sha256(self._read_evidence_bytes(path, 16777216)).hexdigest():
                raise InputError("v3_extended_previous_bytes_conflict")
        assets = receipt.get("assets")
        if type(assets) is not dict or not assets or len(assets) > 32:
            raise InputError("v3_extended_saved_assets_missing")
        total = 0
        for asset in assets.values():
            if (type(asset) is not dict or type(asset.get("path")) is not str
                    or not asset["path"].startswith(prefix)
                    or relative_path(asset["path"]) != asset["path"]
                    or type(asset.get("bytes")) is not int or not 0 <= asset["bytes"] <= 16777216
                    or asset.get("exact_readback") is not True):
                raise InputError("v3_extended_saved_asset_contract")
            total += asset["bytes"]
            if total > 33554432:
                raise InputError("v3_extended_saved_assets_budget")
            raw = self._read_evidence_bytes(asset["path"], asset["bytes"])
            if len(raw) != asset["bytes"] or hashlib.sha256(raw).hexdigest() != asset.get("sha256"):
                raise InputError("v3_extended_saved_asset_changed")
        self.persist_scratch(INSTANCE + ".live-extended-20261101T233105Z", {
            "request_id": arguments["request_id"], "recover_from_request_id": source,
            "previous_recovery_request_id": previous_id,
            "previous_diagnostic_request_id": diagnostic_id,
            "previous_diagnostic_receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "previous_postmortem_sha256": hashlib.sha256(audit_raw).hexdigest(),
            "automatic_replay": False, "max_retries": 3, "process_seconds": LIVE_PROCESS_SECONDS,
            "authorization": "2026-11-01 explicit one new recovery; 600 second shared ceiling"})

    def _admit_minimal(self, arguments, source, *, persist=True):
        """Authorize one new linked slot only after authenticating the exited window."""
        from .preflight_v3 import INSTANCE, PARENT
        extended_id = INSTANCE + "-live-extended-20261101T233105Z"
        prefix = PARENT + "/phase5-live-extended-20261101T233105Z."
        original = self._read_scratch(source)
        marker_path = "verification/.scratch/" + INSTANCE + ".live-extended-20261101T233105Z.full.json"
        marker_raw = self._read_evidence_bytes(marker_path, 4096)
        marker = strict_json(marker_raw.decode("utf-8"))
        receipt_raw = self._read_evidence_bytes(prefix + "receipt.json", 32768)
        receipt = strict_json(receipt_raw.decode("utf-8"))
        audit_raw = self._read_evidence_bytes(
            PARENT + "/phase5-extended-postmortem-20261101T235855Z.receipt.json", 16384)
        audit = strict_json(audit_raw.decode("utf-8"))
        if (type(original) is not dict or original.get("request_id") != source
                or original.get("failures") != [{"kind": "blocked", "reason": "upstream_stream_error"}]
                or type(marker) is not dict or marker.get("request_id") != extended_id
                or marker.get("recover_from_request_id") != source
                or marker.get("previous_recovery_request_id") != INSTANCE + "-live-recovery-20261101"
                or marker.get("previous_diagnostic_request_id") != INSTANCE + "-live-diagnostic-20261101T133705Z"
                or marker.get("automatic_replay") is not False
                or type(marker.get("max_retries")) is not int or marker["max_retries"] != 3
                or type(marker.get("process_seconds")) is not int or marker["process_seconds"] != 600
                or type(receipt) is not dict or receipt.get("request_id") != extended_id
                or receipt.get("execution_instance") != INSTANCE or receipt.get("status") != "blocked"
                or type(receipt.get("service_exit_code")) is not int or receipt["service_exit_code"] != 0
                or receipt.get("service_cleanup") != "confirmed_natural_exit"
                or type(receipt.get("business_call_attempts")) is not int or receipt["business_call_attempts"] != 1
                or type(audit) is not dict or audit.get("execution_instance") != INSTANCE
                or audit.get("online_acceptance") != "blocked"):
            raise InputError("v3_minimal_previous_window_conflict")
        actual = audit.get("online_result")
        if (type(actual) is not dict or type(actual.get("actual_exit_code")) is not int
                or actual["actual_exit_code"] != 2
                or type(actual.get("service_exit_code")) is not int or actual["service_exit_code"] != 0
                or actual.get("service_cleanup") != "confirmed_natural_exit"
                or actual.get("original_receipt") != prefix + "receipt.json"
                or type(actual.get("original_receipt_bytes")) is not int
                or actual["original_receipt_bytes"] != len(receipt_raw)
                or actual.get("original_receipt_sha256") != hashlib.sha256(receipt_raw).hexdigest()):
            raise InputError("v3_minimal_exit_evidence_conflict")
        binding = receipt.get("binding")
        old_paths = {
            "original_admission_sha256": "verification/.scratch/" + INSTANCE + ".live-admission.full.json",
            "original_trace_sha256": "verification/.scratch/" + source + ".full.json",
            "previous_marker_sha256": "verification/.scratch/" + INSTANCE + ".live-recovery-20261101.full.json",
            "previous_receipt_sha256": PARENT + "/phase5-live-recovery-20261101.receipt.json",
            "previous_diagnostic_marker_sha256": "verification/.scratch/" + INSTANCE + ".live-diagnostic-20261101T133705Z.full.json",
            "previous_diagnostic_receipt_sha256": PARENT + "/phase5-live-diagnostic-20261101T133705Z.receipt.json",
            "previous_postmortem_sha256": PARENT + "/phase5-diagnostic-postmortem-20261101T135839Z.receipt.json"}
        if type(binding) is not dict:
            raise InputError("v3_minimal_preservation_binding_missing")
        for key, path in old_paths.items():
            if binding.get(key) != hashlib.sha256(self._read_evidence_bytes(path, 16777216)).hexdigest():
                raise InputError("v3_minimal_previous_bytes_conflict")
        for assets, asset_prefix in ((receipt.get("assets"), prefix), (
                binding.get("previous_diagnostic_assets"),
                PARENT + "/phase5-live-diagnostic-20261101T133705Z.")):
            if type(assets) is not dict or not 0 < len(assets) <= 32:
                raise InputError("v3_minimal_saved_assets_missing")
            total = 0
            for asset in assets.values():
                if (type(asset) is not dict or type(asset.get("path")) is not str
                        or not asset["path"].startswith(asset_prefix)
                        or relative_path(asset["path"]) != asset["path"]
                        or type(asset.get("bytes")) is not int or not 0 <= asset["bytes"] <= 16777216
                        or asset.get("exact_readback") is not True):
                    raise InputError("v3_minimal_saved_asset_contract")
                total += asset["bytes"]
                if total > 33554432:
                    raise InputError("v3_minimal_saved_assets_budget")
                raw = self._read_evidence_bytes(asset["path"], asset["bytes"])
                if len(raw) != asset["bytes"] or hashlib.sha256(raw).hexdigest() != asset.get("sha256"):
                    raise InputError("v3_minimal_saved_asset_changed")
        admission_marker = {
            "request_id": arguments["request_id"], "recover_from_request_id": source,
            "previous_extended_request_id": extended_id,
            "previous_extended_marker_sha256": hashlib.sha256(marker_raw).hexdigest(),
            "previous_extended_receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "previous_extended_postmortem_sha256": hashlib.sha256(audit_raw).hexdigest(),
            "automatic_replay": False, "max_retries": 3, "process_seconds": 600,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "retryable_reasons": ["upstream_stream_error", "upstream_timeout"],
            "authorization": "2026-11-02 explicit one single-gateway minimal recovery"}
        if persist:
            self.persist_scratch(INSTANCE + ".live-minimal-20261102T020451Z", admission_marker)
        return admission_marker

    def _admit_coverage(self, arguments, source):
        """Authenticate the exited minimal window without inheriting semantic acceptance."""
        from .preflight_v3 import INSTANCE
        if arguments.get("selection_version") != COVERAGE_VERSION:
            raise InputError("v3_coverage_explicit_selection_version_required")
        marker = self._coverage_admission_marker(source)
        marker["request_id"] = arguments["request_id"]
        self.persist_scratch(INSTANCE + ".live-coverage-20261102T050022Z", marker)

    def _coverage_admission_marker(self, source):
        """Read-only verification shared by the two explicitly authorized admissions."""
        from .preflight_v3 import INSTANCE, PARENT
        minimal_id = INSTANCE + MINIMAL_RECOVERY_SUFFIX
        marker_path = "verification/.scratch/" + INSTANCE + ".live-minimal-20261102T020451Z.full.json"
        prefix = PARENT + "/phase5-live-minimal-20261102T020451Z."
        acceptance_prefix = PARENT + "/phase5-minimal-acceptance-20261102T020451Z."
        marker_raw = self._read_evidence_bytes(marker_path, 4096)
        marker = strict_json(marker_raw.decode("utf-8"))
        receipt_raw = self._read_evidence_bytes(prefix + "receipt.json", 65536)
        receipt = strict_json(receipt_raw.decode("utf-8"))
        consume_raw = self._read_evidence_bytes(acceptance_prefix + "consume.receipt.json", 65536)
        consumed = strict_json(consume_raw.decode("utf-8"))
        reconcile_raw = self._read_evidence_bytes(acceptance_prefix + "reconciliation.receipt.json", 65536)
        reconciled = strict_json(reconcile_raw.decode("utf-8"))
        if (type(marker) is not dict or marker.get("request_id") != minimal_id
                or marker.get("recover_from_request_id") != source
                or marker.get("previous_extended_request_id") != INSTANCE + "-live-extended-20261101T233105Z"
                or marker.get("automatic_replay") is not False
                or type(marker.get("max_retries")) is not int or marker["max_retries"] != 3
                or type(marker.get("process_seconds")) is not int or marker["process_seconds"] != 600
                or marker.get("generation_controls") != {"temperature": 0.2, "max_tokens": 3072}
                or marker.get("retryable_reasons") != ["upstream_stream_error", "upstream_timeout"]
                or type(receipt) is not dict or receipt.get("request_id") != minimal_id
                or receipt.get("execution_instance") != INSTANCE
                or receipt.get("status") != "online_transport_saved_trusted_consumption_pending"
                or receipt.get("failures") != []
                or type(receipt.get("service_exit_code")) is not int or receipt["service_exit_code"] != 0
                or receipt.get("service_cleanup") != "confirmed_natural_exit"
                or type(receipt.get("business_call_attempts")) is not int or receipt["business_call_attempts"] != 1):
            raise InputError("v3_coverage_previous_window_conflict")
        bound = receipt.get("binding")
        if (type(bound) is not dict or type(consumed) is not dict or type(reconciled) is not dict
                or consumed.get("execution_instance") != INSTANCE or consumed.get("request_id") != minimal_id
                or consumed.get("status") != "dual_consumption_pass_semantics_pending"
                or consumed.get("production_and_independent_publication_equal") is not True
                or consumed.get("task_status") != "complete"
                or consumed.get("live_receipt_sha256") != hashlib.sha256(receipt_raw).hexdigest()
                or consumed.get("binding") != bound or reconciled.get("binding") != bound
                or reconciled.get("execution_instance") != INSTANCE or reconciled.get("request_id") != minimal_id
                or reconciled.get("status") != "blocked_required_anchor_body_missing"
                or type(reconciled.get("missing_anchor_ids")) is not list or not reconciled["missing_anchor_ids"]
                or reconciled.get("human_semantic_review") != "not_proven"):
            raise InputError("v3_coverage_previous_acceptance_conflict")
        prior_paths = {
            "original_admission_sha256": "verification/.scratch/" + INSTANCE + ".live-admission.full.json",
            "original_trace_sha256": "verification/.scratch/" + source + ".full.json",
            "previous_marker_sha256": "verification/.scratch/" + INSTANCE + ".live-recovery-20261101.full.json",
            "previous_receipt_sha256": PARENT + "/phase5-live-recovery-20261101.receipt.json",
            "previous_diagnostic_marker_sha256": "verification/.scratch/" + INSTANCE + ".live-diagnostic-20261101T133705Z.full.json",
            "previous_diagnostic_receipt_sha256": PARENT + "/phase5-live-diagnostic-20261101T133705Z.receipt.json",
            "previous_postmortem_sha256": PARENT + "/phase5-diagnostic-postmortem-20261101T135839Z.receipt.json",
            "previous_extended_marker_sha256": "verification/.scratch/" + INSTANCE + ".live-extended-20261101T233105Z.full.json",
            "previous_extended_receipt_sha256": PARENT + "/phase5-live-extended-20261101T233105Z.receipt.json",
            "previous_extended_postmortem_sha256": PARENT + "/phase5-extended-postmortem-20261101T235855Z.receipt.json"}
        for key, path in prior_paths.items():
            if key in bound or key.startswith("original_") or key.startswith("previous_extended_"):
                digest = hashlib.sha256(self._read_evidence_bytes(path, 16777216)).hexdigest()
                if bound.get(key) != digest or (key.startswith("previous_extended_") and marker.get(key) != digest):
                    raise InputError("v3_coverage_previous_bytes_conflict")
        for assets, allowed_prefix in ((receipt.get("assets"), prefix),
                (consumed.get("assets"), acceptance_prefix),
                ({"review": reconciled.get("review_inputs")}, acceptance_prefix)):
            if type(assets) is not dict or not 0 < len(assets) <= 32:
                raise InputError("v3_coverage_saved_assets_missing")
            total = 0
            for asset in assets.values():
                if (type(asset) is not dict or type(asset.get("path")) is not str
                        or not asset["path"].startswith(allowed_prefix)
                        or relative_path(asset["path"]) != asset["path"]
                        or type(asset.get("bytes")) is not int or not 0 <= asset["bytes"] <= 16777216
                        or asset.get("exact_readback") is not True):
                    raise InputError("v3_coverage_saved_asset_contract")
                total += asset["bytes"]
                if total > 33554432:
                    raise InputError("v3_coverage_saved_assets_budget")
                raw = self._read_evidence_bytes(asset["path"], asset["bytes"])
                if len(raw) != asset["bytes"] or hashlib.sha256(raw).hexdigest() != asset.get("sha256"):
                    raise InputError("v3_coverage_saved_asset_changed")
        return {
            "request_id": INSTANCE + COVERAGE_RECOVERY_SUFFIX, "recover_from_request_id": source,
            "previous_minimal_request_id": minimal_id,
            "previous_minimal_marker_sha256": hashlib.sha256(marker_raw).hexdigest(),
            "previous_minimal_receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "previous_consumption_receipt_sha256": hashlib.sha256(consume_raw).hexdigest(),
            "previous_reconciliation_receipt_sha256": hashlib.sha256(reconcile_raw).hexdigest(),
            "selection_version": COVERAGE_VERSION, "automatic_replay": False,
            "max_retries": 3, "process_seconds": 600,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "retryable_reasons": ["upstream_stream_error", "upstream_timeout"],
            "authorization": "2026-11-02 explicit one new-selection coverage recovery"}

    def _admit_candidate(self, arguments, source, *, persist=True):
        """核验覆盖窗口；默认独占接纳，显式只读复核不创建或复活旧标记。"""
        from .preflight_v3 import INSTANCE, PARENT
        if arguments.get("selection_version") != COVERAGE_VERSION:
            raise InputError("v3_candidate_explicit_selection_version_required")
        expected_marker = self._coverage_admission_marker(source)
        paths = {
            "previous_coverage_marker_sha256": "verification/.scratch/" + INSTANCE
                + ".live-coverage-20261102T050022Z.full.json",
            "previous_coverage_receipt_sha256": PARENT
                + "/phase5-live-coverage-20261102T050022Z.receipt.json",
            "previous_coverage_diagnostic_sha256": PARENT
                + "/phase5-coverage-failure-diagnostic-20261102T064856Z.receipt.json",
            "previous_coverage_ready_sha256": PARENT
                + "/phase5-coverage-ready-20261102T050022Z.json"}
        raws = {key: self._read_evidence_bytes(path, 131072) for key, path in paths.items()}
        marker, receipt, diagnostic, ready = (
            strict_json(raw.decode("utf-8")) for raw in raws.values())
        if (type(marker) is not dict
                or any(type(marker.get(key)) is not type(value) or marker[key] != value
                       for key, value in expected_marker.items() if key != "authorization")
                or type(receipt) is not dict or receipt.get("status") != "blocked"
                or receipt.get("request_id") != INSTANCE + COVERAGE_RECOVERY_SUFFIX
                or receipt.get("execution_instance") != INSTANCE
                or type(receipt.get("service_exit_code")) is not int or receipt["service_exit_code"] != 2
                or receipt.get("service_cleanup") != "confirmed_nonzero_exit"
                or type(receipt.get("business_call_attempts")) is not int
                or receipt["business_call_attempts"] != 1
                or type(ready) is not dict or ready.get("status") != "ready_not_online_executed"
                or ready.get("selection_version") != COVERAGE_VERSION
                or type(ready.get("binding")) is not dict or receipt.get("binding") != ready["binding"]
                or receipt.get("listener_after") != ready.get("listener_inodes")):
            raise InputError("v3_candidate_previous_window_conflict")
        for key, value in expected_marker.items():
            if key.endswith("_sha256") and ready["binding"].get(key) != value:
                raise InputError("v3_candidate_previous_binding_conflict")
        if (type(diagnostic) is not dict
                or diagnostic.get("status") != "failed_window_diagnosed_not_business_acceptance"
                or diagnostic.get("execution_instance") != INSTANCE
                or diagnostic.get("request_id") != receipt["request_id"]
                or any(type(diagnostic.get(key)) is not int or diagnostic[key] != value
                       for key, value in (("actual_online_outer_exit_code", 2),
                                          ("service_exit_code", 2), ("actual_diagnostic_exit_code", 0)))
                or diagnostic.get("service_cleanup") != "confirmed_nonzero_exit"
                or diagnostic.get("online_receipt") != paths["previous_coverage_receipt_sha256"]
                or diagnostic.get("online_receipt_sha256")
                    != hashlib.sha256(raws["previous_coverage_receipt_sha256"]).hexdigest()
                or diagnostic.get("failure_reason") != "v3_task_invalid_or_unsent_conclusion"
                or any(type(diagnostic.get(key)) is not str or not diagnostic[key].strip()
                       for key in ("online_tool_result", "diagnostic_tool_result"))
                or diagnostic.get("listener_unchanged") is not True
                or diagnostic.get("delivery_saved") is not False or diagnostic.get("stdio_saved") is not False):
            raise InputError("v3_candidate_previous_exit_evidence_conflict")
        assets = receipt.get("assets")
        prefix = PARENT + "/phase5-live-coverage-20261102T050022Z."
        if (type(assets) is not dict or not 0 < len(assets) <= 32
                or not {"request", "admission", "failure", "transport_attempt_1"} <= set(assets)
                or "delivery" in assets or "stdio" in assets):
            raise InputError("v3_candidate_previous_assets_missing")
        total = 0
        for asset in assets.values():
            if (type(asset) is not dict or type(asset.get("path")) is not str
                    or not asset["path"].startswith(prefix)
                    or relative_path(asset["path"]) != asset["path"]
                    or type(asset.get("bytes")) is not int or not 0 <= asset["bytes"] <= 16777216
                    or asset.get("exact_readback") is not True):
                raise InputError("v3_candidate_previous_asset_contract")
            total += asset["bytes"]
            if total > 33554432:
                raise InputError("v3_candidate_previous_assets_budget")
            raw = self._read_evidence_bytes(asset["path"], asset["bytes"])
            if len(raw) != asset["bytes"] or hashlib.sha256(raw).hexdigest() != asset.get("sha256"):
                raise InputError("v3_candidate_previous_asset_changed")
        if (any(type(diagnostic.get(key)) is not int or diagnostic[key] != value
                for key, value in (("saved_assets_verified", len(assets)), ("saved_asset_bytes", total)))
                or self._read_evidence_bytes(assets["admission"]["path"], 4096)
                    != raws["previous_coverage_marker_sha256"]):
            raise InputError("v3_candidate_previous_asset_binding_conflict")
        frozen = ready.get("wire_arguments")
        if (type(frozen) is not dict or frozen.get("request_id") != receipt["request_id"]
                or any(arguments.get(key) != frozen.get(key) for key in (
                    "contract_version", "protocol_version", "operation", "project_id",
                    "recover_from_request_id", "selection_version", "scenario", "question",
                    "dimensions", "include", "return_bytes", "transport", "wire_encoding"))
                or resolve_selection(arguments, tick=self.check)
                    != resolve_selection(frozen, tick=self.check)):
            raise InputError("v3_candidate_frozen_inputs_conflict")
        admission_marker = {
            "request_id": arguments["request_id"], "recover_from_request_id": source,
            "previous_coverage_request_id": receipt["request_id"],
            **{key: hashlib.sha256(raw).hexdigest() for key, raw in raws.items()},
            "selection_version": COVERAGE_VERSION, "automatic_replay": False,
            "max_retries": 3, "max_total_retries": 3, "process_seconds": 600,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "retryable_reasons": ["upstream_stream_error", "upstream_timeout"],
            "candidate_retryable_reason": InvalidSourceCandidate.reason,
            "authorization": "2026-11-02T07:01:53Z explicit one bounded candidate recovery"}
        if persist:
            self.persist_scratch(INSTANCE + ".live-candidate-20261102T070153Z", admission_marker)
        return admission_marker

    def _admit_syntax(self, arguments, source):
        """核验已退出候选窗口及原资产，仅消耗一个新的独占语法恢复槽。"""
        from .preflight_v3 import INSTANCE, PARENT
        previous_id = INSTANCE + CANDIDATE_RECOVERY_SUFFIX
        self._admit_minimal(
            {**arguments, "request_id": INSTANCE + MINIMAL_RECOVERY_SUFFIX}, source, persist=False)
        expected_marker = self._admit_candidate(
            {**arguments, "request_id": previous_id}, source, persist=False)
        prefix = PARENT + "/phase5-live-candidate-20261102T070153Z."
        paths = {
            "previous_candidate_marker_sha256": "verification/.scratch/" + INSTANCE
                + ".live-candidate-20261102T070153Z.full.json",
            "previous_candidate_receipt_sha256": prefix + "receipt.json",
            "previous_candidate_diagnostic_sha256": PARENT
                + "/phase5-candidate-failure-diagnostic-20261102T080521Z.receipt.json",
            "previous_candidate_ready_sha256": PARENT
                + "/phase5-candidate-ready-20261102T070153Z.json"}
        raws = {key: self._read_evidence_bytes(path, 131072) for key, path in paths.items()}
        marker, receipt, diagnostic, ready = (
            strict_json(raw.decode("utf-8")) for raw in raws.values())
        if (type(marker) is not dict or marker != expected_marker
                or type(receipt) is not dict or receipt.get("status") != "blocked"
                or receipt.get("execution_instance") != INSTANCE
                or receipt.get("request_id") != previous_id
                or type(receipt.get("service_exit_code")) is not int
                or receipt["service_exit_code"] != 2
                or receipt.get("service_cleanup") != "confirmed_nonzero_exit"
                or type(receipt.get("business_call_attempts")) is not int
                or receipt["business_call_attempts"] != 1
                or type(ready) is not dict or ready.get("status") != "ready_not_online_executed"
                or ready.get("selection_version") != COVERAGE_VERSION
                or type(ready.get("binding")) is not dict
                or receipt.get("binding") != ready["binding"]
                or receipt.get("listener_after") != ready.get("listener_inodes")
                or any(ready["binding"].get(key) != value for key, value in marker.items()
                       if key.endswith("_sha256"))):
            raise InputError("v3_syntax_previous_window_conflict")
        if (type(diagnostic) is not dict
                or diagnostic.get("status") != "failed_window_diagnosed_not_business_acceptance"
                or diagnostic.get("execution_instance") != INSTANCE
                or diagnostic.get("request_id") != previous_id
                or any(type(diagnostic.get(key)) is not int or diagnostic[key] != value
                       for key, value in (("actual_online_outer_exit_code", 2),
                                          ("service_exit_code", 2), ("actual_diagnostic_exit_code", 0)))
                or diagnostic.get("service_cleanup") != "confirmed_nonzero_exit"
                or diagnostic.get("online_receipt") != paths["previous_candidate_receipt_sha256"]
                or diagnostic.get("online_receipt_sha256")
                    != hashlib.sha256(raws["previous_candidate_receipt_sha256"]).hexdigest()
                or diagnostic.get("failure_error_type") != "JSONDecodeError"
                or any(type(diagnostic.get(key)) is not str or not diagnostic[key].strip()
                       for key in ("online_tool_result", "diagnostic_tool_result"))
                or diagnostic.get("listener_unchanged") is not True
                or diagnostic.get("delivery_saved") is not False
                or diagnostic.get("stdio_saved") is not False):
            raise InputError("v3_syntax_previous_exit_evidence_conflict")
        assets = receipt.get("assets")
        if (type(assets) is not dict or not 0 < len(assets) <= 32
                or not {"request", "admission", "failure", "transport_attempt_1",
                        "transport_attempt_2"} <= set(assets)
                or {"delivery", "stdio"} & set(assets)):
            raise InputError("v3_syntax_previous_assets_missing")
        total = 0
        for asset in assets.values():
            if (type(asset) is not dict or type(asset.get("path")) is not str
                    or not asset["path"].startswith(prefix)
                    or relative_path(asset["path"]) != asset["path"]
                    or type(asset.get("bytes")) is not int or not 0 <= asset["bytes"] <= 16777216
                    or asset.get("exact_readback") is not True):
                raise InputError("v3_syntax_previous_asset_contract")
            total += asset["bytes"]
            if total > 33554432:
                raise InputError("v3_syntax_previous_assets_budget")
            raw = self._read_evidence_bytes(asset["path"], asset["bytes"])
            if len(raw) != asset["bytes"] or hashlib.sha256(raw).hexdigest() != asset.get("sha256"):
                raise InputError("v3_syntax_previous_asset_changed")
        if (any(type(diagnostic.get(key)) is not int or diagnostic[key] != value
                for key, value in (("saved_assets_verified", len(assets)), ("saved_asset_bytes", total)))
                or self._read_evidence_bytes(assets["admission"]["path"], 4096)
                    != raws["previous_candidate_marker_sha256"]):
            raise InputError("v3_syntax_previous_asset_binding_conflict")
        frozen = ready.get("wire_arguments")
        if (type(frozen) is not dict or frozen.get("request_id") != previous_id
                or any(arguments.get(key) != frozen.get(key) for key in (
                    "contract_version", "protocol_version", "operation", "project_id",
                    "recover_from_request_id", "selection_version", "scenario", "question",
                    "dimensions", "include", "return_bytes", "transport", "wire_encoding"))
                or resolve_selection(arguments, tick=self.check)
                    != resolve_selection(frozen, tick=self.check)):
            raise InputError("v3_syntax_frozen_inputs_conflict")
        self.persist_scratch(INSTANCE + ".live-syntax-20261102T085352Z", {
            "request_id": arguments["request_id"], "recover_from_request_id": source,
            "previous_candidate_request_id": previous_id,
            **{key: hashlib.sha256(raw).hexdigest() for key, raw in raws.items()},
            "selection_version": COVERAGE_VERSION, "automatic_replay": False,
            "max_retries": 3, "max_total_retries": 3, "process_seconds": 600,
            "soft_target_seconds": 180, "return_reserve_seconds": 15,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "retryable_reasons": ["upstream_stream_error", "upstream_timeout"],
            "candidate_retryable_reasons": [InvalidSourceCandidate.reason, InvalidCandidateSyntax.reason],
            "authorization": "2026-11-02 explicit one bounded syntax recovery"})

    def _save_gateway_attempt(self, arguments, prepared, observation, output=None):
        """Preserve each transport before candidate parsing or publication can fail."""
        number = observation["attempt_index"]
        self.persist_scratch(arguments["request_id"] + ".gateway-attempt-" + str(number), {
            "contract_version": 3, "request_id": arguments["request_id"],
            "recover_from_request_id": arguments.get("recover_from_request_id"),
            "status": "transport_failed_not_business_acceptance" if output is None
                      else "transport_completed_not_business_acceptance",
            "post_utf8": prepared.post.decode("utf-8"),
            "post_sha256": hashlib.sha256(prepared.post).hexdigest(),
            "observation": observation, "output": output})

    def validate_v3(self, arguments, rpc_id):
        try:
            if (type(arguments) is not dict
                    or set(arguments) - set(INPUT_SCHEMA["properties"])
                    or not set(INPUT_SCHEMA["required"]) <= set(arguments)
                    or type(arguments["contract_version"]) is not int
                    or arguments["contract_version"] != 3):
                raise ValueError("v3_service_fields_or_version")
            identity = arguments["request_id"]
            if (type(identity) is not str
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identity) is None):
                raise ValueError("v3_service_request_identity")
            key = arguments["project_id"]
            if (type(key) is not str or key not in self.projects
                    or key not in self.policy["authorized_project_ids"]):
                raise ValueError("v3_service_project_authorization")
            project = self.projects[key]
            include = arguments.get("include", list(project.include))
            if (type(include) is not list or not 1 <= len(include) <= 32
                    or any(type(item) is not str or relative_path(item) != item for item in include)
                    or len(set(include)) != len(include)
                    or any(not any(within(item, allowed) for allowed in project.include) for item in include)):
                raise ValueError("v3_service_authorized_scope")
            _question_identity(arguments["question"], arguments["dimensions"])
            selection_version = arguments.get("selection_version", VERSION)
            if (type(selection_version) is not str
                    or selection_version not in (VERSION, COVERAGE_VERSION)):
                raise ValueError("v3_service_unknown_selection_version")
            operation = arguments["operation"]
            if type(operation) is not str or operation not in {"preflight", "live"}:
                raise ValueError("v3_service_operation")
            selection_fields = [name for name in (
                "frozen_selection", "frozen_selection_lz", "compressed_frozen_selection")
                if name in arguments]
            if operation == "live":
                if (not self.online or len(selection_fields) != 1
                        or type(arguments[selection_fields[0]]) is not dict):
                    raise ValueError("v3_service_live_authorization_and_selection_required")
            elif any(name != "frozen_selection" for name in selection_fields):
                raise ValueError("v3_service_compressed_selection_requires_live")
            if "recover_from_request_id" in arguments:
                from .preflight_v3 import INSTANCE
                if (operation != "live"
                        or arguments["recover_from_request_id"] != INSTANCE + "-live-20261101"
                        or identity not in {INSTANCE + "-live-recovery-20261101",
                                            INSTANCE + "-live-diagnostic-20261101T133705Z",
                                            INSTANCE + "-live-extended-20261101T233105Z",
                                            INSTANCE + MINIMAL_RECOVERY_SUFFIX,
                                            INSTANCE + COVERAGE_RECOVERY_SUFFIX,
                                            INSTANCE + CANDIDATE_RECOVERY_SUFFIX,
                                            INSTANCE + SYNTAX_RECOVERY_SUFFIX}):
                    raise ValueError("v3_service_recovery_authorization")
                if (identity in {INSTANCE + COVERAGE_RECOVERY_SUFFIX,
                                 INSTANCE + CANDIDATE_RECOVERY_SUFFIX,
                                 INSTANCE + SYNTAX_RECOVERY_SUFFIX}
                        and arguments.get("selection_version") != COVERAGE_VERSION):
                    raise ValueError("v3_service_coverage_explicit_version_required")
            ceiling = 60 if operation == "preflight" else LIVE_PROCESS_SECONDS
            started, end, window = (arguments[key] for key in (
                "started_at_unix_ms", "deadline_unix_ms", "host_timeout_seconds"))
            if (any(type(value) is not int for value in (started, end, window))
                    or not 1 <= window <= ceiling or started <= 0
                    or not 0 < end - started <= window * 1000):
                raise ValueError("v3_service_host_window")
            now = time.time() * 1000
            if started > now or end <= now:
                raise ValueError("v3_service_expired_or_future_start")
            budget = arguments["return_bytes"]
            if type(budget) is not int or not 1 <= budget <= min(22016, project.return_bytes):
                raise ValueError("v3_service_return_budget")
            admission = Admission(arguments["protocol_version"], arguments["scenario"], rpc_id,
                                  budget, TransportCapabilities.from_dict(arguments["transport"]),
                                  wire_encoding=arguments.get("wire_encoding", "json"))
            # 预检内部耗时只观测；只传播调用方真实绝对窗口，不另造 27 秒截止。
            deadline_ms = min(end, started + window * 1000)
            deadline = time.monotonic() + (deadline_ms - now) / 1000
            if deadline <= time.monotonic():
                raise ValueError("v3_service_internal_deadline")
            return project, tuple(include), admission, deadline
        except (ValueError, TypeError, KeyError) as exception:
            raise InputError("V3 请求契约或授权无效") from exception

    @snapshot_scope
    def distill_frame(self, arguments, cancelled=None, *, rpc_id):
        service_started = time.monotonic()
        if self.used:
            raise InputError("v3_service_single_business_only")
        project, include, admission, deadline = self.validate_v3(arguments, rpc_id)
        self.cancelled = threading.Event() if cancelled is None else cancelled
        self.business_deadline = deadline
        self.check()
        try:
            # 先按固定浅层头部及预算解码，不先序列化未知压缩嵌套值。
            # 使用同一绝对窗口；无效表示不消耗独占业务接纳。
            frozen_selection = resolve_selection(arguments, tick=self.check)
        except (ValueError, TypeError, KeyError) as exception:
            raise InputError("V3 冻结选择入站表示无效") from exception
        arguments = json.loads(utf8_json(arguments))
        self.check()
        self.used = True
        if arguments["operation"] == "live":
            # One logical admission includes bounded transport retries. Explicit
            # recovery preserves the old marker and consumes a separate linked slot.
            self._admit_live(arguments)
        prepared = prepare_snapshot(project=project, include=include,
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=deadline, cancelled=self.cancelled,
            frozen_selection=frozen_selection,
            selection_version=arguments.get("selection_version", VERSION), tick=self.check)
        from .preflight_v3 import INSTANCE
        controlled_recovery = (arguments["operation"] == "live"
            and arguments.get("recover_from_request_id") == INSTANCE + "-live-20261101"
            and arguments["request_id"] in {INSTANCE + MINIMAL_RECOVERY_SUFFIX,
                                             INSTANCE + COVERAGE_RECOVERY_SUFFIX,
                                             INSTANCE + CANDIDATE_RECOVERY_SUFFIX,
                                             INSTANCE + SYNTAX_RECOVERY_SUFFIX})
        syntax_recovery = (controlled_recovery
            and arguments["request_id"] == INSTANCE + SYNTAX_RECOVERY_SUFFIX)
        candidate_recovery = (controlled_recovery
            and arguments["request_id"] in {INSTANCE + CANDIDATE_RECOVERY_SUFFIX,
                                             INSTANCE + SYNTAX_RECOVERY_SUFFIX})
        coverage_recovery = (controlled_recovery
            and arguments["request_id"] in {INSTANCE + COVERAGE_RECOVERY_SUFFIX,
                                             INSTANCE + CANDIDATE_RECOVERY_SUFFIX,
                                             INSTANCE + SYNTAX_RECOVERY_SUFFIX})
        legacy_post = prepared.post
        if controlled_recovery:
            from dataclasses import replace
            prepared = replace(prepared, post=encode_request(
                prepared.messages, generation_controls=True))
        raw_files, selection, report = prepared.raw_files, prepared.selection, prepared.report
        trusted = {"project_id": project.project_id, "snapshot_id": report["snapshot_id"],
            "required_core_unit_ids": selection["core_unit_ids"],
            "selection_id": selection["selection_id"], "request_id": arguments["request_id"],
            "question": arguments["question"], "dimensions": arguments["dimensions"],
            "selection_version": arguments.get("selection_version", VERSION),
            "post": prepared.post, "frozen_coverage": report["coverage"], "failures": []}
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        task = None
        gateway_requests, model_outputs = [], []
        if arguments["operation"] == "live":
            if (encode_request(prepared.messages, generation_controls=controlled_recovery)
                    != prepared.post or len(prepared.post) > 150000):
                raise InputError("v3_service_exact_post_conflict")
            prepared.verify_sources()
            source = arguments.get("recover_from_request_id")
            if source is not None:
                original = self._read_scratch(source)
                if (original.get("request_id") != source
                        or original.get("report", {}).get("snapshot_id") != report["snapshot_id"]
                        or original.get("failures") != [{"kind": "blocked", "reason": "upstream_stream_error"}]):
                    raise InputError("v3_recovery_original_business_conflict")
                if coverage_recovery:
                    original_post = strict_json(original.get("post_utf8", ""))
                    original_payload = strict_json(original_post["messages"][-1]["content"])
                    if (original_payload.get("question") != arguments["question"]
                            or original_payload.get("dimensions") != arguments["dimensions"]
                            or {k: v for k, v in original["report"].items() if k != "observations"}
                            != {k: v for k, v in report.items() if k != "observations"}):
                        raise InputError("v3_coverage_original_source_or_question_conflict")
                elif (original.get("post_utf8", "").encode("utf-8") != legacy_post
                        or original.get("selection") != selection):
                    raise InputError("v3_recovery_original_business_conflict")
            if candidate_recovery:
                from .preflight_v3 import PARENT
                ready_name = ("phase5-candidate-ready-20261102T070153Z.json" if syntax_recovery
                              else "phase5-coverage-ready-20261102T050022Z.json")
                marker_name = (".live-syntax-20261102T085352Z" if syntax_recovery
                               else ".live-candidate-20261102T070153Z")
                ready_binding_key = ("previous_candidate_ready_sha256" if syntax_recovery
                                     else "previous_coverage_ready_sha256")
                previous_ready_raw = self._read_evidence_bytes(PARENT + "/" + ready_name, 131072)
                previous_ready = strict_json(previous_ready_raw.decode("utf-8"))
                marker = self._read_scratch(INSTANCE + marker_name, 4096)
                if (marker.get(ready_binding_key)
                        != hashlib.sha256(previous_ready_raw).hexdigest()
                        or previous_ready.get("post_sha256") != hashlib.sha256(prepared.post).hexdigest()
                        or previous_ready.get("post_bytes") != len(prepared.post)
                        or previous_ready.get("snapshot_id") != report["snapshot_id"]
                        or previous_ready.get("selection_id") != selection["selection_id"]
                        or resolve_selection(previous_ready["wire_arguments"], tick=self.check) != selection):
                    raise InputError("v3_candidate_prepared_frozen_input_conflict")
            gateway_deadline = deadline - self.policy["return_reserve_seconds"]
            for retry_index in range(4):
                self.check()
                if time.monotonic() >= gateway_deadline:
                    raise InputError("v3_service_gateway_reserve_exhausted")
                prepared.verify_sources()
                try:
                    controls = {"generation_controls": True} if controlled_recovery else {}
                    output, observation = self.gateway.generate(prepared.messages,
                        gateway_deadline, self.cancelled, **controls)
                except GatewayError as exception:
                    observation = dict(exception.observations)
                    observation.update(attempt_index=retry_index + 1,
                        client_auto_retries=retry_index, absolute_deadline=gateway_deadline,
                        error_reason=exception.reason)
                    gateway_requests.append(observation)
                    self._save_gateway_attempt(arguments, prepared, observation)
                    allowed_reasons = {"upstream_stream_error", "upstream_timeout"} if controlled_recovery else {"upstream_stream_error"}
                    retryable = (exception.reason in allowed_reasons
                        and observation.get("upstream_error_type") == exception.reason)
                    if retryable and retry_index < 3:
                        self.check()
                        continue
                    trusted["failures"] = [{"kind": "blocked", "reason": exception.reason}]
                    break
                observation = dict(observation)
                observation.update(attempt_index=retry_index + 1,
                    client_auto_retries=retry_index, absolute_deadline=gateway_deadline)
                gateway_requests.append(observation)
                self._save_gateway_attempt(arguments, prepared, observation, output)
                if (observation.get("message_bytes") != len(prepared.post)
                        or observation.get("message_sha256") != hashlib.sha256(prepared.post).hexdigest()):
                    raise InputError("v3_service_actual_post_conflict")
                if syntax_recovery:
                    if (type(observation.get("http_status")) is not int
                            or observation["http_status"] != 200
                            or observation.get("finish_reason") != "stop"
                            or observation.get("done_marker") is not True
                            or type(observation.get("outer_replays")) is not int
                            or observation["outer_replays"] != 0
                            or observation.get("error_reason") is not None
                            or observation.get("upstream_error_type") is not None):
                        raise InputError("v3_syntax_completed_transport_required")
                    try:
                        candidate = recovery_candidate_json(output)
                    except InvalidCandidateSyntax as exception:
                        rejection_id = (arguments["request_id"] + ".candidate-rejection-"
                                        + str(retry_index + 1))
                        self.persist_scratch(rejection_id, {
                            "contract_version": 3, "request_id": arguments["request_id"],
                            "status": "candidate_rejected_not_business_acceptance",
                            "reason": exception.reason, "attempt_index": retry_index + 1,
                            "transport_attempt_id": (arguments["request_id"]
                                + ".gateway-attempt-" + str(retry_index + 1)),
                            "post_sha256": hashlib.sha256(prepared.post).hexdigest(),
                            "absolute_deadline": gateway_deadline,
                            "output": exception.output, "parse_error": exception.details})
                        observation["candidate_error_reason"] = exception.reason
                        observation["candidate_rejection_id"] = rejection_id
                        self.check()
                        prepared.verify_sources()
                        if retry_index < 3:
                            candidate = {"summary": "", "conclusions": [], "unknowns": []}
                            continue
                        raise
                else:
                    candidate = candidate_json(output)
                if candidate_recovery:
                    try:
                        task = build_task(candidate, prepared.core, raw_files,
                                          **trusted, tick=self.check)
                    except InvalidSourceCandidate as exception:
                        rejection_id = (arguments["request_id"] + ".candidate-rejection-"
                                        + str(retry_index + 1))
                        self.persist_scratch(rejection_id, {
                            "contract_version": 3, "request_id": arguments["request_id"],
                            "status": "candidate_rejected_not_business_acceptance",
                            "reason": exception.reason, "attempt_index": retry_index + 1,
                            "transport_attempt_id": (arguments["request_id"]
                                + ".gateway-attempt-" + str(retry_index + 1)),
                            "post_sha256": hashlib.sha256(prepared.post).hexdigest(),
                            "absolute_deadline": gateway_deadline,
                            "candidate": candidate,
                            "invalid_references": exception.invalid_references})
                        observation["candidate_error_reason"] = exception.reason
                        observation["candidate_rejection_id"] = rejection_id
                        self.check()
                        prepared.verify_sources()
                        if retry_index < 3:
                            candidate = {"summary": "", "conclusions": [], "unknowns": []}
                            continue
                        raise
                model_outputs.append(output)
                break
        self.check()
        if task is None:
            task = build_task(candidate, prepared.core, raw_files, **trusted, tick=self.check)
        frame = publish_v3(prepared.core, task, raw_files, admission,
                           candidate=candidate, **trusted, tick=self.check)
        prepared.verify_sources()
        trace = {"contract_version": 3, "request_id": arguments["request_id"],
            "operation": arguments["operation"], "report": report, "selection": selection,
            "core": prepared.core.document, "candidate": candidate, "task": task,
            "post_utf8": prepared.post.decode("utf-8"), "post_sha256": hashlib.sha256(prepared.post).hexdigest(),
            "gateway_requests": gateway_requests, "model_outputs": model_outputs,
            "failures": trusted["failures"], "metrics": prepared.metrics,
            "freshness": "current_at_final_verification",
            "independent_acceptance": "requires_external_receiver",
            "semantic_acceptance": "requires_independent_and_human_review"}
        if arguments["operation"] == "live":
            elapsed = time.monotonic() - service_started
            trace["live_timing"] = {
                "soft_target_seconds": 180, "host_window_seconds": arguments["host_timeout_seconds"],
                "process_hard_ceiling_seconds": LIVE_PROCESS_SECONDS,
                "service_elapsed_before_trace_save_seconds": elapsed,
                "soft_target_met": elapsed <= 180,
                "soft_overrun_seconds": max(0, elapsed - 180),
                "soft_behavior": "observation_only_no_exception",
                "scope": "service_before_trace_save_not_outer_process_exit"}
        self.persist_scratch(arguments["request_id"], trace)
        self.check()
        return frame
