"""Screen canonical JSON prose for exact overlap with available primary text.

This limited, reproducible screen is not a legal clearance decision. It writes
only pointers/counts, never passages from the source documents.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from libstruct_bench.audit.hf_groundtruth import load, write
from libstruct_bench.audit.verify_groundtruth_upload import sha256
from libstruct_bench.libgen.staging import GROUNDTRUTH_FILENAMES


def strings(value, pointer=''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, pointer + '/' + key.replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from strings(item, pointer + '/' + str(i))
    elif isinstance(value, str):
        yield pointer, value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite existing screen')
    private = args.private_root.resolve()
    matches, coverage = [], []
    for directory in sorted((private / 'ground_truth').iterdir()):
        if not directory.is_dir():
            continue
        corpus, inputs = {}, []
        for f in sorted((private / 'ground_truth_audit/renditions' / directory.name).glob('*/native-text.txt')):
            if 'primary-' not in f.parent.name:
                continue
            inputs.append({'name': f.parent.name, 'sha256': sha256(f)})
            tokens = re.findall(r'[a-z0-9]+', f.read_text().lower())
            for i in range(len(tokens) - 24):
                corpus.setdefault(tuple(tokens[i:i + 25]), f.parent.name)
        truth_inputs = []
        for name in GROUNDTRUTH_FILENAMES.values():
            f = directory / name
            truth_inputs.append({'name': name, 'sha256': sha256(f)})
            for pointer, value in strings(load(f)):
                tokens = re.findall(r'[a-z0-9]+', value.lower())
                found = next((corpus[tuple(tokens[i:i + 25])] for i in range(len(tokens) - 24)
                              if tuple(tokens[i:i + 25]) in corpus), None)
                if found:
                    matches.append({'protocol_id': directory.name, 'file': name,
                                    'json_pointer': pointer, 'matching_source': found,
                                    'field_token_count': len(tokens)})
        coverage.append({'protocol_id': directory.name, 'rendition_inputs': inputs,
                         'groundtruth_inputs': truth_inputs})
    report = {'scope': 'Exact contiguous 25-token overlap against available primary native-text renditions only. No OCR, figure text, spreadsheet or legacy-HTML clearance is implied. Heuristic, not legal clearance.',
              'code_sha256': sha256(Path(__file__)), 'matching_fields': len(matches),
              'matches': matches, 'coverage': coverage}
    write(args.output, report)
    print(json.dumps({'matching_fields': len(matches), 'protocols': len(coverage),
                      'primary_renditions': sum(len(c['rendition_inputs']) for c in coverage)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
