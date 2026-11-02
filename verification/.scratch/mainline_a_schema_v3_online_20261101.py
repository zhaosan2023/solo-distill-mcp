"""获准阶段 5：只读准入复核与原实例唯一在线业务。

prepare 不重跑冷预检或任何前序测试，不执行来源、不发业务 POST。
live 单独调用，180 秒仅软观测；历史窗口 240 秒，显式新恢复 600 秒共享硬限。
真实外层退出须由客户端回执证明；本入口不替代人工或双层消费。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

import gateway_readonly_diagnostic
from schema_v3_selection_oracle import expected_selection
from schema_v3_selection_ingress_oracle import unpack_selection as independent_unpack
from schema_v3_snapshot_oracle import oracle_scope
from schema_v3_wire_oracle import reassemble
from solo_distill.client import StdioClient
from solo_distill.distillation import load_policy, utf8_json
from solo_distill.extractor import Scanner, load_projects, open_root, open_relative
from solo_distill.preflight_v3 import ARTIFACT_NAMES, INSTANCE, PARENT, _save_exact
from solo_distill.selection_ingress_v3 import live_wire_arguments
from solo_distill.snapshot_v2 import snapshot_scope

ROOT = Path.cwd()
PLAN = "_bmad-output/mainline-a-v2-scenario-a-20261029.md"
CONTRACT = PARENT + "/schema-v3.execution-contract.json"
READY = "phase5-online-recovery-ready-20261101.json"
LIVE = "phase5-live-recovery-20261101"
MARKER = "verification/.scratch/" + INSTANCE + ".live-admission.full.json"
RECOVERY_MARKER = "verification/.scratch/" + INSTANCE + ".live-recovery-20261101.full.json"
ORIGINAL_REQUEST = INSTANCE + "-live-20261101"
REQUEST_ID = INSTANCE + "-live-recovery-20261101"
ADR_REVISION = 10
CONTRACT_REVISION = 11
DIAGNOSTIC = False
EXTENDED = False
PROCESS_SECONDS = 240
MINIMAL = False
COVERAGE = False
CANDIDATE = False
SYNTAX = False
FROZEN_SAMPLE = "preflight-1"
EXTENDED_MARKER = "verification/.scratch/" + INSTANCE + ".live-extended-20261101T233105Z.full.json"
EXTENDED_RECEIPT = PARENT + "/phase5-live-extended-20261101T233105Z.receipt.json"
EXTENDED_POSTMORTEM = PARENT + "/phase5-extended-postmortem-20261101T235855Z.receipt.json"
PREVIOUS_MARKER = RECOVERY_MARKER
PREVIOUS_RECEIPT = PARENT + "/phase5-live-recovery-20261101.receipt.json"
DIAGNOSTIC_MARKER = "verification/.scratch/" + INSTANCE + ".live-diagnostic-20261101T133705Z.full.json"
DIAGNOSTIC_RECEIPT = PARENT + "/phase5-live-diagnostic-20261101T133705Z.receipt.json"
POSTMORTEM = PARENT + "/phase5-diagnostic-postmortem-20261101T135839Z.receipt.json"
ATTEMPT = "unattended-20261031T1513Z"


def configure_diagnostic():
    """Select only the explicitly authorized fixed diagnostic namespace."""
    global READY, LIVE, RECOVERY_MARKER, REQUEST_ID, ADR_REVISION, CONTRACT_REVISION, DIAGNOSTIC
    global EXTENDED, MINIMAL, COVERAGE, CANDIDATE, FROZEN_SAMPLE, PROCESS_SECONDS, SYNTAX
    SYNTAX = False
    CANDIDATE = False
    COVERAGE, FROZEN_SAMPLE = False, "preflight-1"
    DIAGNOSTIC, EXTENDED, MINIMAL, PROCESS_SECONDS = True, False, False, 240
    READY = "phase5-diagnostic-ready-20261101T133705Z.json"
    LIVE = "phase5-live-diagnostic-20261101T133705Z"
    REQUEST_ID = INSTANCE + "-live-diagnostic-20261101T133705Z"
    RECOVERY_MARKER = "verification/.scratch/" + INSTANCE + ".live-diagnostic-20261101T133705Z.full.json"
    ADR_REVISION, CONTRACT_REVISION = 11, 12
def configure_extended():
    """Select the one authorized new window without resetting prior namespaces."""
    global READY, LIVE, RECOVERY_MARKER, REQUEST_ID, ADR_REVISION, CONTRACT_REVISION
    global DIAGNOSTIC, EXTENDED, MINIMAL, COVERAGE, CANDIDATE, FROZEN_SAMPLE, PROCESS_SECONDS, SYNTAX
    SYNTAX = False
    CANDIDATE = False
    COVERAGE, FROZEN_SAMPLE = False, "preflight-1"
    DIAGNOSTIC, EXTENDED, MINIMAL, PROCESS_SECONDS = False, True, False, 600
    READY = "phase5-extended-ready-20261101T233105Z.json"
    LIVE = "phase5-live-extended-20261101T233105Z"
    REQUEST_ID = INSTANCE + "-live-extended-20261101T233105Z"
    RECOVERY_MARKER = "verification/.scratch/" + INSTANCE + ".live-extended-20261101T233105Z.full.json"
    ADR_REVISION, CONTRACT_REVISION = 12, 13


def configure_minimal():
    """Select only the currently authorized single-gateway recovery namespace."""
    global READY, LIVE, RECOVERY_MARKER, REQUEST_ID, ADR_REVISION, CONTRACT_REVISION
    global DIAGNOSTIC, EXTENDED, MINIMAL, COVERAGE, CANDIDATE, FROZEN_SAMPLE, PROCESS_SECONDS, SYNTAX
    SYNTAX = False
    CANDIDATE = False
    COVERAGE, FROZEN_SAMPLE = False, "preflight-1"
    DIAGNOSTIC, EXTENDED, MINIMAL, PROCESS_SECONDS = False, False, True, 600
    READY = "phase5-minimal-ready-20261102T020451Z.json"
    LIVE = "phase5-live-minimal-20261102T020451Z"
    REQUEST_ID = INSTANCE + "-live-minimal-20261102T020451Z"
    RECOVERY_MARKER = "verification/.scratch/" + INSTANCE + ".live-minimal-20261102T020451Z.full.json"
    ADR_REVISION, CONTRACT_REVISION = 13, 14


def configure_coverage():
    """仅选择获准的新选择窗口；历史窗口、冻结对象及证据保持独立。"""
    global READY, LIVE, RECOVERY_MARKER, REQUEST_ID, ADR_REVISION, CONTRACT_REVISION
    global DIAGNOSTIC, EXTENDED, MINIMAL, COVERAGE, CANDIDATE, FROZEN_SAMPLE, PROCESS_SECONDS, SYNTAX
    SYNTAX = False
    CANDIDATE = False
    DIAGNOSTIC, EXTENDED, MINIMAL, COVERAGE = False, False, False, True
    PROCESS_SECONDS = 600
    FROZEN_SAMPLE = "preflight-coverage-20261102T050022Z-1"
    READY = "phase5-coverage-ready-20261102T050022Z.json"
    LIVE = "phase5-live-coverage-20261102T050022Z"
    REQUEST_ID = INSTANCE + "-live-coverage-20261102T050022Z"
    RECOVERY_MARKER = "verification/.scratch/" + INSTANCE + ".live-coverage-20261102T050022Z.full.json"
    ADR_REVISION, CONTRACT_REVISION = 14, 15


def configure_candidate():
    """仅选择获准候选恢复；沿用覆盖冻结输入，不复活此前窗口。"""
    global READY, LIVE, RECOVERY_MARKER, REQUEST_ID, ADR_REVISION, CONTRACT_REVISION, CANDIDATE
    configure_coverage()
    CANDIDATE = True
    READY = "phase5-candidate-ready-20261102T070153Z.json"
    LIVE = "phase5-live-candidate-20261102T070153Z"
    REQUEST_ID = INSTANCE + "-live-candidate-20261102T070153Z"
    RECOVERY_MARKER = "verification/.scratch/" + INSTANCE + ".live-candidate-20261102T070153Z.full.json"
    ADR_REVISION, CONTRACT_REVISION = 15, 16


def configure_syntax():
    """仅显式选择获准的新语法恢复窗口，共享原候选与传输配额。"""
    global READY, LIVE, RECOVERY_MARKER, REQUEST_ID, ADR_REVISION, CONTRACT_REVISION, SYNTAX
    configure_candidate()
    SYNTAX = True
    READY = "phase5-syntax-ready-20261102T085352Z.json"
    LIVE = "phase5-live-syntax-20261102T085352Z"
    REQUEST_ID = INSTANCE + "-live-syntax-20261102T085352Z"
    RECOVERY_MARKER = "verification/.scratch/" + INSTANCE + ".live-syntax-20261102T085352Z.full.json"
    ADR_REVISION, CONTRACT_REVISION = 16, 17


def expected_live_post():
    legacy = read(PARENT + "/" + FROZEN_SAMPLE + "/exact-http-post.json")
    return legacy[:-1] + b',"temperature":0.2,"max_tokens":3072}' if MINIMAL or COVERAGE else legacy


def retry_allowed(observation):
    if observation.get("candidate_error_reason") is not None:
        number = observation.get("attempt_index")
        reasons = {"invalid_or_unsent_source_id"}
        if SYNTAX:
            reasons.add("invalid_candidate_json_syntax")
        return (CANDIDATE and type(number) is int and 1 <= number <= 3
            and type(observation.get("candidate_error_reason")) is str
            and observation["candidate_error_reason"] in reasons
            and observation.get("candidate_rejection_id")
                == REQUEST_ID + ".candidate-rejection-" + str(number)
            and observation.get("error_reason") is None
            and observation.get("upstream_error_type") is None
            and type(observation.get("http_status")) is int and observation["http_status"] == 200
            and observation.get("finish_reason") == "stop"
            and observation.get("done_marker") is True
            and type(observation.get("outer_replays")) is int and observation["outer_replays"] == 0)
    allowed = {"upstream_stream_error", "upstream_timeout"} if MINIMAL or COVERAGE else {"upstream_stream_error"}
    reason = observation.get("error_reason")
    return ("candidate_rejection_id" not in observation and type(reason) is str
        and reason in allowed and observation.get("upstream_error_type") == reason)


CAPS = {"policy": "adaptive-backpressure-v1", "authority": "caller_explicit",
        "max_frame_bytes": 22016, "max_transfer_bytes": 1572864,
        "max_decoded_bytes": 1048576, "max_chunks": 512, "chunking": True}


class Guard:
    def __init__(self, seconds):
        self.started = time.monotonic()
        self.deadline = self.started + seconds

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("phase5_absolute_window_exhausted")


def require(condition, reason):
    if not condition:
        raise ValueError("phase5_" + reason)


def read(path, maximum=33554432):
    root = open_root(str(ROOT))
    try:
        fd = open_relative(root, path)
        try:
            data = bytearray()
            while True:
                part = os.read(fd, min(65536, maximum + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
                require(len(data) <= maximum, "read_budget")
            return bytes(data)
        finally:
            os.close(fd)
    finally:
        os.close(root)


def load(path):
    return json.loads(read(path))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(name, value, guard):
    raw = value if type(value) is bytes else utf8_json(value)
    root = open_root(str(ROOT))
    try:
        parent = open_relative(root, PARENT, directory=True)
        try:
            sha = _save_exact(parent, name, raw, guard)
            os.fsync(parent)
            guard.check()
        finally:
            os.close(parent)
    finally:
        os.close(root)
    return {"path": PARENT + "/" + name, "bytes": len(raw),
            "sha256": sha, "exact_readback": True}


def verify_saved_diagnostic_assets(assets):
    require(type(assets) is dict and 0 < len(assets) <= 32, "previous_diagnostic_assets")
    prefix = PARENT + "/phase5-live-diagnostic-20261101T133705Z."
    total = 0
    for asset in assets.values():
        require(type(asset) is dict and type(asset.get("path")) is str
                and asset["path"].startswith(prefix)
                and type(asset.get("bytes")) is int and 0 <= asset["bytes"] <= 16777216
                and asset.get("exact_readback") is True, "previous_diagnostic_asset_contract")
        total += asset["bytes"]
        require(total <= 33554432, "previous_diagnostic_assets_budget")
        raw = read(asset["path"], asset["bytes"])
        require(len(raw) == asset["bytes"] and digest(raw) == asset.get("sha256"),
                "previous_diagnostic_asset_changed")


def verify_prior_preservation(bound):
    paths = {
        "original_admission_sha256": MARKER,
        "original_trace_sha256": "verification/.scratch/" + ORIGINAL_REQUEST + ".full.json",
        "previous_marker_sha256": PREVIOUS_MARKER,
        "previous_receipt_sha256": PREVIOUS_RECEIPT,
        "previous_diagnostic_marker_sha256": DIAGNOSTIC_MARKER,
        "previous_diagnostic_receipt_sha256": DIAGNOSTIC_RECEIPT,
        "previous_postmortem_sha256": POSTMORTEM,
        "previous_extended_marker_sha256": EXTENDED_MARKER,
        "previous_extended_receipt_sha256": EXTENDED_RECEIPT,
        "previous_extended_postmortem_sha256": EXTENDED_POSTMORTEM}
    for key, path in paths.items():
        if key in bound:
            require(digest(read(path)) == bound[key], "prior_preservation:" + key)
    if "previous_diagnostic_assets" in bound:
        verify_saved_diagnostic_assets(bound["previous_diagnostic_assets"])


    if "previous_extended_assets" in bound:
        verify_saved_extended_assets(bound["previous_extended_assets"])
    if "previous_minimal_receipt_sha256" in bound:
        import mainline_a_schema_v3_coverage_20261102 as coverage_support
        coverage_support.verify_preservation(sys.modules[__name__], bound)
    if "previous_coverage_receipt_sha256" in bound:
        import mainline_a_schema_v3_candidate_20261102 as candidate_support
        candidate_support.verify_preservation(sys.modules[__name__], bound)
    if "previous_candidate_receipt_sha256" in bound:
        import mainline_a_schema_v3_syntax_20261102 as syntax_support
        syntax_support.verify_preservation(sys.modules[__name__], bound)


def verify_saved_extended_assets(assets):
    require(type(assets) is dict and 0 < len(assets) <= 32, "previous_extended_assets")
    prefix = PARENT + "/phase5-live-extended-20261101T233105Z."
    total = 0
    for asset in assets.values():
        require(type(asset) is dict and type(asset.get("path")) is str
                and asset["path"].startswith(prefix)
                and type(asset.get("bytes")) is int and 0 <= asset["bytes"] <= 16777216
                and asset.get("exact_readback") is True, "previous_extended_asset_contract")
        total += asset["bytes"]
        require(total <= 33554432, "previous_extended_assets_budget")
        raw = read(asset["path"], asset["bytes"])
        require(len(raw) == asset["bytes"] and digest(raw) == asset.get("sha256"),
                "previous_extended_asset_changed")


def verify_minimal_previous(contract):
    """Independently verify the exited extended window; no writes or business POST."""
    minimal = contract["minimal_recovery_contract"]
    fixed = {"request_id": INSTANCE + "-live-minimal-20261102T020451Z",
        "marker_id": INSTANCE + ".live-minimal-20261102T020451Z",
        "recover_from_request_id": ORIGINAL_REQUEST,
        "previous_extended_request_id": INSTANCE + "-live-extended-20261101T233105Z",
        "previous_extended_receipt": EXTENDED_RECEIPT,
        "previous_extended_postmortem": EXTENDED_POSTMORTEM,
        "process_seconds": 600, "soft_target_seconds": 180,
        "return_reserve_seconds": 15, "max_retries": 3, "max_http_attempts": 4}
    require(type(minimal) is dict and all(type(minimal.get(key)) is type(value)
        and minimal[key] == value for key, value in fixed.items()), "minimal_contract")
    controls = minimal.get("generation_controls")
    require(type(controls) is dict and set(controls) == {"temperature", "max_tokens"}
            and type(controls["temperature"]) is float and controls["temperature"] == 0.2
            and type(controls["max_tokens"]) is int and controls["max_tokens"] == 3072
            and minimal.get("retryable_reasons") == ["upstream_stream_error", "upstream_timeout"],
            "minimal_generation_and_retry_contract")
    bound = verify_extended_previous(contract)
    marker_raw = read(EXTENDED_MARKER, 4096)
    marker = json.loads(marker_raw)
    receipt_raw = read(EXTENDED_RECEIPT, 32768)
    receipt = json.loads(receipt_raw)
    audit_raw = read(EXTENDED_POSTMORTEM, 16384)
    audit = json.loads(audit_raw)
    require(marker.get("request_id") == fixed["previous_extended_request_id"]
            and marker.get("recover_from_request_id") == ORIGINAL_REQUEST
            and marker.get("previous_recovery_request_id") == INSTANCE + "-live-recovery-20261101"
            and marker.get("previous_diagnostic_request_id") == INSTANCE + "-live-diagnostic-20261101T133705Z"
            and marker.get("automatic_replay") is False
            and type(marker.get("max_retries")) is int and marker["max_retries"] == 3
            and type(marker.get("process_seconds")) is int and marker["process_seconds"] == 600
            and marker.get("previous_diagnostic_receipt_sha256") == bound["previous_diagnostic_receipt_sha256"]
            and marker.get("previous_postmortem_sha256") == bound["previous_postmortem_sha256"],
            "minimal_previous_marker")
    require(receipt.get("request_id") == marker["request_id"]
            and receipt.get("execution_instance") == INSTANCE and receipt.get("status") == "blocked"
            and type(receipt.get("service_exit_code")) is int and receipt["service_exit_code"] == 0
            and receipt.get("service_cleanup") == "confirmed_natural_exit"
            and type(receipt.get("business_call_attempts")) is int and receipt["business_call_attempts"] == 1
            and audit.get("execution_instance") == INSTANCE and audit.get("online_acceptance") == "blocked",
            "minimal_previous_exit")
    actual = audit.get("online_result")
    require(type(actual) is dict and type(actual.get("actual_exit_code")) is int
            and actual["actual_exit_code"] == 2
            and type(actual.get("service_exit_code")) is int and actual["service_exit_code"] == 0
            and actual.get("service_cleanup") == "confirmed_natural_exit"
            and actual.get("original_receipt") == EXTENDED_RECEIPT
            and type(actual.get("original_receipt_bytes")) is int
            and actual["original_receipt_bytes"] == len(receipt_raw)
            and actual.get("original_receipt_sha256") == digest(receipt_raw), "minimal_actual_exit")
    prior = receipt.get("binding")
    require(type(prior) is dict and all(prior.get(key) == bound[key]
        for key in bound), "minimal_prior_binding")
    bound.update(previous_extended_marker_sha256=digest(marker_raw),
        previous_extended_receipt_sha256=digest(receipt_raw),
        previous_extended_postmortem_sha256=digest(audit_raw),
        previous_extended_assets=receipt.get("assets"))
    verify_prior_preservation(bound)
    return bound


def verify_extended_previous(contract):
    """Read-only admission evidence; never create or consume an online marker."""
    extended = contract["extended_recovery_contract"]
    fixed = {
        "request_id": INSTANCE + "-live-extended-20261101T233105Z",
        "recover_from_request_id": ORIGINAL_REQUEST,
        "previous_recovery_request_id": INSTANCE + "-live-recovery-20261101",
        "previous_diagnostic_request_id": INSTANCE + "-live-diagnostic-20261101T133705Z",
        "marker_id": INSTANCE + ".live-extended-20261101T233105Z",
        "previous_diagnostic_marker_id": INSTANCE + ".live-diagnostic-20261101T133705Z",
        "previous_diagnostic_receipt": DIAGNOSTIC_RECEIPT,
        "previous_postmortem": POSTMORTEM,
        "process_seconds": 600, "soft_target_seconds": 180,
        "return_reserve_seconds": 15, "max_retries": 3, "max_http_attempts": 4}
    require(type(extended) is dict and all(
        type(extended.get(key)) is type(value) and extended[key] == value
        for key, value in fixed.items()), "extended_contract")
    original = load(MARKER)
    previous = load(PREVIOUS_MARKER)
    first = load(PREVIOUS_RECEIPT)
    diagnostic = load(DIAGNOSTIC_MARKER)
    receipt_raw = read(DIAGNOSTIC_RECEIPT, 16384)
    receipt = json.loads(receipt_raw)
    audit_raw = read(POSTMORTEM, 16384)
    audit = json.loads(audit_raw)
    require(original.get("request_id") == ORIGINAL_REQUEST
            and original.get("automatic_replay") is False
            and previous.get("request_id") == fixed["previous_recovery_request_id"]
            and previous.get("recover_from_request_id") == ORIGINAL_REQUEST
            and previous.get("automatic_replay") is False
            and type(previous.get("max_retries")) is int and previous["max_retries"] == 3
            and first.get("request_id") == previous["request_id"]
            and first.get("execution_instance") == INSTANCE and first.get("status") == "blocked"
            and type(first.get("service_exit_code")) is int and first["service_exit_code"] == 2
            and first.get("service_cleanup") == "confirmed_nonzero_exit"
            and type(first.get("business_call_attempts")) is int and first["business_call_attempts"] == 1,
            "extended_previous_recovery")
    require(diagnostic.get("request_id") == fixed["previous_diagnostic_request_id"]
            and diagnostic.get("recover_from_request_id") == ORIGINAL_REQUEST
            and diagnostic.get("previous_recovery_request_id") == previous["request_id"]
            and diagnostic.get("automatic_replay") is False
            and type(diagnostic.get("max_retries")) is int and diagnostic["max_retries"] == 3
            and receipt.get("request_id") == diagnostic["request_id"]
            and receipt.get("execution_instance") == INSTANCE and receipt.get("status") == "blocked"
            and type(receipt.get("service_exit_code")) is int and receipt["service_exit_code"] == 0
            and receipt.get("service_cleanup") == "confirmed_natural_exit"
            and type(receipt.get("business_call_attempts")) is int
            and receipt["business_call_attempts"] == 1
            and audit.get("execution_instance") == INSTANCE
            and audit.get("online_acceptance") == "blocked", "extended_previous_diagnostic")
    actual = audit["online_result"]
    require(type(actual.get("actual_exit_code")) is int and actual["actual_exit_code"] == 2
            and type(actual.get("service_exit_code")) is int and actual["service_exit_code"] == 0
            and actual.get("service_cleanup") == "confirmed_natural_exit"
            and actual.get("original_receipt") == DIAGNOSTIC_RECEIPT
            and type(actual.get("original_receipt_bytes")) is int
            and actual["original_receipt_bytes"] == len(receipt_raw)
            and actual.get("original_receipt_sha256") == digest(receipt_raw), "extended_actual_exit")
    prior = receipt["binding"]
    require(type(prior) is dict and all(key in prior for key in (
        "original_admission_sha256", "original_trace_sha256",
        "previous_marker_sha256", "previous_receipt_sha256")), "extended_prior_binding")
    bound = {key: prior[key] for key in (
        "original_admission_sha256", "original_trace_sha256",
        "previous_marker_sha256", "previous_receipt_sha256")}
    bound.update(previous_diagnostic_marker_sha256=digest(read(DIAGNOSTIC_MARKER)),
        previous_diagnostic_receipt_sha256=digest(receipt_raw),
        previous_postmortem_sha256=digest(audit_raw),
        previous_diagnostic_assets=receipt["assets"])
    verify_prior_preservation(bound)
    return bound


def absolute_live_arguments(wire, started_ms):
    require(type(wire.get("host_timeout_seconds")) is int
            and wire["host_timeout_seconds"] == PROCESS_SECONDS
            and wire.get("request_id") == REQUEST_ID, "live_absolute_declaration")
    arguments = dict(wire)
    arguments["started_at_unix_ms"] = started_ms
    arguments["deadline_unix_ms"] = started_ms + PROCESS_SECONDS * 1000
    return arguments


def binding():
    plan = read(PLAN)
    lines = plan.decode("utf-8").splitlines()
    for text in ("plan_id: mainline-a-v2-scenario-a-20261029", "contract_version: 6",
                 "status: EXECUTING", "adr_revision: " + str(ADR_REVISION), "session_id: null",
                 "execution_instance: " + INSTANCE):
        require(lines.count(text) == 1, "plan_binding")
    contract_raw = read(CONTRACT)
    contract = json.loads(contract_raw)
    require(contract["contract_revision"] == CONTRACT_REVISION and contract["adr_revision"] == ADR_REVISION
            and contract["plan_contract_version"] == 6
            and contract["execution_instance"] == INSTANCE
            and contract["plan_id"] == "mainline-a-v2-scenario-a-20261029"
            and contract["session_id"] is None, "execution_binding")
    adr_raw = read(contract["adr_file"])
    adr = adr_raw.decode("utf-8").splitlines()
    for text in ("adr_id: ADR-20261030-harel-fastcode-schema-v3",
                 "plan_id: mainline-a-v2-scenario-a-20261029", "contract_version: 6",
                 "adr_revision: " + str(ADR_REVISION), "status: PROPOSED", "session_id: null"):
        require(adr.count(text) == 1, "adr_binding")
    gates = contract["resource_gates"]
    for key, value in CAPS.items():
        if key.startswith("max_"):
            require(type(gates[key]) is int and gates[key] == value, "capacity_binding")
    require(gates["http_post_bytes"] == 150000 and gates["body_utf8_bytes"] == 98304
            and gates["live_soft_target_seconds"] == 180
            and gates["live_process_seconds"] == PROCESS_SECONDS
            and contract["frozen_selection_ingress_contract"]["field"]
            == "compressed_frozen_selection", "live_contract")
    policy = load_policy(str(ROOT / "config/distillation.json"))
    require(policy["endpoint"] == "http://127.0.0.1:8080/v1/chat/completions"
            and policy["model"] == "gpt-6-sol"
            and "fastcode-sample" in policy["authorized_project_ids"], "gateway_policy")
    retry = contract["live_retry_contract"]
    require(retry["retryable_reason"] == "upstream_stream_error"
            and type(retry["max_retries"]) is int and retry["max_retries"] == 3
            and retry["max_http_attempts_per_recovery_window"] == 4
            and retry["process_seconds"] == PROCESS_SECONDS
            and policy["return_reserve_seconds"] == 15, "retry_contract")
    original_marker = load(MARKER)
    require(original_marker["request_id"] == ORIGINAL_REQUEST
            and original_marker["automatic_replay"] is False, "original_admission_binding")
    require(not os.path.lexists(ROOT / RECOVERY_MARKER), "recovery_admission_already_consumed")
    previous_binding = {}
    if SYNTAX:
        import mainline_a_schema_v3_syntax_20261102 as syntax_support
        previous_binding = syntax_support.verify_previous(sys.modules[__name__], contract)
    elif CANDIDATE:
        import mainline_a_schema_v3_candidate_20261102 as candidate_support
        previous_binding = candidate_support.verify_previous(sys.modules[__name__], contract)
    elif COVERAGE:
        import mainline_a_schema_v3_coverage_20261102 as coverage_support
        previous_binding = coverage_support.verify_previous(sys.modules[__name__], contract)
    elif MINIMAL:
        previous_binding = verify_minimal_previous(contract)
    elif EXTENDED:
        previous_binding = verify_extended_previous(contract)
    elif DIAGNOSTIC:
        diagnostic = contract["diagnostic_recovery_contract"]
        require(diagnostic["request_id"] == REQUEST_ID
                and diagnostic["recover_from_request_id"] == ORIGINAL_REQUEST
                and diagnostic["previous_recovery_request_id"] == INSTANCE + "-live-recovery-20261101"
                and diagnostic["marker_id"] + ".full.json" == RECOVERY_MARKER.rsplit("/", 1)[1]
                and diagnostic["process_seconds"] == 240
                and diagnostic["max_retries"] == 3 and diagnostic["max_http_attempts"] == 4,
                "diagnostic_contract")
        previous = load(PREVIOUS_MARKER)
        failed = load(PREVIOUS_RECEIPT)
        require(previous["request_id"] == INSTANCE + "-live-recovery-20261101"
                and previous["recover_from_request_id"] == ORIGINAL_REQUEST
                and previous["automatic_replay"] is False and previous["max_retries"] == 3
                and failed["request_id"] == previous["request_id"]
                and failed["execution_instance"] == INSTANCE and failed["status"] == "blocked"
                and type(failed["service_exit_code"]) is int and failed["service_exit_code"] == 2
                and failed["service_cleanup"] == "confirmed_nonzero_exit"
                and failed["business_call_attempts"] == 1, "previous_recovery_failure")
        require(digest(read(MARKER)) == failed["binding"]["original_admission_sha256"]
                and digest(read("verification/.scratch/" + ORIGINAL_REQUEST + ".full.json"))
                == failed["binding"]["original_trace_sha256"], "original_failure_unchanged")
        previous_binding = {"previous_marker_sha256": digest(read(PREVIOUS_MARKER)),
                            "previous_receipt_sha256": digest(read(PREVIOUS_RECEIPT))}
    original_trace = load("verification/.scratch/" + ORIGINAL_REQUEST + ".full.json")
    require(original_trace["request_id"] == ORIGINAL_REQUEST
            and original_trace["failures"] == [{"kind": "blocked", "reason": "upstream_stream_error"}]
            and len(original_trace["gateway_requests"]) == 1, "original_retryable_failure")
    original_sse = base64.b64decode(original_trace["gateway_requests"][0]["raw_sse_base64"], validate=True)
    require(b'"upstream_stream_error"' in original_sse, "original_typed_failure_evidence")
    return {"execution_instance": INSTANCE, "plan_contract_version": 6,
            "adr_revision": ADR_REVISION, "execution_contract_revision": CONTRACT_REVISION, "session_id": None,
            **previous_binding,
            "original_admission_sha256": digest(read(MARKER)),
            "original_trace_sha256": digest(read("verification/.scratch/" + ORIGINAL_REQUEST + ".full.json")),
            "plan_sha256": digest(plan), "adr_sha256": digest(adr_raw),
            "execution_contract_sha256": digest(contract_raw),
            "projects_sha256": digest(read("config/projects.json")),
            "policy_sha256": digest(read("config/distillation.json"))}


def listener():
    inodes = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in Path(name).read_text().splitlines()[1:]:
            fields = line.split()
            address, port = fields[1].rsplit(":", 1)
            reachable = address in {"00000000", "0100007F", "0" * 32,
                                   "00000000000000000000000001000000"}
            if reachable and port == "1F90" and fields[3] == "0A":
                inodes.add(fields[9])
    return sorted(inodes)


def current_source(frozen, guard):
    project = load_projects(str(ROOT / "config/projects.json"))["fastcode-sample"]
    require(frozen["project_id"] == project.project_id
            and frozen["source_root"] == project.root
            and frozen["include"] == list(project.include), "source_scope")
    scanner = Scanner(project, tuple(frozen["include"]), project.max_seconds)
    scanner.deadline = min(scanner.deadline, guard.deadline)
    report = scanner.run()
    guard.check()
    # 扫描耗时属于观测，不参与快照或结构等价；其余字段全部比较。
    require({k: v for k, v in report.items() if k != "observations"}
            == {k: v for k, v in frozen["report"].items() if k != "observations"},
            "current_source_report_changed")
    expected = {p: base64.b64decode(b, validate=True)
                for p, b in frozen["raw_files_base64"].items()}
    entries = {item["path"]: item for item in report["entries"]
               if item["status"] == "parsed"}
    require(bool(expected) and set(expected) == set(entries), "frozen_source_denominator")
    root = open_root(project.root)
    try:
        for path, raw in sorted(expected.items()):
            guard.check()
            actual, version = scanner.read_bytes(root, path, project.max_file_bytes)
            require(actual == raw and digest(actual) == entries[path]["content_sha256"]
                    and version == scanner.versions[path], "current_source_content_changed")
    finally:
        os.close(root)
    guard.check()
    return expected


@snapshot_scope
@oracle_scope
def prepare():
    if SYNTAX:
        import mainline_a_schema_v3_syntax_20261102 as syntax_support
        return syntax_support.prepare(sys.modules[__name__])
    if CANDIDATE:
        import mainline_a_schema_v3_candidate_20261102 as candidate_support
        return candidate_support.prepare(sys.modules[__name__])
    if COVERAGE:
        import mainline_a_schema_v3_coverage_20261102 as coverage_support
        return coverage_support.prepare(sys.modules[__name__])
    guard = Guard(30)
    bound = binding()
    regression = load(PARENT + "/phase5-production-stdio-call-20d17f10981b80b8d56d4d9e.receipt.json")
    require(regression["actual_exit_code"] == 0 and regression["tests_run"] == 14
            and regression["failed"] == 0 and regression["errors"] == 0
            and regression["skipped"] == 0 and regression["adr_revision"] == 9
            and regression["execution_contract_revision"] == 10, "production_regression_receipt")
    retry_regression = load(PARENT + (
        "/phase5-minimal-regression-20261102T020451Z.receipt.json" if MINIMAL else
        "/phase5-extended-regression-20261101T233105Z.receipt.json" if EXTENDED else
        "/phase5-diagnostic-regression-20261101T133705Z.receipt.json" if DIAGNOSTIC else
        "/phase5-retry-regression-20261101.receipt.json"))
    require(retry_regression["actual_exit_code"] == 0
            and retry_regression["tests_run"] > 0
            and retry_regression["failed"] == 0 and retry_regression["errors"] == 0
            and retry_regression["skipped"] == 0
            and retry_regression["adr_revision"] == ADR_REVISION
            and retry_regression["execution_contract_revision"] == CONTRACT_REVISION,
            "retry_regression_receipt")
    samples = []
    frozen = None
    for index in range(1, 4):
        sample = "preflight-" + str(index)
        audit_path = PARENT + "/" + sample + "." + ATTEMPT + ".phase4-audit.receipt.json"
        audit = load(audit_path)
        original = audit["binding"]
        require(audit["status"] == "pass" and audit["actual_exit_code"] == 0
                and audit["attempt_id"] == ATTEMPT
                and original["execution_instance"] == INSTANCE
                and original["plan_contract_version"] == 6
                and original["adr_revision"] == 7
                and original["execution_contract_revision"] == 8
                and original["wire_encoding"] == "lz-json-v3-1", "original_sample_binding")
        assets = {}
        for name in ARTIFACT_NAMES:
            guard.check()
            raw = read(PARENT + "/" + sample + "/" + name)
            measured = {"bytes": len(raw), "sha256": digest(raw)}
            require(measured == audit["six_artifact_hash_readback"][name], "original_asset_changed")
            assets[name] = measured
        item = load(PARENT + "/" + sample + "/frozen-source-and-selection.json")
        if frozen is None:
            frozen = item
        else:
            require({k: v for k, v in item.items() if k != "report"}
                    == {k: v for k, v in frozen.items() if k != "report"}
                    and {k: v for k, v in item["report"].items() if k != "observations"}
                    == {k: v for k, v in frozen["report"].items() if k != "observations"},
                    "inter_sample_frozen_source_changed")
        samples.append({"sample_id": sample, "audit_path": audit_path,
                        "original_binding": original, "artifact_readback": assets})
    raw_files = current_source(frozen, guard)
    messages, post, selection = expected_selection(raw_files,
        project_id=frozen["project_id"], snapshot_id=frozen["report"]["snapshot_id"],
        question=frozen["question"], dimensions=frozen["dimensions"],
        frozen_coverage=frozen["report"]["coverage"], tick=guard.check)
    require(selection == frozen["selection"]
            and post == read(PARENT + "/preflight-1/exact-http-post.json"), "independent_frozen_selection")
    if MINIMAL:
        post = post[:-1] + b',"temperature":0.2,"max_tokens":3072}'
        require(post == expected_live_post(), "minimal_exact_post")
    require(0 < len(post) <= 150000
            and selection["witness"]["unique_body_bytes"] <= 98304, "post_and_body_budget")
    arguments = {"contract_version": 3, "protocol_version": "2024-11-05", "operation": "live",
        "request_id": REQUEST_ID, "project_id": frozen["project_id"],
        "recover_from_request_id": ORIGINAL_REQUEST,
        "scenario": "A", "question": frozen["question"], "dimensions": frozen["dimensions"],
        "include": frozen["include"], "started_at_unix_ms": 1,
        "deadline_unix_ms": 1 + PROCESS_SECONDS * 1000,
        "host_timeout_seconds": PROCESS_SECONDS, "return_bytes": 22016, "transport": CAPS,
        "wire_encoding": "lz-json-v3-1", "frozen_selection": selection}
    wire, _ = live_wire_arguments(arguments, selection, tick=guard.check)
    packed = wire.pop("frozen_selection_lz")
    require(independent_unpack(packed, tick=guard.check) == selection, "independent_ingress_decode")
    wire["compressed_frozen_selection"] = packed
    request = utf8_json({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "solo_distill_knowledge_v3", "arguments": wire}}) + b"\n"
    require(len(request) <= 65536, "complete_live_request_budget")
    captured = io.StringIO()
    before = listener()
    with contextlib.redirect_stdout(captured):
        code = gateway_readonly_diagnostic.run()
    health = json.loads(captured.getvalue())
    after = listener()
    old_listener = load(PARENT + "/health." + ATTEMPT + ".receipt.json")["listener_after"]
    require(code == 0 and bool(before) and before == after == old_listener
            and health["results"][0].get("health_reports_ok") is True
            and all(item.get("http_status") == 200 for item in health["results"]), "current_gateway_health")
    require(binding() == bound, "binding_changed_during_prepare")
    ready = {"status": "ready_not_online_executed", "binding": bound,
        "original_samples": samples, "wire_arguments": wire,
        "snapshot_id": frozen["report"]["snapshot_id"], "selection_id": selection["selection_id"],
        "post_bytes": len(post), "post_sha256": digest(post),
        "body_utf8_bytes": selection["witness"]["unique_body_bytes"],
        "compressed_selection_bytes": len(utf8_json(packed)),
        "listener_inodes": after, "health": health,
        "elapsed_seconds": time.monotonic() - guard.started,
        "controller_coverage": "not_proven_platform_task_record_used",
        "business_posts": 0, "cold_preflight_runs": 0, "prior_test_runs": 0}
    evidence = save(READY, ready, guard)
    print(json.dumps({"status": ready["status"], "evidence": evidence,
        "post_bytes": len(post), "body_utf8_bytes": ready["body_utf8_bytes"],
        "compressed_selection_bytes": ready["compressed_selection_bytes"],
        "listener_inodes": after, "elapsed_seconds": time.monotonic() - guard.started}), flush=True)
    return 0


def preserve_failure_evidence(client, receipt, guard):
    """Save available failure evidence independently of a missing delivery receipt."""
    assets = receipt["assets"]
    candidates = [("stderr", LIVE + ".stderr.txt", bytes(client.stderr))]
    if "request_wire" not in assets and hasattr(client, "last_request_bytes"):
        candidates.append(("request_wire", LIVE + ".request.jsonl", client.last_request_bytes))
    request_id = receipt.get("request_id")
    if request_id:
        paths = [("failure", LIVE + ".failure.json",
                  "verification/.scratch/" + request_id + ".failure.full.json"),
                 ("admission", LIVE + ".admission.json", RECOVERY_MARKER)]
        for number in range(1, 5):
            paths.append(("transport_attempt_" + str(number),
                LIVE + ".transport-attempt-" + str(number) + ".json",
                "verification/.scratch/" + request_id + ".gateway-attempt-" + str(number) + ".full.json"))
            if CANDIDATE:
                paths.append(("candidate_rejection_" + str(number),
                    LIVE + ".candidate-rejection-" + str(number) + ".json",
                    "verification/.scratch/" + request_id + ".candidate-rejection-" + str(number) + ".full.json"))
        for key, name, path in paths:
            if key in assets:
                continue
            try:
                candidates.append((key, name, read(path)))
            except FileNotFoundError:
                continue
            except Exception as exception:
                receipt["failures"].append("failure_evidence_read:" + type(exception).__name__)
    for key, name, raw in candidates:
        if key in assets:
            continue
        try:
            assets[key] = save(name, raw, guard)
        except Exception as exception:
            receipt["failures"].append("failure_evidence_save:" + type(exception).__name__)
    receipt["status"] = "blocked"


def require_gateway_completion(observation, sse):
    """Keep failed partial capture distinct from successful stream completeness."""
    require(len(sse) == observation.get("raw_sse_bytes")
            and digest(sse) == observation.get("raw_sse_sha256"), "complete_sse_capture")
    require(observation.get("error_reason") is None,
            "gateway_failed:" + str(observation.get("error_reason")))
    require(bool(sse) and len(sse) == observation.get("response_bytes"), "complete_sse_capture")
    require(observation.get("http_status") == 200
            and observation.get("finish_reason") == "stop"
            and observation.get("done_marker") is True
            and observation.get("outer_replays") == 0, "normal_gateway_completion")


def save_or_verify_asset(receipt, key, name, raw, guard):
    """Reuse only the exact asset already saved during this invocation."""
    existing = receipt["assets"].get(key)
    if existing is None:
        return save(name, raw, guard)
    guard.check()
    expected = {"path": PARENT + "/" + name, "bytes": len(raw),
                "sha256": digest(raw), "exact_readback": True}
    require(existing == expected and read(expected["path"]) == raw, "existing_asset_binding")
    guard.check()
    return existing


@snapshot_scope
def live():
    guard = Guard(PROCESS_SECONDS)
    started_ms = int(time.time() * 1000)
    receipt = {"status": "blocked", "execution_instance": INSTANCE, "failures": [],
        "soft_target_seconds": 180, "process_hard_seconds": PROCESS_SECONDS,
        "outer_replays": 0, "source_writes": 0, "gateway_restart_operations": 0,
        "business_call_attempts": 0, "semantic_acceptance": "not_proven",
        "host_loading": "not_proven", "assets": {}}
    client = None
    try:
        ready = load(PARENT + "/" + READY)
        require(ready["status"] == "ready_not_online_executed"
                and ready["binding"] == binding(), "live_ready_binding")
        require(listener() == ready["listener_inodes"], "listener_changed_before_live")
        frozen = load(PARENT + "/" + FROZEN_SAMPLE + "/frozen-source-and-selection.json")
        current_source(frozen, guard)
        arguments = absolute_live_arguments(ready["wire_arguments"], started_ms)
        request = utf8_json({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "solo_distill_knowledge_v3", "arguments": arguments}}) + b"\n"
        require(len(request) <= 65536, "complete_live_request_budget")
        receipt["request_id"] = arguments["request_id"]
        receipt["binding"] = ready["binding"]
        receipt["assets"]["request"] = save(LIVE + ".request.json", arguments, guard)
        client = StdioClient(ROOT, online=True, v3=True, deadline=guard.deadline)
        tools = client.initialize()
        require([tool["name"] for tool in tools["result"]["tools"]]
                == ["solo_distill_knowledge_v3"], "live_tool_registration")
        receipt["business_call_attempts"] = 1
        raw = client.receive_v3(arguments, tick=guard.check)
        receipt["assets"]["stdio"] = save(LIVE + ".stdio.jsonl", raw, guard)
        receipt["assets"]["request_wire"] = save(LIVE + ".request.jsonl", client.last_request_bytes, guard)
        receipt["request_wire_bytes"] = len(client.last_request_bytes)
        trace_path = "verification/.scratch/" + arguments["request_id"] + ".full.json"
        trace_raw = read(trace_path)
        trace = json.loads(trace_raw)
        receipt["assets"]["trace"] = save(LIVE + ".trace.json", trace_raw, guard)
        post = trace["post_utf8"].encode("utf-8")
        receipt["assets"]["post"] = save(LIVE + ".exact-http-post.json", post, guard)
        receipt["assets"]["model_outputs"] = save(LIVE + ".model-output.json", trace["model_outputs"], guard)
        receipt["assets"]["candidate"] = save(LIVE + ".candidate.json", trace["candidate"], guard)
        attempts = trace["gateway_requests"]
        require(1 <= len(attempts) <= 4, "bounded_gateway_attempts")
        receipt["actual_http_attempts"] = len(attempts)
        receipt["client_auto_retries"] = len(attempts) - 1
        receipt["gateway_observations"] = []
        for index, attempt in enumerate(attempts):
            require(attempt.get("attempt_index") == index + 1
                    and attempt.get("client_auto_retries") == index
                    and attempt.get("message_bytes") == len(post)
                    and attempt.get("message_sha256") == digest(post)
                    and attempt.get("absolute_deadline") == attempts[0].get("absolute_deadline"),
                    "per_attempt_post_and_budget_binding")
            captured = base64.b64decode(attempt.get("raw_sse_base64", ""), validate=True)
            require(bool(captured) and len(captured) == attempt.get("raw_sse_bytes")
                    and digest(captured) == attempt.get("raw_sse_sha256"), "attempt_capture_binding")
            receipt["assets"]["sse_attempt_" + str(index + 1)] = save(
                LIVE + ".gateway-attempt-" + str(index + 1) + ".sse", captured, guard)
            receipt["gateway_observations"].append({k: v for k, v in attempt.items() if k != "raw_sse_base64"})
            if SYNTAX:
                import mainline_a_schema_v3_syntax_20261102 as syntax_support
                syntax_support.verify_attempt(sys.modules[__name__], receipt,
                    attempt, post, captured, guard)
            elif CANDIDATE:
                import mainline_a_schema_v3_candidate_20261102 as candidate_support
                candidate_support.verify_attempt(sys.modules[__name__], receipt,
                    attempt, post, captured, guard)
            if index < len(attempts) - 1:
                require(retry_allowed(attempt), "only_typed_transient_retried")
        observation = attempts[-1]
        sse = base64.b64decode(observation.get("raw_sse_base64", ""), validate=True)
        receipt["assets"]["sse"] = save(LIVE + ".gateway.sse", sse, guard)
        receipt["gateway_observation"] = {k: v for k, v in observation.items() if k != "raw_sse_base64"}
        require(post == expected_live_post()
                and len(post) == ready["post_bytes"] and digest(post) == ready["post_sha256"]
                and observation.get("message_bytes") == len(post)
                and observation.get("message_sha256") == digest(post), "actual_post_binding")
        require_gateway_completion(observation, sse)
        if CANDIDATE:
            require(observation.get("candidate_error_reason") is None
                and "candidate_rejection_id" not in observation, "final_candidate_not_rejected")
            from mainline_a_schema_v3_coverage_failure_diagnostic_20261102 import sse_output
            from solo_distill.gateway import candidate_json
            output, finishes, done, errors = sse_output(sse)
            require(trace["model_outputs"] == [output] and candidate_json(output) == trace["candidate"]
                and finishes == ["stop"] and done and not errors, "final_whole_candidate_binding")
        require(observation.get("client_auto_retries") == len(attempts) - 1, "normal_gateway_completion")
        require(type(trace["model_outputs"]) is list and len(trace["model_outputs"]) == 1
                and type(trace["model_outputs"][0]) is str and bool(trace["model_outputs"][0].strip()),
                "nonempty_model_output")
        require(trace["selection"] == frozen["selection"]
                and {k: v for k, v in trace["report"].items() if k != "observations"}
                == {k: v for k, v in frozen["report"].items() if k != "observations"}
                and not trace["failures"], "live_source_selection_failure")
        decoded, manifest = reassemble(raw, CAPS, response_id=3, return_bytes=22016,
                                       wire_encoding="lz-json-v3-1", tick=guard.check)
        receipt["assets"]["decoded"] = save(LIVE + ".decoded.jsonl", decoded, guard)
        receipt["physical_limits"] = {"post_bytes": len(post), "body_utf8_bytes": ready["body_utf8_bytes"],
            "decoded_bytes": len(decoded), "transfer_bytes": len(raw),
            "max_frame_bytes": max(map(len, raw.splitlines(keepends=True))),
            "physical_frames": raw.count(b"\n"),
            "notification_chunks": 0 if manifest is None else manifest["manifest"]["count"]}
        require(receipt["physical_limits"]["post_bytes"] <= 150000
                and receipt["physical_limits"]["body_utf8_bytes"] <= 98304
                and receipt["physical_limits"]["decoded_bytes"] <= 1048576
                and receipt["physical_limits"]["transfer_bytes"] <= 1572864
                and receipt["physical_limits"]["max_frame_bytes"] <= 22016, "four_physical_limits")
        receipt["live_timing"] = trace["live_timing"]
        receipt["status"] = "online_transport_saved_trusted_consumption_pending"
    except Exception as error:
        receipt["failures"].append(type(error).__name__ + ":" + str(error)[:240])
    finally:
        if client is not None:
            client.close()
            receipt.update(service_exit_code=client.exit_code, service_cleanup=client.cleanup,
                           stderr_bytes=len(client.stderr), stderr_sha256=digest(bytes(client.stderr)))
            if client.exit_code != 0 or client.cleanup != "confirmed_natural_exit" or client.stderr:
                receipt["status"] = "blocked"
                receipt["failures"].append("service_exit_or_cleanup_gate")
            if receipt["status"] == "blocked":
                preserve_failure_evidence(client, receipt, guard)
        if receipt.get("request_id"):
            try:
                path = "verification/.scratch/" + receipt["request_id"] + ".delivery.full.json"
                delivery_raw = read(path)
                delivery = json.loads(delivery_raw)
                receipt["assets"]["delivery"] = save(LIVE + ".delivery.json", delivery_raw, guard)
                wire = receipt["assets"]["stdio"]
                require(delivery["written"] == wire["bytes"]
                        and delivery["frame_sha256"] == wire["sha256"]
                        and delivery["status"] == "frame_written_not_host_consumption", "delivery_binding")
            except Exception as error:
                receipt["status"] = "blocked"
                receipt["failures"].append(type(error).__name__ + ":" + str(error)[:240])
            try:
                verify_prior_preservation(ready["binding"])
                marker_raw = read(RECOVERY_MARKER)
                marker = json.loads(marker_raw)
                require(marker["request_id"] == receipt["request_id"]
                        and marker["recover_from_request_id"] == ORIGINAL_REQUEST
                        and marker["max_retries"] == 3
                        and marker["automatic_replay"] is False, "instance_recovery_admission_binding")
                if SYNTAX:
                    import mainline_a_schema_v3_syntax_20261102 as syntax_support
                    syntax_support.verify_marker(sys.modules[__name__], marker, ready["binding"])
                elif CANDIDATE:
                    import mainline_a_schema_v3_candidate_20261102 as candidate_support
                    candidate_support.verify_marker(sys.modules[__name__], marker, ready["binding"])
                elif COVERAGE:
                    import mainline_a_schema_v3_coverage_20261102 as coverage_support
                    coverage_support.verify_marker(sys.modules[__name__], marker, ready["binding"])
                if MINIMAL:
                    require(marker.get("previous_extended_request_id")
                            == INSTANCE + "-live-extended-20261101T233105Z"
                            and type(marker.get("process_seconds")) is int
                            and marker["process_seconds"] == PROCESS_SECONDS
                            and marker.get("previous_extended_marker_sha256")
                            == ready["binding"]["previous_extended_marker_sha256"]
                            and marker.get("previous_extended_receipt_sha256")
                            == ready["binding"]["previous_extended_receipt_sha256"]
                            and marker.get("previous_extended_postmortem_sha256")
                            == ready["binding"]["previous_extended_postmortem_sha256"]
                            and marker.get("generation_controls") == {"temperature": 0.2, "max_tokens": 3072}
                            and marker.get("retryable_reasons") == ["upstream_stream_error", "upstream_timeout"],
                            "minimal_previous_link")
                if DIAGNOSTIC or EXTENDED:
                    require(marker["previous_recovery_request_id"] == INSTANCE + "-live-recovery-20261101",
                            "diagnostic_previous_link")
                if EXTENDED:
                    require(marker["previous_diagnostic_request_id"]
                            == INSTANCE + "-live-diagnostic-20261101T133705Z"
                            and type(marker.get("process_seconds")) is int
                            and marker["process_seconds"] == PROCESS_SECONDS
                            and marker["previous_diagnostic_receipt_sha256"]
                            == ready["binding"]["previous_diagnostic_receipt_sha256"]
                            and marker["previous_postmortem_sha256"]
                            == ready["binding"]["previous_postmortem_sha256"], "extended_previous_link")
                receipt["assets"]["admission"] = save_or_verify_asset(
                    receipt, "admission", LIVE + ".admission.json", marker_raw, guard)
            except Exception as error:
                receipt["status"] = "blocked"
                receipt["failures"].append(type(error).__name__ + ":" + str(error)[:240])
        receipt["listener_after"] = listener()
        if "ready" in locals() and receipt["listener_after"] != ready["listener_inodes"]:
            receipt["status"] = "blocked"
            receipt["failures"].append("listener_changed_during_live")
        elapsed = time.monotonic() - guard.started
        receipt.update(total_before_final_receipt_seconds=elapsed, soft_target_met=elapsed <= 180,
                       soft_overrun_seconds=max(0, elapsed - 180),
                       soft_behavior="observation_only_no_exception",
                       outer_process_exit="requires_actual_client_command_receipt")
        evidence = save(LIVE + ".receipt.json", receipt, guard)
        print(json.dumps({"status": receipt["status"], "receipt": evidence,
            "failures": receipt["failures"], "physical_limits": receipt.get("physical_limits"),
            "service_exit_code": receipt.get("service_exit_code"),
            "service_cleanup": receipt.get("service_cleanup"),
            "elapsed_seconds": time.monotonic() - guard.started,
            "soft_target_met": receipt["soft_target_met"]}, ensure_ascii=False), flush=True)
    return 0 if receipt["status"] == "online_transport_saved_trusted_consumption_pending" else 2


def main():
    parser = argparse.ArgumentParser(description="显式选择准入复核或唯一在线；不自动串联")
    parser.add_argument("phase", choices=["prepare", "live"])
    namespace = parser.add_mutually_exclusive_group()
    namespace.add_argument("--diagnostic", action="store_true",
                           help="选择2026-11-01T13:37:05Z明确授权的历史诊断恢复命名空间")
    namespace.add_argument("--extended", action="store_true",
                           help="选择明确授权的一次新600秒恢复；不复活旧窗口")
    namespace.add_argument("--minimal", action="store_true",
                           help="选择2026-11-02获准的单网关微创恢复；不复活旧窗口")
    namespace.add_argument("--coverage", action="store_true",
                           help="选择获准的新选择覆盖恢复；不复活旧窗口或继承旧验收")
    namespace.add_argument("--candidate", action="store_true",
                           help="选择获准的整份候选恢复；共用传输配额与期限，不复活旧窗口")
    namespace.add_argument("--syntax", action="store_true",
                           help="选择唯一新语法恢复窗口；全部允许错误共用三次重试及绝对期限")
    arguments = parser.parse_args()
    if arguments.syntax:
        configure_syntax()
    elif arguments.candidate:
        configure_candidate()
    elif arguments.coverage:
        configure_coverage()
    elif arguments.minimal:
        configure_minimal()
    elif arguments.extended:
        configure_extended()
    elif arguments.diagnostic:
        configure_diagnostic()
    return prepare() if arguments.phase == "prepare" else live()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__,
                          "reason": str(error)[:256]}), file=sys.stderr, flush=True)
        raise SystemExit(2)
