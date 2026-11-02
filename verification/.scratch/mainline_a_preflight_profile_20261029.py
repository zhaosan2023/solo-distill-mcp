"""获准离线性能定位：定位 30s 冷预检阻塞阶段，不发 POST、不执行或写入目标。
环境前提为既有依赖、原配置与当前源快照；验收为有界阶段耗时及超时栈，
本诊断不替代预检门禁、不继承通过状态、不占用三次冷预检样本目录。
"""
from __future__ import annotations
import ast
import faulthandler
import threading
import time
import mainline_a_scouting_20261029 as driver
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v2 import consume_frame
from solo_distill.core_v2 import form_core
from solo_distill.evidence_v2 import V2EvidenceReader
from solo_distill.extractor import Scanner
from solo_distill.live_v2 import LiveV2Distiller, core_selection, select_inventory
from solo_distill.scouting_v2 import select_prompt
from solo_distill.snapshot_v2 import parsed_tree, snapshot_scope
from solo_distill.syntax_v2 import extract_units
from solo_distill.wire_v2 import publish, encode_report, core_report
from adaptive_receiver import receive

@snapshot_scope
def main():
    started = time.monotonic()
    faulthandler.dump_traceback_later(8, repeat=True)
    stages = {}
    directory = 'verification/mainline-a-scouting-v4-20261029T153709Z-capacity-blocked'
    def stage(name, action):
        print('stage_start=' + name, flush=True)
        checkpoint = time.monotonic()
        result = action()
        stages[name] = round(time.monotonic()-checkpoint, 6)
        print('stage_end=' + name + ' seconds=' + str(stages[name]), flush=True)
        return result
    try:
        projects, policy = driver.historical.configs()
        args = driver.arguments('mainline-a-offline-profile')
        project, include, _, admission = LiveV2Distiller(projects, policy).validate_v2(args, 3)
        scanner = Scanner(project, include, project.max_seconds)
        scanner.deadline = started + 27
        structure = stage('scan', scanner.run)
        print('parsed_files=' + str(structure['coverage']['parsed_files']), flush=True)
        reader = V2EvidenceReader(scanner, structure, started+27, threading.Event(), budget_policy=driver.POLICY)
        question = args['question'] + ' ' + ' '.join(d['question'] for d in args['dimensions'])
        package, _ = stage('inventory_static', lambda: select_inventory(reader, question, budget_policy=driver.POLICY))
        core = stage('core_authentication', lambda: form_core(package, reader.raw_files, **core_selection(package, question, budget_policy=driver.POLICY)))
        package = core.document['package']
        _, post, selected = stage('question_view', lambda: select_prompt(package, reader.raw_files, args['question'], args['dimensions'], structure['coverage'], tick=reader.tick))
        print('post_bytes=' + str(len(post)) + ' body_bytes=' + str(selected['unique_body_bytes']), flush=True)
        encoded = stage('exact_static_wire_encoding', lambda: encode_report(core_report(core, {}), admission.request_id, wire_encoding=core.wire_encoding))
        caps = admission.transport
        # The existing transport base64-encodes this complete original wire frame.
        # This is a necessary lower bound, not a fabricated successful transfer.
        base64_minimum = 4 * ((len(encoded) + 2) // 3)
        print('static_core_bytes=' + str(len(core.payload)) + ' static_decoded_wire_bytes=' + str(len(encoded)) + ' base64_payload_lower_bound=' + str(base64_minimum), flush=True)
        if len(encoded) > caps.max_decoded_bytes or base64_minimum > caps.max_transfer_bytes:
            stage('source_reverification', reader.verify)
            evidence = []
            def persist():
                evidence.extend([
                    driver.save('structure.json', structure, directory),
                    driver.save('sources.json', {p: __import__('base64').b64encode(raw).decode('ascii') for p, raw in reader.raw_files.items()}, directory),
                    driver.save('static-core.json', core.payload, directory),
                    driver.save('static-decoded.stdio.jsonl', encoded, directory),
                    driver.save('http-post.json', post, directory),
                    driver.save('selection.receipt.json', selected, directory),
                ])
            stage('evidence_save_and_exact_readback', persist)
            if time.monotonic() - started >= 27:
                raise ValueError('diagnostic_internal_deadline_not_preflight_pass')
            receipt = {
                'phase': 'capacity_diagnostic', 'status': 'blocked', 'driver_exit_code': 2,
                'command': 'PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_preflight_profile_20261029.py </dev/null',
                'cwd': str(driver.ROOT), 'plan_path': driver.PLAN, 'contract_version': 4,
                'implementation_sha256': driver.implementation(),
                'snapshot_id': package['snapshot_id'], 'coverage': structure['coverage'],
                'registry_counts': {name: len(package[name]) for name in ('files', 'units', 'owners', 'sources', 'conditions', 'role_proofs', 'facts', 'relations', 'obligations')},
                'static_core_bytes': len(core.payload), 'static_decoded_wire_bytes': len(encoded),
                'max_decoded_bytes': caps.max_decoded_bytes,
                'base64_payload_lower_bound': base64_minimum, 'max_transfer_bytes': caps.max_transfer_bytes,
                'max_frame_bytes': caps.max_frame_bytes,
                'decoded_limit_exceeded': len(encoded) > caps.max_decoded_bytes,
                'transfer_lower_bound_exceeded': base64_minimum > caps.max_transfer_bytes,
                'post_bytes': len(post), 'post_limit': 150000, 'prompt_body_bytes': selected['unique_body_bytes'], 'body_limit': 98304,
                'stages_seconds': stages, 'elapsed_seconds_before_receipt_save': round(time.monotonic()-started, 6),
                'freshness': 'source_reverified_before_evidence_save', 'evidence': evidence,
                'generation_requests': 0, 'outer_replays': 0, 'target_write_operations': 0,
                'target_execution_operations': 0, 'gateway_restart_operations': 0,
                'preflight_pass': False, 'transport_published': False,
                'four_dimension_semantic_acceptance': 'not_run',
                'controller_coverage': 'not_proven_platform_task_record_used',
                'failures': ['complete_static_wire_exceeds_approved_transport_capacity'],
            }
            location = driver.save('capacity-diagnostic.receipt.json', receipt, directory)
            print(__import__('json').dumps({'status': 'blocked', 'driver_exit_code': 2, 'evidence': location, 'static_decoded_wire_bytes': len(encoded), 'base64_payload_lower_bound': base64_minimum, 'post_bytes': len(post)}, ensure_ascii=False), flush=True)
            return 2
        definitions, calls = set(), set()
        for path, raw in reader.raw_files.items():
            definitions.update(u['qualified_name'] for u in extract_units(path, raw)['units'] if u['kind']=='definition')
            calls.update(ast.unparse(n.func) for n in ast.walk(parsed_tree(raw, path)) if isinstance(n, ast.Call))
        frame = stage('publish', lambda: publish(core, admission))
        stage('production_consumer', lambda: consume_frame(frame.data, reader.raw_files, protocol_version='2024-11-05', request_id=3, scenario='A', return_bytes=22016, project_id=project.project_id, policy_id=driver.POLICY, transport=TransportCapabilities.from_dict(driver.CAPS)))
        stage('independent_consumer', lambda: receive(frame.data, reader.raw_files, driver.CAPS, request_id=3, project_id=project.project_id, expected_definitions=definitions, expected_calls=calls))
        stage('source_reverification', reader.verify)
        print('diagnostic_completed_not_preflight_pass seconds=' + str(round(time.monotonic()-started, 6)), flush=True)
        return 0
    finally:
        faulthandler.cancel_dump_traceback_later()

if __name__ == '__main__':
    raise SystemExit(main())
