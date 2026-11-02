"""Read-only sizing experiment: five fixed files, no imports of target code.

Question: do all observed definitions/imports fit after lossless provenance
factoring? Compare actual JSON-RPC UTF-8 frames, not an assumed 15KB estimate.
No target writes; Python -B and Scanner's descriptor/hash verification required.
"""
import copy
import json
from solo_distill.extractor import Scanner, load_projects
from solo_distill.server import encode, tool_response

SELECTED = ['api.py', 'fastcode/__init__.py', 'fastcode/agent_tools.py',
            'fastcode/answer_generator.py', 'fastcode/cache.py']
projects = load_projects('config/projects.json')
report = Scanner(projects['fastcode-sample'], tuple(SELECTED), 5).run()
if report['status'] != 'complete':
    raise SystemExit('BLOCKED: scan not complete')
core = copy.deepcopy(report)
for entry in core['entries']:
    if entry['status'] == 'parsed':
        entry['calls'] = []
columns = {
    'symbols': ['source_id', 'start_line', 'end_line', 'kind', 'qualified_name', 'bases', 'conditions'],
    'imports': ['source_id', 'start_line', 'end_line', 'module', 'name', 'alias', 'level', 'scope', 'conditions'],
}
compact = copy.deepcopy(core)
compact['record_encoding'] = {'version': 1, 'columns': columns,
    'inherit': ['path', 'content_sha256'], 'evidence_level': 'direct_static',
    'symbol_name': 'last qualified_name component', 'conditions': 'entry condition table indexes'}
for entry in compact['entries']:
    if entry['status'] != 'parsed':
        continue
    conditions = []
    for kind, fields in columns.items():
        rows = []
        for record in entry[kind]:
            refs = []
            for condition in record['conditions']:
                if condition not in conditions:
                    conditions.append(condition)
                refs.append(conditions.index(condition))
            rows.append([refs if field == 'conditions' else record.get(field, [] if field == 'bases' else None)
                         for field in fields])
        entry[kind] = rows
    entry['condition_table'] = conditions
    entry.pop('calls', None)
def wire(value):
    return len(encode(tool_response(value, 3))) + 1
summary = {'status': 'PASS', 'experiment': 'lossless_core_size',
    'snapshot_id': report['snapshot_id'], 'coverage': report['coverage'],
    'raw_full_wire_bytes': wire(report), 'legacy_core_wire_bytes': wire(core),
    'table_core_wire_bytes': wire(compact), 'hard_limit_bytes': 24576,
    'definition_levels': {}, 'by_file': [], 'observations': report['observations']}
for entry in report['entries']:
    if entry['status'] != 'parsed':
        continue
    for record in entry['symbols']:
        level = 'top_level' if '.' not in record['qualified_name'] else 'nested_or_method'
        summary['definition_levels'][level] = summary['definition_levels'].get(level, 0) + 1
    summary['by_file'].append({'path': entry['path'], 'symbols': len(entry['symbols']),
                              'imports': len(entry['imports']), 'calls': len(entry['calls'])})
print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
