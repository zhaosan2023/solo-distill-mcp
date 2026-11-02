"""获准主线 A 收尾：独立治理导出、最终对账和独占归档；不联网、不新增测试。

仅消费已保存的语法窗口和真实客户端退出回执。人工必需门禁由当前用户明确替代，
人工及宿主未证明标记保持；旧证据、来源、主线 B、生产模块和 ADR 修订保持。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

import mainline_a_schema_v3_online_20261101 as support
import mainline_a_schema_v3_syntax_acceptance_20261102 as acceptance
import mainline_a_schema_v3_syntax_20261102 as syntax_support
import mainline_a_schema_v3_coverage_20261102 as coverage
from mainline_a_schema_v3_minimal_acceptance_20261102 import checked_asset
from solo_distill.brownfield_v3 import build_blueprint_v3, export_blueprint_v3, validate_blueprint_v3
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.extractor import load_projects
from solo_distill.macro_v3 import canonical
from solo_distill.snapshot_v2 import snapshot_scope
from schema_v3_snapshot_oracle import oracle_scope
from solo_distill.wire_v3 import Admission

ROOT = Path.cwd()
PARENT = support.PARENT
PREFIX = "phase6-7-closeout-20261102"
ADR = "_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md"
CONSUME_SHA = "4320d3e2339c621ebe369a5031f95c0ff4649345b81c36f28fdf6867d49f923e"
RECONCILE_SHA = "49fb71e618647f568653f319bd72ea902cbe842dcc0d84340b55c5bd146d8928"
CONSUME_EXIT = "call_c802a90c8a9f8967572e4ea4"
RECONCILE_EXIT = "call_ef61bd74405f787c006a8698"


def require(condition, reason):
    if not condition:
        raise ValueError("closeout_" + reason)


def load(path):
    return json.loads(support.read(path))


def asset(path):
    raw = support.read(path)
    return {"path": path, "bytes": len(raw), "sha256": support.digest(raw), "exact_readback": True}


def snapshot_binding():
    contract = load(support.CONTRACT)
    plan = support.read(support.PLAN).decode("utf-8")
    require(contract["contract_revision"] == 18 and contract["adr_revision"] == 16
            and contract["plan_contract_version"] == 6
            and contract["execution_instance"] == support.INSTANCE
            and contract["session_id"] is None, "current_contract")
    override = contract["closing_acceptance_override"]
    require(override["human_review_required"] is False
            and override["human_semantic_review"] == "not_performed_not_proven"
            and override["prior_evidence_contract_revision"] == 17
            and override["consume_process_seconds"] == override["reconciliation_process_seconds"] == 90
            and "明确替代人工必需门禁" in override["authorization"], "acceptance_authorization")
    for line in ("plan_id: mainline-a-v2-scenario-a-20261029", "contract_version: 6",
                 "adr_revision: 16", "session_id: null", "execution_instance: " + support.INSTANCE):
        require(plan.splitlines().count(line) == 1, "plan_identity")
    require("closing_acceptance_authorization:" in plan and "## 当前收尾验收替代契约" in plan,
            "plan_override")
    return {"plan": asset(support.PLAN), "contract": asset(support.CONTRACT), "adr": asset(ADR),
            "contract_revision": 18, "adr_revision": 16, "plan_contract_version": 6, "session_id": None}


def inputs(guard):
    live_raw = support.read(PARENT + "/" + support.LIVE + ".receipt.json")
    require(support.digest(live_raw) == acceptance.LIVE_RECEIPT_SHA256, "live_receipt_changed")
    live = json.loads(live_raw)
    consume_path = PARENT + "/" + acceptance.PREFIX + ".consume.receipt.json"
    reconcile_path = PARENT + "/" + acceptance.PREFIX + ".reconciliation.receipt.json"
    require(asset(consume_path)["sha256"] == CONSUME_SHA
            and asset(reconcile_path)["sha256"] == RECONCILE_SHA, "actual_consumption_receipts_changed")
    consumed, reconciled = load(consume_path), load(reconcile_path)
    require(live["binding"] == consumed["binding"] == reconciled["binding"]
            and live["binding"]["execution_contract_revision"] == 17
            and live["binding"]["adr_revision"] == 16
            and consumed["production_and_independent_publication_equal"] is True
            and consumed["task_status"] == "complete"
            and reconciled["anchors_located"] == reconciled["required_anchors"] == 13
            and reconciled["missing_anchor_ids"] == []
            and len(reconciled["four_dimensions"]) == 4
            and all(d["structural_state"] == "satisfied" for d in reconciled["four_dimensions"]),
            "required_dual_or_anchor_gate")
    require(live["service_exit_code"] == 0 and live["service_cleanup"] == "confirmed_natural_exit"
            and live["failures"] == [] and live["actual_http_attempts"] == 3,
            "saved_online_exit")
    for key, path in (("projects_sha256", "config/projects.json"), ("policy_sha256", "config/distillation.json")):
        require(support.digest(support.read(path)) == live["binding"][key], "source_configuration_changed")
    assets = {key: checked_asset(value) for key, value in live["assets"].items()}
    publication = json.loads(checked_asset(consumed["assets"]["publication"]))
    require(publication["core"] == json.loads(checked_asset(consumed["assets"]["core"]))
            and publication["core"]["package"] == json.loads(checked_asset(consumed["assets"]["package"])),
            "publication_assets")
    review = json.loads(checked_asset(reconciled["review_inputs"]))
    require(review["human_semantic_review"] == "pending"
            and review["reviewer_kind"] == "not_recorded"
            and len(review["conclusions"]) == reconciled["conclusions"] == 18
            and all(a["body_coverage"] for a in reconciled["anchor_checks"]), "review_not_forged")
    require(support.digest(support.read(review["frozen_anchor_path"])) == review["frozen_anchor_sha256"],
            "reference_anchors_changed")
    frozen = load(PARENT + "/" + support.FROZEN_SAMPLE + "/frozen-source-and-selection.json")
    raw_files = support.current_source(frozen, guard)
    require(len(raw_files) == consumed["source_files"] == 81, "source_denominator")
    trusted = {"project_id": frozen["project_id"], "snapshot_id": frozen["report"]["snapshot_id"],
               "required_core_unit_ids": frozen["selection"]["core_unit_ids"],
               "selection_id": frozen["selection"]["selection_id"], "selection_version": acceptance.VERSION,
               "request_id": syntax_support.REQUEST, "question": frozen["question"],
               "dimensions": frozen["dimensions"], "post": assets["post"],
               "frozen_coverage": frozen["report"]["coverage"], "failures": [], "tick": guard.check}
    evidence = {"live": asset(PARENT + "/" + support.LIVE + ".receipt.json"),
                "consume": asset(consume_path), "reconcile": asset(reconcile_path),
                "review_inputs": reconciled["review_inputs"],
                "client_exits": [{"tool_result": CONSUME_EXIT, "exit_code": 0},
                                 {"tool_result": RECONCILE_EXIT, "exit_code": 0}],
                "prior_binding": live["binding"]}
    return live, reconciled, frozen, raw_files, trusted, assets, publication, evidence


@snapshot_scope
@oracle_scope
def governance(guard):
    binding = snapshot_binding()
    require(support.digest(support.read(ADR)) == load(PARENT + "/" + support.LIVE + ".receipt.json")["binding"]["adr_sha256"],
            "adr_changed_before_acceptance")
    live, reconciled, frozen, raw_files, trusted, assets, publication, evidence = inputs(guard)
    project = load_projects("config/projects.json")[frozen["project_id"]]
    admission = Admission("2024-11-05", "A", 3, 22016,
                          TransportCapabilities.from_dict(support.CAPS), wire_encoding="lz-json-v3-1")
    bundle = build_blueprint_v3(assets["stdio"], raw_files, artifact_root=ROOT,
                               source_root=project.root, admission=admission,
                               candidate=json.loads(assets["candidate"]), **trusted)
    require(json.loads(bundle.documents["verification/schema-v3/publication.json"]) == publication,
            "blueprint_publication_conflict")
    # 生成器的默认修订2/3是历史模板；当前导出仅校正候选溯源，不修改旧API或生产模块。
    provenance_path = "verification/schema-v3/provenance.json"
    provenance = json.loads(bundle.documents[provenance_path])
    provenance.update(execution_contract_revision=18, adr_revision=16, adr_state="PROPOSED",
                      human_review_required=False, human_semantic_review="not_performed_not_proven",
                      acceptance_basis="explicit_user_override_dual_consumption_four_dimensions_13_anchors",
                      evidence_contract_revision=17,
                      task_binding_scope="independent_source_blueprint_no_source_contribution_task_bound")
    bundle.documents[provenance_path] = canonical(provenance) + b"\n"
    bundle.report["provenance_sha256"] = support.digest(bundle.documents[provenance_path])
    validate_blueprint_v3(bundle)
    manifest = export_blueprint_v3(bundle, ROOT, tick=guard.check)
    require(snapshot_binding() == binding, "binding_changed_during_export")
    manifest_path = str(Path(bundle.output_root).relative_to(ROOT) / "manifest.json")
    require(load(manifest_path) == manifest, "manifest_readback")
    summary = [{"id": a["id"], "body_coverage": a["body_coverage"],
                "conclusion_indices": a["conclusion_indices"]} for a in reconciled["anchor_checks"]]
    result = {"status": "governance_delivery_pass_under_explicit_acceptance_override",
              "execution_instance": support.INSTANCE, "binding": binding, "evidence": evidence,
              "manifest": asset(manifest_path), "anchors": summary, "conclusions": 18,
              "four_dimensions": 4, "agents_lines": len(bundle.documents["AGENTS.md"].splitlines()),
              "max_markdown_bytes": max(len(v) for k, v in bundle.documents.items() if k.endswith(".md")),
              "bundle_bytes": manifest["document_bytes"] + len(support.read(manifest_path)),
              "files": len(manifest["files"]), "source_writes": 0, "network_calls": 0,
              "human_semantic_review": "not_performed_not_proven", "host_loading": "not_proven",
              "outer_process_exit": "requires_actual_client_command_receipt"}
    receipt = support.save(PREFIX + ".governance.receipt.json", result, guard)
    print(json.dumps({k: result[k] for k in ("status", "anchors", "agents_lines", "max_markdown_bytes",
          "bundle_bytes", "files", "human_semantic_review")} | {"receipt": receipt}, ensure_ascii=False), flush=True)


def final(guard, previous_exit):
    require(bool(previous_exit), "governance_client_exit_missing")
    binding = snapshot_binding()
    live, reconciled, frozen, raw_files, trusted, assets, publication, evidence = inputs(guard)
    governance_path = PARENT + "/" + PREFIX + ".governance.receipt.json"
    prior = load(governance_path)
    require(prior["binding"] == binding and prior["status"] == "governance_delivery_pass_under_explicit_acceptance_override",
            "governance_gate")
    require(asset(prior["manifest"]["path"]) == prior["manifest"], "blueprint_manifest_changed")
    manifest = load(prior["manifest"]["path"])
    output = str(Path(prior["manifest"]["path"]).parent)
    total = 0
    for path, digest in manifest["files"].items():
        guard.check()
        raw = support.read(output + "/" + path)
        require(support.digest(raw) == digest, "blueprint_file_changed:" + path)
        total += len(raw)
    require(total == manifest["document_bytes"] and total + prior["manifest"]["bytes"] <= 8388608
            and manifest["source_writes"] == 0 and manifest["exact_readback"] is True, "blueprint_budget")
    regressions = []
    for path in syntax_support.REGRESSIONS:
        receipt = load(path)
        syntax_support.require_regression(support, receipt)
        regressions.append(asset(path))
    phase23_path = PARENT + "/phase2-3-offline-reconciliation-20261030T1908Z.json"
    phase2_path = PARENT + "/phase2-implemented-assets-tests.receipt.json"
    phase23, phase2 = load(phase23_path), load(phase2_path)
    require(phase23["execution_instance"] == phase2["execution_instance"] == support.INSTANCE
            and phase2["exit_code"] == phase2["failures"] == phase2["errors"] == phase2["skipped"] == 0
            and phase2["tests_run"] > 0, "prior_offline_gate")
    samples = []
    for sample in coverage.SAMPLES:
        receipt, saved, sample_frozen = coverage.sample_assets(support, sample, guard)
        audit_path = PARENT + "/" + sample + ".phase5-audit.receipt.json"
        audit = load(audit_path)
        require(audit["status"] == "pass" and audit["actual_exit_code"] == 0
                and bool(audit["actual_exit_tool_result"]) and audit["artifact_readback"] == saved
                and sample_frozen["raw_files_base64"] == frozen["raw_files_base64"]
                and sample_frozen["selection"] == frozen["selection"], "cold_sample_evidence")
        samples.append({"sample": sample, "audit": asset(audit_path),
                        "actual_exit_tool_result": audit["actual_exit_tool_result"], "exit_code": 0})
    require(snapshot_binding() == binding, "binding_changed_during_final")
    result = {"status": "all_current_required_gates_pass_ready_for_authorized_promotion_and_archive",
              "execution_instance": support.INSTANCE, "binding": binding, "evidence": evidence,
              "governance": asset(governance_path), "governance_client_exit": {"tool_result": previous_exit, "exit_code": 0},
              "phase2_3_evidence": [asset(phase23_path), asset(phase2_path)],
              "syntax_regressions": regressions, "cold_samples": samples,
              "required_anchors": 13, "anchors_covered": 13, "four_dimensions": 4, "conclusions": 18,
              "human_semantic_review": "not_performed_not_proven", "human_review_required": False,
              "host_loading": "not_proven_not_in_current_scope", "source_installation": "not_performed",
              "network_calls": 0, "business_retested": False, "source_writes": 0,
              "prior_failures": "retained_not_reclassified", "outer_process_exit": "requires_actual_client_command_receipt"}
    receipt = support.save(PREFIX + ".final-reconciliation.receipt.json", result, guard)
    print(json.dumps({"status": result["status"], "anchors": "13/13", "cold_samples": len(samples),
                      "human_semantic_review": result["human_semantic_review"], "receipt": receipt}, ensure_ascii=False), flush=True)


def retire(guard, previous_exit, archive_target=None):
    require(bool(previous_exit), "final_client_exit_missing")
    final_path = PARENT + "/" + PREFIX + ".final-reconciliation.receipt.json"
    result = load(final_path)
    require(result["status"] == "all_current_required_gates_pass_ready_for_authorized_promotion_and_archive", "final_gate")
    plan = support.read(support.PLAN)
    adr = support.read(ADR)
    require(plan.count(b"\nstatus: COMPLETED\n") == 1
            and support.digest(plan.replace(b"\nstatus: COMPLETED\n", b"\nstatus: EXECUTING\n", 1))
                == result["binding"]["plan"]["sha256"], "completed_plan_unrelated_change")
    require(adr.count(b"\nstatus: ACCEPTED\n") == 1
            and support.digest(adr.replace(b"\nstatus: ACCEPTED\n", b"\nstatus: PROPOSED\n", 1))
                == result["binding"]["adr"]["sha256"], "accepted_adr_unrelated_change")
    require(asset(support.CONTRACT) == result["binding"]["contract"], "contract_changed_before_archive")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = archive_target or "_bmad-output/archive/plans/" + stamp + "-mainline-a-v2-scenario-a-20261029.md"
    require(str(Path(target).parent) == "_bmad-output/archive/plans"
            and Path(target).name.endswith("-mainline-a-v2-scenario-a-20261029.md"), "archive_target")
    time.strptime(Path(target).name[:16], "%Y%m%dT%H%M%SZ")
    parent = ROOT / "_bmad-output/archive/plans"
    require(parent.is_dir() and not parent.is_symlink(), "archive_parent")
    if archive_target is None:
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            guard.check()
            fd = os.open(Path(target).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, "wb") as stream:
                stream.write(plan)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(descriptor)
            require(support.read(target) == plan and support.read(support.PLAN) == plan,
                    "archive_exact_readback_source_changed")
        finally:
            os.close(descriptor)
        print(json.dumps({"status": "ARCHIVE_SAVED_ACTIVE_SOURCE_RETAINED_PENDING_NATIVE_READBACK",
                          "archive": asset(target)}, ensure_ascii=False), flush=True)
        return
    require(support.read(target) == plan, "archive_exact_readback")
    require(support.read(support.PLAN) == plan and support.read(ADR) == adr
            and asset(support.CONTRACT) == result["binding"]["contract"], "source_changed_before_retirement")
    guard.check()
    os.unlink(ROOT / support.PLAN)
    directory = os.open(ROOT / "_bmad-output", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    require(not os.path.lexists(ROOT / support.PLAN) and support.read(target) == plan,
            "retirement_readback")
    completed = {"status": "COMPLETED", "execution_instance": support.INSTANCE,
                 "archive": asset(target), "active_source_retired": True, "adr_status": "ACCEPTED",
                 "adr": asset(ADR), "final_reconciliation": asset(final_path),
                 "final_client_exit": {"tool_result": previous_exit, "exit_code": 0},
                 "human_semantic_review": "not_performed_not_proven", "host_loading": "not_proven",
                 "network_calls": 0, "source_writes": 0,
                 "outer_process_exit": "requires_actual_client_command_receipt"}
    receipt = support.save(PREFIX + ".completion.receipt.json", completed, guard)
    print(json.dumps(completed | {"receipt": receipt}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description="仅获准治理、最终对账和独占归档；禁止联网")
    parser.add_argument("phase", choices=("governance", "final", "retire"))
    parser.add_argument("--previous-exit-tool-result")
    parser.add_argument("--archive-target")
    arguments = parser.parse_args()
    support.configure_syntax()
    guard = support.Guard(90)
    with patch("socket.socket", side_effect=RuntimeError("closeout_network_forbidden")):
        if arguments.phase == "governance":
            governance(guard)
        elif arguments.phase == "final":
            final(guard, arguments.previous_exit_tool_result)
        else:
            retire(guard, arguments.previous_exit_tool_result, arguments.archive_target)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "error": type(error).__name__, "reason": str(error)[:300]},
                         ensure_ascii=False), flush=True)
        raise SystemExit(2)
