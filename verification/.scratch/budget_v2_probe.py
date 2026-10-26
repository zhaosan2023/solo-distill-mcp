"""Bounded E5 offline probe: inventory/closure wire cost, no model or target execution.
Question: does the authenticated inventory plus one complete leaf fit A?
Scope: configured FastCode Python data and existing E5 trace; no writes to target.
Prerequisites: existing snapshot hashes and standard-library-only environment.
Acceptance: measured full reserved frame sizes, nonempty sources, unchanged hashes.
"""
import hashlib
import json
import os
import time
from pathlib import Path

from solo_distill.core_v2 import make_seal
from solo_distill.extractor import Scanner, load_projects, open_root
from solo_distill.package_v2 import build_package
from solo_distill.syntax_v2 import extract_units
from solo_distill.wire_v2 import reserved_size

started = time.monotonic()
trace = json.loads(Path('verification/.scratch/live-v2-a-1792991436455.full.json').read_bytes())
project = load_projects('config/projects.json')['fastcode-sample']
scanner = Scanner(project, ('.',), 25)
raw_files = {}
fd = open_root(project.root)
try:
    for entry in trace['structure']['entries']:
        if entry['status'] != 'parsed':
            continue
        raw, _ = scanner.read_bytes(fd, entry['path'], project.max_file_bytes)
        if hashlib.sha256(raw).hexdigest() != entry['content_sha256']:
            raise ValueError('source_snapshot_changed')
        raw_files[entry['path']] = raw
finally:
    os.close(fd)
units = [(path, unit) for path, raw in raw_files.items()
         for unit in extract_units(path, raw)['units']]
leaf = min(units, key=lambda item: (len(item[1]['conditions']),
           item[1]['raw_end'] - item[1]['raw_start'], item[0], item[1]['raw_start']))
package = build_package(trace['arguments']['project_id'], trace['structure']['snapshot_id'],
                        raw_files, [leaf[1]['id']])
core = make_seal(package, raw_files, 'lifecycle-v2-1')
print(json.dumps({'question': 'inventory_plus_one_complete_leaf_fits_A',
                  'parsed_files': len(raw_files), 'units': len(units),
                  'leaf_path': leaf[0], 'leaf_bytes': leaf[1]['raw_end'] - leaf[1]['raw_start'],
                  'file_table_json_bytes': len(json.dumps(package['files'], separators=(',', ':')).encode()),
                  'scope_json_bytes': len(json.dumps(sorted(raw_files), separators=(',', ':')).encode()),
                  'reserved_frame_bytes': reserved_size(core), 'limit': 22016,
                  'elapsed_seconds': round(time.monotonic() - started, 3)}))
