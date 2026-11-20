"""Build an offline, aligned Task 1 review from frozen results.

No scoring, inference, or changes to experiment files. Run from any directory:
  python analysis/paper_results/name_only_v4_7_0/build_task1_visual_review.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from task1_alignment import library_alignment, readable

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
CAMERA = BASE.parent / 'camera_ready_v4_7_0'
OUT = BASE / 'task1_visual_review'
SYMBOLS = {'#': 'CELL_BARCODE', '~': 'UMI', '@': 'SAMPLE_INDEX',
           '&': 'LIGATION_BARCODE', '=': 'RT_BARCODE', '%': 'TN5_BARCODE',
           '$': 'FEATURE_BARCODE', '?': 'VARIABLE'}
BASES = frozenset('ACGTU')
AMBIGUOUS = frozenset('NVRYSWKMBDH')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read_csv(p):
    with Path(p).open(newline='') as f:
        return list(csv.DictReader(f))


def category(label):
    s = label.upper()
    if 'UNKNOWN' in s or 'UNRESOLVED' in s or 'NOT_GIVEN' in s:
        return 'unknown'
    if 'UMI' in s:
        return 'umi'
    if 'BARCODE' in s or s.startswith('CB:'):
        return 'barcode'
    if 'INDEX' in s or re.match(r'I[57](?:_|:)', s):
        return 'index'
    if any(x in s for x in ('CDNA', 'GDNA', 'INSERT', 'PAYLOAD', 'TRANSCRIPT')):
        return 'insert'
    if any(x in s for x in ('AMBIG', 'RANDOM', 'VARIABLE', 'SPACER')):
        return 'variable'
    return 'fixed'


def scrub(s):
    """Remove embedded nucleotide strings even inside free-form labels/tooltips."""
    return re.sub(r'(?<![A-Za-z])[ACGTUNVRYSWKMBDH]{5,}(?![A-Za-z])',
                  lambda m: f'{len(m[0])} bases hidden', str(s))


def token(label, *, kind=None, note='', length=None):
    label = scrub(label).strip().strip('[]')
    return dict(label=label, kind=kind or category(label), note=scrub(note), length=length)


def compact_sequence(s):
    """Lossless block lengths, hiding nucleotide identities; no biological inference.

    Adjacent identical score symbols are one run. Consecutive fixed A/C/G/T/U
    bases are one block. Ambiguous bases remain distinct categories. Homopolymer
    runs of >=8 bases are exposed as repeated-base labels for easier review.
    """
    s = re.sub(r'\s+', '', s).upper()
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == '[':
            end = s.find(']', i)
            if end >= 0:
                out.append(token(s[i+1:end], kind='unknown', note='Literal unresolved placeholder in the stored string.'))
                i = end + 1
                continue
        j = i + 1
        if c in SYMBOLS:
            while j < len(s) and s[j] == c:
                j += 1
            out.append(token(f'{SYMBOLS[c]}:{j-i}', length=j-i))
        elif c in BASES:
            while j < len(s) and s[j] in BASES:
                j += 1
            block = s[i:j]
            cursor = 0
            for match in re.finditer(r'([ACGTU])\1{7,}', block):
                if match.start() > cursor:
                    n = match.start() - cursor
                    out.append(token(f'FIXED:{n}', length=n))
                n = len(match[0])
                out.append(token(f'POLY_{match[1]}:{n}', length=n,
                                 note='Repeated base in the stored string; no functional role inferred.'))
                cursor = match.end()
            if cursor < len(block):
                n = len(block) - cursor
                out.append(token(f'FIXED:{n}', length=n))
        elif c in AMBIGUOUS:
            while j < len(s) and s[j] == c:
                j += 1
            out.append(token(f'AMBIG_{c}:{j-i}', kind='variable', length=j-i))
        else:
            out.append(token(f'OTHER:{ord(c)}', kind='unknown', length=1,
                             note='Unrecognized literal character, displayed by Unicode code point.'))
        i = j
    if not ('[' in s):
        assert sum(t['length'] for t in out) == len(s)
    return out


def annotation_tokens(s):
    """Preserve annotation ordering without assigning adjacent labels to sequences.

    Parenthetical labels are retained; actual base runs are masked. This is an
    annotation transcript, not a validated nonoverlapping segment decomposition.
    """
    parts, start, i = [], 0, 0
    while i < len(s):
        if s[i] in '[(':
            if i > start:
                parts.append(('text', s[start:i]))
            opener = s[i]
            closer = ']' if opener == '[' else ')'
            depth, end = 1, i+1
            while end < len(s) and depth:
                depth += (s[end] == opener) - (s[end] == closer)
                end += 1
            if depth:
                parts.append(('text', s[i:]))
                start = len(s)
                break
            parts.append(('label', s[i+1:end-1]))
            i = start = end
        else:
            i += 1
    if start < len(s):
        parts.append(('text', s[start:]))
    result = []
    for typ, part in parts:
        part = part.strip()
        if not part:
            continue
        if typ == 'label':
            result.append(token(part, note='Model-supplied annotation; not independently verified.'))
        else:
            for piece in re.findall(r'[ACGTUNVRYSWKMBDH#~@&=%$?]+|[^ACGTUNVRYSWKMBDH#~@&=%$?]+', part):
                if re.fullmatch(r'[ACGTUNVRYSWKMBDH#~@&=%$?]+', piece):
                    result.extend(compact_sequence(piece))
                elif piece.strip(" \n\t→-·,;:'53′\"*"):
                    result.append(token(piece, kind='note', note='Free-form model annotation.'))
    return result


def gt_annotation(lib, pid, alternatives):
    result = []
    for s in lib.get('segments', []):
        note = s.get('role', '')
        if s.get('notes'):
            note += '; ' + s['notes']
        if s.get('placeholder'):
            label = s['placeholder'].strip('[]')
            alt = next((a for a in alternatives if a['protocol_id'] == pid and
                        a['segment_id'] == s['segment_id']), None)
            if alt:
                label += ':' + '/'.join(map(str, alt['lengths']))
            result.append(token(label, note=note))
        else:
            seq = re.sub(r'\s+', '', s.get('sequence', ''))
            n = len(seq)
            label = re.sub(r'^(seg_|gex_|fb_)', '', s['segment_id']).upper()
            if n:
                label += f':{n}'
            if s.get('length') is not None and s['length'] != n:
                note += f"; stored sequence has {n} positions; reference length metadata says {s['length']}."
            result.append(token(label, kind='fixed', note=note, length=n))
    return result or compact_sequence(lib['library_sequence'])


def build():
    hashes = {}
    def pinned(p, expected=None):
        p = Path(p).resolve()
        digest = sha(p)
        if expected:
            assert digest == expected, f'Changed frozen input: {p}'
        hashes[str(p)] = digest
        return p
    def load(p, expected=None):
        return json.loads(pinned(p, expected).read_text())

    release = load(CAMERA / 'reproducibility_manifest.json')
    prov = load(CAMERA / 'task1_controls_recomputed_provenance.json',
                release['artifacts']['task1_controls_recomputed_provenance.json'])
    gt_prov = load(CAMERA / 'task1_recomputed_provenance.json',
                   release['artifacts']['task1_recomputed_provenance.json'])
    scores = read_csv(pinned(CAMERA / 'task1_controls_recomputed.csv',
                            release['artifacts']['task1_controls_recomputed.csv']))
    pdf_rows = read_csv(pinned(CAMERA / 'task1_recomputed.csv',
                              release['artifacts']['task1_recomputed.csv']))
    new_scores = read_csv(pinned(BASE / 'results_n1_v1/task1_per_protocol.csv'))
    runs = {(r['protocol_id'], r['model']): r for r in prov['runs']}
    pdf = {(r['protocol_id'], r['model']): r for r in pdf_rows if r['interface'] == 'API'}
    frozen = {(r['protocol_id'], r['model_family']): r for r in new_scores}
    protocols = []
    models = ['GPT', 'Claude', 'Gemini']
    pred_count = empty_count = pred_lib_count = 0
    for pid in gt_prov['protocol_ids']:
        g = gt_prov['groundtruth'][pid]
        gt_path = pinned(g['path'], g['sha256'])
        doc = json.loads(gt_path.read_text())
        libraries = []
        for i, lib in enumerate(doc['libraries']):
            libraries.append(dict(id=f'G{i+1}', modality=lib.get('modality', ''),
                                  annotated=gt_annotation(lib, pid, gt_prov['length_alternatives'])))
        variants = [dict(index=v['index'], choices=v['length_choices'], sequences=v['library_sequences'],
                         libraries=[compact_sequence(s) for s in v['library_sequences']])
                    for v in g['reference_variants']]
        protocol = dict(id=pid, name=doc['protocol_name'], gt=libraries, variants=variants,
                        reference_path=str(gt_path), reference_sha256=g['sha256'], models=[])
        for model in models:
            score = next(s for s in scores if s['protocol_id'] == pid and s['model_family'] == model)
            record = runs[pid, score['model']]
            row = dict(model=model, model_id=score['model'], label=score['model_label'])
            for condition in ('pdf', 'name_only'):
                assert abs(float(score[f'{condition}_similarity']) - float(frozen[pid, model][f'{condition}_similarity'])) < 1e-12
                source = record[condition]
                path = ROOT / pdf[pid, score['model']]['prediction_path'] if condition == 'pdf' else Path(source['case_path']) / 'prediction.json'
                prediction = load(path, source['files_sha256']['prediction.json'])
                audit = source['audit']
                assert float(score[f'{condition}_similarity']) == source['metrics']['sequence_similarity']
                matches = audit.get('library_matches', [])
                indexed = {m['predicted_index']: m for m in matches if m['predicted_index'] is not None}
                entries = []
                for i, lib in enumerate(prediction['libraries']):
                    m = indexed.get(i)
                    entries.append(dict(id=f'P{i+1}', name=scrub(lib.get('library_id', '')),
                        modality=lib.get('modality', ''), annotated=annotation_tokens(lib['annotated_library_sequence']),
                        sequence=lib['library_sequence'], original_annotation=lib['annotated_library_sequence'],
                        scored=compact_sequence(lib['library_sequence']),
                        matched_gt=m['ground_truth_index'] if m else None,
                        similarity=m['sequence_similarity'] if m else None,
                        edit_distance=m['edit_distance'] if m else None))
                row[condition] = dict(score=float(score[f'{condition}_similarity']),
                    exact=score[f'{condition}_exact'] == 'True', status=score[f'{condition}_status'],
                    empty=not entries, libraries=entries, path=str(path.resolve()),
                    sha256=source['files_sha256']['prediction.json'],
                    reference_variant=audit.get('reference_variant_index', 0),
                    reference_choices=audit.get('reference_length_choices', []))
                pred_count += 1
                pred_lib_count += len(entries)
                empty_count += not entries
            row['alignments'] = []
            for gi in range(len(libraries)):
                members = {c: next((l for l in row[c]['libraries'] if l['matched_gt'] == gi), None)
                           for c in ('pdf', 'name_only')}
                refs = {c: variants[row[c]['reference_variant']]['sequences'][gi] for c in members}
                groups = [('pdf','name_only')] if refs['pdf'] == refs['name_only'] else [('pdf',),('name_only',)]
                groups_out = []
                for group in groups:
                    alignment = library_alignment(refs[group[0]],
                        [members[c]['sequence'] if members[c] else '' for c in group],
                        ['PDF prediction' if c == 'pdf' else 'Name-only prediction' for c in group])
                    alignment['conditions'] = list(group)
                    alignment['reference_choices'] = row[group[0]]['reference_choices']
                    for c, summary in zip(group, alignment['summaries']):
                        entry = members[c]
                        summary['missing_library'] = entry is None
                        summary['empty_output'] = row[c]['empty']
                        summary['prediction_id'] = entry['id'] if entry else None
                        summary['prediction_name'] = entry['name'] if entry else None
                        summary['saved_similarity'] = entry['similarity'] if entry else 0.
                        if entry:
                            assert summary['distance'] == entry['edit_distance'], (pid, model, c, gi)
                            match = next(m for m in record[c]['audit']['library_matches'] if m['ground_truth_index'] == gi)
                            assert refs[c] == match['ground_truth_sequence']
                            assert entry['sequence'] == match['predicted_sequence']
                    groups_out.append(alignment)
                row['alignments'].append(dict(gt_index=gi, groups=groups_out,
                    different_allowed_references=len(groups)>1))
            row['delta'] = row['name_only']['score'] - row['pdf']['score']
            protocol['models'].append(row)
        protocols.append(protocol)
    assert len(protocols) == 20 and pred_count == 120 and empty_count == 4
    content = dict(protocols=protocols, reference_snapshot=prov['reference_snapshot'],
                   models=models, execution_count=pred_count, library_count=pred_lib_count,
                   reference_library_count=sum(len(p['gt']) for p in protocols), empty_count=empty_count)
    encoded = json.dumps(content, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    pinned(BASE / 'task1_alignment.py')
    template = pinned(OUT / 'alignment.template.html').read_text()
    assert template.count('__REVIEW_DATA__') == 1
    OUT.mkdir(exist_ok=True)
    target = OUT / 'task1_pdf_vs_name_review.html'
    target.write_text(template.replace('__REVIEW_DATA__', encoded))
    assert all(sha(p) == h for p, h in hashes.items())
    manifest = dict(inputs_sha256=hashes, builder_sha256=sha(__file__),
                    output_sha256=sha(target), reference_snapshot=prov['reference_snapshot'],
                    protocols=20, models=3, predictions=pred_count,
                    predicted_libraries=pred_lib_count, empty_predictions=empty_count,
                    reference_libraries=content['reference_library_count'],
                    scoring_recomputed=False, source_files_changed=False,
                    display_policy='Actual nucleotides are displayed; variable scoring-symbol runs are collapsed into typed placeholders. '
                    'Saved library pairings and scores are unchanged. Pairwise unit-cost Levenshtein distances reproduce every saved matched-library distance. '
                    'PDF and Name are aligned independently to the reference, then merged into a shared coordinate display; insertion sequences at a shared boundary are not aligned against each other. '
                    'Different allowed inDrop reference variants use separate paired alignments. '
                    'Biological payloads omitted from scored strings are not reconstructed. Unresolved literal reference labels are flagged separately.')
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k:v for k,v in manifest.items() if k != 'inputs_sha256'}, indent=2))
    print(target)


if __name__ == '__main__':
    build()
