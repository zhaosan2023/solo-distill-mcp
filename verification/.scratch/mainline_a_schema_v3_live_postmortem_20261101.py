"""阶段 5 失败现场的有界离线对账；不联网、不重发、不修改历史资产。

问题：唯一在线失败是否来自期限、物理容量或已保存的上游错误。
范围：仅本实例保存资产、接纳标记、回执和独立传输重组。
前提：原在线进程实际退出 2，服务自然退出 0，禁止重新接纳。
验收：返回真实错误、资产原字节复读、容量与证据缺口；不登记业务通过。
"""
import hashlib
import json
from pathlib import Path

from schema_v3_wire_oracle import reassemble

BASE = Path('verification/mainline-a-schema-v3-20261030T095713Z')
RECEIPT = BASE / 'phase5-live-20261101.receipt.json'


def main():
    receipt = json.loads(RECEIPT.read_bytes())
    assert receipt['status'] == 'blocked'
    assets = {}
    for name, evidence in receipt['assets'].items():
        raw = Path(evidence['path']).read_bytes()
        actual = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        assert actual['bytes'] == evidence['bytes']
        assert actual['sha256'] == evidence['sha256']
        assets[name] = actual
    trace = json.loads(Path(receipt['assets']['trace']['path']).read_bytes())
    attempts = trace['gateway_requests']
    assert len(attempts) == 1
    observation = {k: v for k, v in attempts[0].items() if k != 'raw_sse_base64'}
    marker = json.loads(Path(receipt['assets']['admission']['path']).read_bytes())
    assert marker['request_id'] == receipt['request_id']
    assert marker['automatic_replay'] is False
    ready = json.loads((BASE / 'phase5-online-ready-20261101.json').read_bytes())
    wire = Path(receipt['assets']['stdio']['path']).read_bytes()
    decoded, manifest = reassemble(wire, ready['wire_arguments']['transport'],
        response_id=3, return_bytes=22016, wire_encoding='lz-json-v3-1')
    limits = {'post_bytes': assets['post']['bytes'],
        'body_utf8_bytes': ready['body_utf8_bytes'], 'decoded_bytes': len(decoded),
        'transfer_bytes': len(wire), 'max_frame_bytes': max(map(len, wire.splitlines(keepends=True))),
        'request_bytes_including_lf': assets['request_wire']['bytes'],
        'physical_frames': wire.count(b'\n'),
        'notification_chunks': 0 if manifest is None else manifest['manifest']['count']}
    ceilings = {'post_bytes': 150000, 'body_utf8_bytes': 98304,
        'decoded_bytes': 1048576, 'transfer_bytes': 1572864,
        'max_frame_bytes': 22016, 'request_bytes_including_lf': 65536,
        'notification_chunks': 512}
    capacity = all(limits[key] <= ceiling for key, ceiling in ceilings.items())
    delivery = json.loads(Path(receipt['assets']['delivery']['path']).read_bytes())
    assert delivery['written'] == len(wire)
    assert delivery['frame_sha256'] == assets['stdio']['sha256']
    sse = Path(receipt['assets']['sse']['path']).read_bytes()
    assert sse.startswith(b'data: ')
    event = json.loads(sse[6:].strip())
    report = {'diagnostic_status': 'saved_failure_evidence_reconciled_not_business_pass',
        'execution_instance': receipt['execution_instance'],
        'actual_online_command_result_id': 'call_5333cce8d2784d77b596e7db',
        'actual_online_outer_exit_code': 2, 'current_task_state': 'BLOCKED',
        'online_receipt_path': str(RECEIPT), 'original_failures': receipt['failures'],
        'gateway_observation': observation, 'saved_sse_event': event,
        'trace_failures': trace['failures'], 'model_output_count': len(trace['model_outputs']),
        'physical_measurements': limits, 'physical_capacity_pass': capacity,
        'asset_original_bytes_readback': assets,
        'service_exit_code': receipt['service_exit_code'], 'service_cleanup': receipt['service_cleanup'],
        'total_before_final_receipt_seconds': receipt['total_before_final_receipt_seconds'],
        'soft_target_met': receipt['soft_target_met'], 'soft_overrun_seconds': receipt['soft_overrun_seconds'],
        'listener_inodes_unchanged': receipt['listener_after'] == ready['listener_inodes'],
        'unique_admission_consumed': True, 'automatic_replay': False,
        'network_operations': 0, 'cold_preflight_runs': 0, 'prior_test_runs': 0,
        'writes_by_diagnostic': 0, 'upstream_root_cause': 'not_established_by_saved_gateway_error',
        'not_proven': ['successful_model_generation', 'successful_live_business',
            'independent_trusted_dual_consumption', 'four_dimension_and_13_anchor_acceptance',
            'real_human_semantic_review', 'governance_delivery', 'Roo_host_loading',
            'ADR_acceptance', 'plan_completion_and_archival']}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
