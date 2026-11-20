"""Derive the legacy Task 1 scoring view from hash-pinned canonical T1.

This is a downstream representation conversion, never a canonical label edit.
The existing string scorer and the historical raw predictions are unchanged.
"""
from copy import deepcopy
from itertools import product
import re

from libstruct_bench.library_structure import grade_library_prediction, normalize_library_sequence


def project_reference(document, length_choices=None):
    length_choices = length_choices or {}
    used_choices = set()
    result = deepcopy(document)
    changes = []
    unresolved = []
    for index, library in enumerate(result['libraries']):
        segments = library['segments']
        assembled = ''.join(s.get('placeholder') or s.get('sequence') or '' for s in segments)
        if assembled != library['library_sequence']:
            raise ValueError(f"{document['protocol_id']} library {index}: segment/string disagreement")
        parts = []
        for segment in segments:
            value = segment.get('placeholder') or segment.get('sequence') or ''
            projected = value
            reason = 'existing v0 placeholder normalization'
            if segment['kind'] == 'biological_insert':
                projected = ''
                reason = 'omit biological payload, including annotated internal insert bases'
            elif (index, segment['segment_id']) in length_choices:
                key = (index, segment['segment_id'])
                length = length_choices[key]
                if (segment['kind'] != 'variable' or 'length' in segment
                        or not re.fullmatch(r'\[CELL_BARCODE\d*\]', value)
                        or type(length) is not int or length <= 0):
                    raise ValueError(f'Invalid declared barcode length choice: {key}')
                projected = '#' * length
                used_choices.add(key)
                reason = 'one explicitly allowed length from the hash-pinned reference alternatives'
            elif re.fullmatch(r'\[CELL_BARCODE\d+:\d+\]', value):
                projected = re.sub(r'CELL_BARCODE\d+:', 'CELL_BARCODE:', value)
                reason = 'numbered cell-barcode role to v0 cell-barcode symbols'
            elif value == '[RANDOM_HEXAMER:6]':
                projected = '[RANDOM:6]'
                reason = 'random hexamer is six structural variable bases'
            elif value == '[PHASE_BLOCK]' and '0-4' in segment.get('role', ''):
                projected = '[PHASE_BLOCK:4]'
                reason = 'existing Task 1 prompt selects the maximum stated phase-block length'
            projected = normalize_library_sequence(projected)
            parts.append(projected)
            if projected != value:
                changes.append({'library_index': index, 'segment_id': segment['segment_id'],
                                'source': value, 'scoring': projected, 'reason': reason})
            if '[' in projected:
                unresolved.append({'library_index': index, 'segment_id': segment['segment_id'],
                                   'placeholder': projected,
                                   'reason': 'No supported expansion in this reference; retained literally by unchanged v0 scorer'})
        library['library_sequence'] = ''.join(parts)
    if used_choices != set(length_choices):
        raise ValueError('A declared barcode length choice did not identify a variable segment')
    return result, {'transformations': changes, 'unresolved_placeholders': unresolved}


def project_reference_variants(document, declarations=()):
    """Expand only locked allowed lengths, independently of any prediction.

    These are alternative complete references, not additional final libraries.
    The four inDrop lengths are not collapsed to an arbitrary single length.
    """
    relevant = [d for d in declarations if d['protocol_id'] == document['protocol_id']]
    keys = [(d['library_index'], d['segment_id']) for d in relevant]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate reference length declarations')
    for d in relevant:
        segments = document['libraries'][d['library_index']]['segments']
        matches = [s for s in segments if s['segment_id'] == d['segment_id']]
        if len(matches) != 1 or matches[0].get('placeholder') != d['placeholder']:
            raise ValueError('Reference length declaration does not match the canonical segment')
        lengths = d['lengths']
        if (not lengths or len(set(lengths)) != len(lengths)
                or any(type(n) is not int or n <= 0 for n in lengths)):
            raise ValueError('Reference lengths must be distinct positive integers')
    variants = []
    for lengths in product(*(d['lengths'] for d in relevant)):
        projected, audit = project_reference(document, dict(zip(keys, lengths, strict=True)))
        variants.append({'document': projected, 'projection': audit,
                         'length_choices': [dict(library_index=k[0], segment_id=k[1], length=n)
                                            for k, n in zip(keys, lengths, strict=True)]})
    return variants


def grade_reference_variants(prediction, variants, *, expected_protocol_id):
    """Reuse the original verifier for each allowed reference; deterministic ties."""
    if not variants:
        raise ValueError('At least one reference variant is required')
    scores = [grade_library_prediction(prediction, v['document'],
                                      expected_protocol_id=expected_protocol_id) for v in variants]
    selected = max(range(len(scores)), key=lambda i: scores[i][0]['sequence_similarity'])
    metrics, audit = scores[selected]
    audit = dict(audit, reference_variant_index=selected,
                 reference_length_choices=variants[selected]['length_choices'],
                 reference_variant_scores=[{'index': i, 'length_choices': v['length_choices'],
                                            'metrics': result[0]}
                                           for i, (v, result) in enumerate(zip(variants, scores, strict=True))])
    return metrics, audit


def without_vn_reference(document):
    """Historical VN-omitted sensitivity reference; primary truth requires VN."""
    result = deepcopy(document)
    if document['protocol_id'] != '10x_chromium_3_feature_barcoding':
        return result
    libraries = [x for x in result['libraries'] if x['modality'] == 'gene expression']
    assert len(libraries) == 1
    library = libraries[0]
    old = '~' * 12 + 'T' * 30 + 'VNAGATCGGAAGAGC'
    new = '~' * 12 + 'T' * 30 + 'AGATCGGAAGAGC'
    assert library['library_sequence'].count(old) == 1
    library['library_sequence'] = library['library_sequence'].replace(old, new)
    return result
