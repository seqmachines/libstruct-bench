#!/usr/bin/env python3
"""Update multiplicity only from saved statistics; no scoring or inference.

Run: python analysis/paper_results/bonferroni_revision_20261025/rebuild.py
Outputs remain here. Publishing the two PDFs is a separate copy operation.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

from plot_task1 import draw

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parent.parent
HARNESS = BASE / 'model_harness_v4_7_0'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def write(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    sources = [
        HARNESS / 'results_v1/summary_table.csv',
        HARNESS / 'results_v1/manifest.json',
        BASE / 'camera_ready_v4_7_0/task1_paired_contrasts.csv',
        BASE / 'camera_ready_v4_7_0/task1_recomputed.csv',
        BASE / 'task1_summary.csv',
        BASE / 'task1_paired_protocols.csv',
    ]
    lock_path = HERE / 'input_lock.json'
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    if lock_path.exists():
        assert json.loads(lock_path.read_text()) == hashes, 'Saved inputs changed'
    else:
        lock_path.write_text(json.dumps(hashes, indent=2) + '\n')

    # Four primary sign-flip tests. Do not include exploratory factorial tests.
    model_harness = read(sources[0])
    assert len(model_harness) == 4
    for row in model_harness:
        row['Historical_Holm_p'] = row.pop('Holm_p')
        row['Bonferroni_p'] = min(1.0, 4 * float(row['Signflip_p']))
        row['Correction_family_size'] = 4
    assert [r['Metric'] for r in model_harness if r['Bonferroni_p'] < .05] == ['Task 3 state F1']
    write(HERE / 'model_harness_primary_bonferroni.csv', model_harness)

    # Retain the Task 1 Wilcoxon signed-rank tests, not mean sign-flip tests.
    task1 = [r for r in read(sources[2]) if r['contrast'] == 'agent_minus_api']
    assert len(task1) == 3 and {r['family'] for r in task1} == {'Claude', 'GPT', 'Gemini'}
    for row in task1:
        row['historical_holm_p'] = row.pop('holm_p')
        row['bonferroni_p'] = min(1.0, 3 * float(row['wilcoxon_sign_permutation_p']))
        row['correction_family_size'] = 3
    assert all(r['bonferroni_p'] == 1.0 for r in task1)
    write(HERE / 'task1_api_agent_bonferroni.csv', task1)

    # Cross-check every plotted Task 1 point against camera-ready scores.
    scores = {(r['model_family'], r['protocol_id'], r['interface'].lower()):
              float(r['sequence_similarity']) for r in read(sources[3])}
    pairs = read(sources[5])
    assert len(pairs) == 60
    for row in pairs:
        for interface in ['api', 'agent']:
            value = float(row[f'{interface}_similarity'])
            assert math.isclose(value, scores[row['model_family'], row['protocol_id'], interface], abs_tol=1e-12)
            row[f'{interface}_similarity'] = value
    summaries = read(sources[4])
    for row in summaries:
        verified = next(r for r in task1 if r['family'] == row['model_family'])
        assert math.isclose(float(row['p_raw']), float(verified['wilcoxon_sign_permutation_p']), abs_tol=1e-12)
        assert math.isclose(float(row['mean_agent_minus_api']), float(verified['difference']), abs_tol=1e-12)
        row['mean_agent_minus_api'] = float(verified['difference'])
        row['p_bonferroni'] = verified['bonferroni_p']
        row['paired_n'] = int(verified['n'])
        assert row['paired_n'] == 20
    figures = HERE / 'figures'
    figures.mkdir(exist_ok=True)
    draw(summaries, pairs, figures / 'fig2_task1_api_vs_coding_agent.pdf')
    subprocess.run([
        shutil.which('Rscript'), str(HARNESS / 'plot_publication.R'),
        str(HARNESS / 'results_v1'), str(figures / 'model_vs_harness_magnitudes.pdf'),
    ], check=True)

    manifest = {
        'operation': 'Bonferroni adjustment of saved raw p-values only',
        'inputs': hashes,
        'families': {
            'model_harness_primary': {'size': 4, 'test': 'two-sided exact sign-flip of protocol-level mean contrast'},
            'task1_api_agent': {'size': 3, 'test': 'two-sided paired Wilcoxon signed-rank with exhaustive sign permutation'},
        },
        'adjustment': 'min(1, family_size * p_raw)',
        'confidence_intervals': 'Saved pointwise paired protocol-bootstrap intervals unchanged; no resampling',
        'preserved': ['scores', 'predictions', 'references', 'selected trials', 'effect sizes', 'raw tests', 'historical result tables'],
        'out_of_scope': ['exploratory factorial and native-interface contrast families', 'PDF versus name-only analyses'],
        'task1_note': 'Use camera-ready contrast CIs; the older style-source summary uses a different saved bootstrap realization. Neither was recomputed.',
        'code': {str(p.relative_to(ROOT)): sha(p) for p in [Path(__file__), HERE / 'plot_task1.py', HARNESS / 'plot_publication.R']},
        'artifacts': {str(p.relative_to(HERE)): sha(p) for p in [
            HERE / 'model_harness_primary_bonferroni.csv',
            HERE / 'task1_api_agent_bonferroni.csv',
            *sorted(figures.glob('*.pdf')),
        ]},
    }
    (HERE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    assert all(sha(ROOT / p) == digest for p, digest in hashes.items())
    print('Verified all 60 Task 1 pairs against camera-ready scores; saved inputs unchanged.')
    print('Model-harness Bonferroni p:', [r['Bonferroni_p'] for r in model_harness])
    print('Task 1 API-agent Bonferroni p:', [r['bonferroni_p'] for r in task1])


if __name__ == '__main__':
    main()
