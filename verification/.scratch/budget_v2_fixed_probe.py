"""Authorized E5 offline preflight of the current A selection and wire boundary.
Question: does the repaired generic selection fit the complete reserved frame?
Scope: configured read-only sample, current scanner/reader and compact producer;
no model, no frozen evaluation reads, no sample import/execution or target write.
Prerequisites: current standard-library code and configured sample are available.
Acceptance: nonempty admitted sources, complete inventory and source verification,
reserved frame <=22016, actual publication/independent decoding match the core.
"""
import faulthandler
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path('tests').resolve()))
from live_v2_driver import request
from solo_distill.core_v2 import form_core
from solo_distill.evidence_v2 import V2EvidenceReader
from solo_distill.extractor import Scanner, load_projects
from solo_distill.live_v2 import core_selection, select_inventory
from solo_distill.wire_v2 import Admission, publish, reserved_size
from v2_consumer import receive

started = time.monotonic()
faulthandler.dump_traceback_later(20, repeat=False)

def stage(name):
    print(json.dumps({'stage': name, 'elapsed_seconds': round(time.monotonic() - started, 3),
                      'model_requests': 0, 'target_writes': 0}), flush=True)

stage('start')
arguments = request('offline-budget-preflight', int(time.time() * 1000))
question = arguments['question'] + ' ' + ' '.join(d['question'] for d in arguments['dimensions'])
project = load_projects('config/projects.json')[arguments['project_id']]
scanner = Scanner(project, tuple(arguments['include']), project.max_seconds)
scanner.deadline = started + 28
structure = scanner.run()
stage('scanner_complete')
if not structure['inventory_complete'] or structure['freshness'] != 'current_at_verification':
    raise ValueError('snapshot_not_ready')
reader = V2EvidenceReader(scanner, structure, started + 28, threading.Event())
package, selection = select_inventory(reader, question)
stage('selection_complete')
core = form_core(package, reader.raw_files, **core_selection(package, question))
stage('core_complete')
frame = publish(core, Admission('2024-11-05', 'A', 3, 22016))
reader.verify()
stage('source_verification_complete')
checked = receive(frame.data, reader.raw_files, request_id=3, scenario='A', return_bytes=22016,
                  project_id=arguments['project_id'], policy_id='lifecycle-v2-1')
faulthandler.cancel_dump_traceback_later()
stage('independent_decode_complete')
if not frame.knowledge_delivered or checked['core'] != core.document or not checked['package']['sources']:
    raise ValueError('preflight_not_delivered_or_decode_conflict')
print(json.dumps({'probe': 'current_A_structural_preflight_only',
    'files': len(reader.raw_files), 'scan_coverage': structure['coverage'],
    'selected_owners': len(selection.owners), 'selected_body_bytes': selection.body_bytes,
    'admitted_owners': len(checked['package']['owners']),
    'reserved_frame_bytes': reserved_size(core), 'actual_frame_bytes': len(frame.data),
    'core_identity': core.core_id, 'model_requests': 0, 'target_writes': 0,
    'sources': [{'path': checked['package']['files'][s['file']]['path'],
                 'symbol': checked['package']['units'][s['unit']]['qualified_name']}
                for s in checked['package']['sources']],
    'elapsed_seconds': round(time.monotonic() - started, 3)}, ensure_ascii=False))
