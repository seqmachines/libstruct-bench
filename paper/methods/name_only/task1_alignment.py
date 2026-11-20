"""Display-only, reference-anchored alignments of already-scored Task 1 strings.

Unit-cost Levenshtein traceback: diagonal, deletion, insertion. Two independent
pairwise alignments are merged on reference positions. Insertions from the two
predictions share a display boundary, but are not aligned against each other.
"""
from __future__ import annotations

from collections import Counter
import re

SYMBOLS = {'#': 'CELL_BARCODE', '~': 'UMI', '@': 'SAMPLE_INDEX',
           '&': 'LIGATION_BARCODE', '=': 'RT_BARCODE', '%': 'TN5_BARCODE',
           '$': 'FEATURE_BARCODE', '?': 'VARIABLE'}


def readable(seq):
    """Expand fixed bases as supplied; compress only repeated variable symbols."""
    return re.sub(r'([#~@&=%$?])\1*', lambda m: f'[{SYMBOLS[m[1]]}:{len(m[0])}]', seq)


def align(reference, predicted):
    n, m = len(reference), len(predicted)
    dp = [list(range(m+1))] + [[i]+[0]*m for i in range(1, n+1)]
    for i in range(1, n+1):
        for j in range(1, m+1):
            dp[i][j] = min(dp[i-1][j-1]+(reference[i-1] != predicted[j-1]),
                           dp[i-1][j]+1, dp[i][j-1]+1)
    columns = []
    i, j = n, m
    while i or j:
        if i and j and dp[i][j] == dp[i-1][j-1]+(reference[i-1] != predicted[j-1]):
            columns.append((reference[i-1], predicted[j-1])); i -= 1; j -= 1
        elif i and dp[i][j] == dp[i-1][j]+1:
            columns.append((reference[i-1], '')); i -= 1
        else:
            columns.append(('', predicted[j-1])); j -= 1
    columns.reverse()
    assert ''.join(a for a,b in columns) == reference
    assert ''.join(b for a,b in columns) == predicted
    assert sum(a != b for a,b in columns) == dp[n][m]
    return columns, dp[n][m]


def operation(a, b):
    if a == b:
        return 'match'
    return 'insertion' if not a else 'deletion' if not b else 'substitution'


def pair_summary(reference, predicted, columns, distance):
    counts = Counter(operation(a,b) for a,b in columns)
    events = []
    refpos = predpos = 0
    current = None
    for a, b in columns:
        op = operation(a, b)
        if op == 'match':
            current = None
        else:
            if current is None or current['type'] != op:
                current = dict(type=op, ref_before=refpos, pred_before=predpos,
                               reference='', prediction='', count=0)
                events.append(current)
            current['reference'] += a
            current['prediction'] += b
            current['count'] += 1
            current['ref_end'] = refpos + bool(a)
            current['pred_end'] = predpos + bool(b)
        refpos += bool(a); predpos += bool(b)
    for e in events:
        e['reference'] = readable(e['reference'])
        e['prediction'] = readable(e['prediction'])
    # Totals and ordered runs are literal properties, not biological inference.
    variables = []
    for symbol, label in SYMBOLS.items():
        nr, np = reference.count(symbol), predicted.count(symbol)
        if nr != np:
            variables.append(dict(label=label, reference=nr, prediction=np))
    # Split by symbol (including adjacent different kinds).
    def run_list(s):
        return [f'{SYMBOLS[m[1]]}:{len(m[0])}' for m in re.finditer(r'([#~@&=%$?])\1*', s)]
    return dict(distance=distance, matches=counts['match'], substitutions=counts['substitution'],
                insertions=counts['insertion'], deletions=counts['deletion'],
                ref_length=len(reference), pred_length=len(predicted), events=events,
                variable_differences=variables, reference_variable_runs=run_list(reference),
                prediction_variable_runs=run_list(predicted),
                unresolved_reference=bool(re.search(r'\[[^\]]+\]', reference)),
                unresolved_prediction=bool(re.search(r'\[[^\]]+\]', predicted)))


def anchor_merge(reference, aligned):
    """Merge pairwise columns, preserving each pairwise alignment exactly."""
    rows = []
    for columns in aligned:
        insertions = [[] for _ in range(len(reference)+1)]
        bases = ['']*len(reference)
        pos = 0
        for a,b in columns:
            if a:
                assert a == reference[pos]
                bases[pos] = b; pos += 1
            else:
                insertions[pos].append(b)
        assert pos == len(reference)
        rows.append((insertions, bases))
    out = []
    for pos in range(len(reference)+1):
        width = max((len(ins[pos]) for ins,_ in rows), default=0)
        for j in range(width):
            out.append(['']+[ins[pos][j] if j<len(ins[pos]) else '' for ins,_ in rows])
        if pos < len(reference):
            out.append([reference[pos]]+[bases[pos] for _,bases in rows])
    for r, pair in enumerate(aligned,1):
        recovered = [(c[0],c[r]) for c in out if c[0] or c[r]]
        assert recovered == pair
    return out


def blocks(columns):
    """Collapse only variable-symbol groups and unresolved bracket labels.

    Every original character belongs to exactly one displayed block. Fixed
    nucleotides outside unresolved-label spans remain individual columns.
    """
    if not columns:
        return []
    # Group each unresolved literal label as a whole display span, not as bases.
    opaque = []
    variable_runs = []
    for row in range(len(columns[0])):
        nongap = [(i,c[row]) for i,c in enumerate(columns) if c[row]]
        string = ''.join(x for _,x in nongap)
        variable_runs.append([(m.start(),m.end(),m[1]) for m in re.finditer(r'([#~@&=%$?])\1*',string)])
        for m in re.finditer(r'\[[^\]]+\]', string):
            opaque.append((nongap[m.start()][0], nongap[m.end()-1][0]+1))
    merged = []
    for start,end in sorted(opaque):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end,merged[-1][1]))
        else:
            merged.append((start,end))
    opaque_map = dict(merged)
    covered = {i for a,b in merged for i in range(a,b)}
    out = []
    i = 0
    positions = [0]*len(columns[0])
    while i < len(columns):
        end = i+1
        kind = 'base'
        if i in opaque_map:
            end = opaque_map[i]; kind = 'unresolved'
        elif all(not c or c in SYMBOLS for c in columns[i]):
            sets = [set(c) if c else set() for c in columns[i]]
            while end < len(columns) and end not in covered:
                candidate = columns[end]
                if not all(not c or c in SYMBOLS for c in candidate):
                    break
                nxt = [s | ({c} if c else set()) for s,c in zip(sets,candidate)]
                if any(len(s)>1 for s in nxt):
                    break
                sets = nxt; end += 1
            kind = 'placeholder'
        subset = columns[i:end]
        texts = [''.join(c[r] for c in subset) for r in range(len(positions))]
        cells = []
        for r, text in enumerate(texts):
            if r == 0:
                status = 'reference'
            else:
                ops = {operation(c[0],c[r]) for c in subset if c[0] or c[r]}
                if ops <= {'match'}:
                    status = 'match'
                elif ops <= {'match','deletion'}:
                    status = 'deletion' if not text else 'length'
                elif ops <= {'match','insertion'}:
                    status = 'insertion' if not texts[0] else 'length'
                else:
                    status = 'substitution'
            display = readable(text) if text else '·'
            fragment_note = ''
            if kind == 'placeholder' and text:
                run = next((x for x in variable_runs[r] if x[0] <= positions[r] < x[1]), None)
                if run and (positions[r] != run[0] or positions[r]+len(text) != run[1]):
                    display = display[:-1] + '*]'
                    fragment_note = (f'Alignment fragment: positions {positions[r]-run[0]+1}–'
                                     f'{positions[r]-run[0]+len(text)} of one {SYMBOLS[run[2]]} run of length {run[1]-run[0]}.')
            cells.append(dict(text=display, status=status, fragment_note=fragment_note,
                              start=positions[r]+1 if text else None, end=positions[r]+len(text),
                              positions=len(text), gap_positions=sum(not c[r] for c in subset)))
            positions[r] += len(text)
        out.append(dict(kind=kind, cells=cells, width=max(len(c['text']) for c in cells),
                        differs=any(c['status'] not in ('reference','match') for c in cells),
                        column_start=i+1, column_end=end))
        i = end
    assert positions == [sum(bool(c[r]) for c in columns) for r in range(len(positions))]
    return out


def library_alignment(reference, predictions, names):
    pairs, summaries = [], []
    for predicted in predictions:
        pair, distance = align(reference, predicted)
        pairs.append(pair)
        summaries.append(pair_summary(reference, predicted, pair, distance))
    merged = anchor_merge(reference, pairs)
    return dict(row_labels=['Ground truth']+names, blocks=blocks(merged), summaries=summaries,
                reference=readable(reference), predictions=[readable(s) for s in predictions])
