#!/usr/bin/env python3
"""Reproduce Figure 2 exclusively from the matched-version Task 1 round-2 runs.

Run from any directory; no model calls or ground-truth downloads are made.
Existing analysis/figure filenames are updated in place. The paper is not edited.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/libstruct-fig2-matplotlib')
os.environ.setdefault('XDG_CACHE_HOME', '/private/tmp/libstruct-fig2-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import scipy
from scipy.stats import PermutationMethod, wilcoxon
from libstruct_bench.library_structure import (
    LibraryStructureValidationError, extract_json_document,
    grade_library_prediction, parse_library_entries, zero_metrics,
)
from task1_reference import project_reference_variants, grade_reference_variants, without_vn_reference

OUT = ROOT / 'analysis/paper_results'
RUNS = ROOT / 'runs/library_structure/round2'
MANIFEST = ROOT / 'benchmarks/library_structure/round2/protocols.json'
REFERENCE_LOCK = OUT / 'task1_reference_lock.json'
BOOTSTRAP_N = 100_000
SEED = 20261010
MODELS = (
    ('Claude', 'Claude Opus 5', 'anthropic/claude-opus-5',
     'task1-round2-claude-code-high', 'claude-code', 'claude-opus-5', '2.1.233'),
    ('GPT', 'GPT-5.6 Sol', 'openai/gpt-5.6-sol',
     'task1-round2-codex-high', 'codex', 'gpt-5.6-sol', '0.147.0'),
    ('Gemini', 'Gemini 3.7 Flash', 'google/gemini-3.7-flash',
     'task1-round2-antigravity-cli-high', 'antigravity-cli', 'gemini-3.7-flash-high', '1.3.2'),
)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def relative(path):
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def collect():
    manifest = read(MANIFEST)
    protocols = manifest['protocols']
    ids = [p['protocol_id'] for p in protocols]
    assert len(ids) == len(set(ids)) == 20, 'Expected exactly the round-2 20-protocol set'
    scorer_files = ['library_structure.py', 'matching.py', 'cli/grade_library_v0.py']
    scorer_hashes = {s: sha(ROOT / 'src/libstruct_bench' / s) for s in scorer_files}
    reference_lock = read(REFERENCE_LOCK)
    reference_root = Path(reference_lock['canonical_root'])
    reference_hashes = {r['protocol_id']: r['sha256'] for r in reference_lock['files']}
    assert set(reference_hashes) == set(ids)
    for amendment in reference_lock['amendments']:
        for artifact in amendment['artifacts'].values():
            assert sha(Path(artifact['path'])) == artifact['sha256'], 'Amendment audit artifact changed'
    provenance = {
        'manifest': relative(MANIFEST), 'manifest_sha256': sha(MANIFEST),
        'input_revision': manifest['input_revision'], 'protocol_ids': ids,
        'source_root': relative(RUNS), 'scorer_sha256': scorer_hashes,
        'reference_repository': reference_lock['repository'],
        'reference_revision': reference_lock['revision'],
        'reference_snapshot': reference_lock['snapshot'],
        'reference_amendments': reference_lock['amendments'],
        'reference_lock_sha256': sha(REFERENCE_LOCK),
        'reference_projection_sha256': sha(OUT / 'task1_reference.py'),
        'reference_policy': 'HF base plus human-approved 10x T(30)VN[CDNA], inDrop W1 orientation and barcode1=8/9/10/11 corrections, projected to the original Task 1 string contract; VN is required in primary analysis',
        'length_alternatives': reference_lock['length_alternatives'],
        'length_alternative_policy': reference_lock['length_alternative_policy'],
        'groundtruth': {}, 'input_transformations': [], 'runs': [],
        'bootstrap': {'method': 'paired percentile', 'resamples': BOOTSTRAP_N, 'seed': SEED},
        'wilcoxon': {'alternative': 'two-sided', 'zero_method': 'wilcox',
                     'method': 'exhaustive sign permutation of nonzero differences',
                     'difference_rounding_decimals': 12, 'multiplicity': 'Holm across three models'},
        'versions': {'numpy': np.__version__, 'scipy': scipy.__version__,
                     'matplotlib': matplotlib.__version__},
    }
    groundtruth = {}
    historical_groundtruth = {}
    for pid in ids:
        task = RUNS / 'tasks' / pid
        gt_path = task / 'tests/groundtruth.json'
        gt_hash = sha(gt_path)
        meta = tomllib.loads((task / 'task.toml').read_text())['metadata']
        assert meta['protocol_id'] == pid
        assert meta['input_revision'] == manifest['input_revision']
        assert meta['groundtruth_revision'] == 'sha256:' + gt_hash
        for s, h in scorer_hashes.items():
            assert sha(task / 'tests/libstruct_bench' / s) == h, (pid, s, 'scorer changed')
        historical_groundtruth[pid] = read(gt_path)
        approved_path = reference_root / pid / 'groundtruth_final_lib_struct.json'
        approved_hash = sha(approved_path)
        assert approved_hash == reference_hashes[pid], ('approved reference changed', pid)
        approved = read(approved_path)
        assert approved['protocol_id'] == pid
        groundtruth[pid] = project_reference_variants(approved, reference_lock['length_alternatives'])
        projection = groundtruth[pid][0]['projection']
        provenance['groundtruth'][pid] = {
            'path': relative(approved_path), 'sha256': approved_hash,
            'historical_path': relative(gt_path), 'historical_sha256': gt_hash,
            'canonical_library_count': len(approved['libraries']),
            'historical_library_count': len(historical_groundtruth[pid]['libraries']),
            'projection': projection,
            'reference_variants': [{'index': i, 'length_choices': v['length_choices'],
                                    'projection': v['projection'],
                                    'library_sequences': [lib['library_sequence'] for lib in v['document']['libraries']]}
                                   for i, v in enumerate(groundtruth[pid])],
        }

    rows = []
    audits = []
    for family, title, model, job, agent_name, agent_model, version in MODELS:
        api_root = RUNS / 'direct_api' / model.replace('/', '-')
        agent_root = RUNS / 'coding_agent' / job
        by_protocol = {}
        for result_path in sorted(agent_root.glob('*/result.json')):
            result = read(result_path)
            pid = Path(result['task_id']['path']).name
            assert pid in ids and pid not in by_protocol, ('unexpected or duplicate CLI protocol', pid)
            assert Path(result['task_id']['path']) == Path(relative(RUNS / 'tasks' / pid))
            by_protocol[pid] = (result_path, result)
        api_ids = {p.parent.name for p in api_root.glob('*/prediction.json')}
        assert api_ids <= set(ids), ('unexpected API protocol', api_ids - set(ids))

        for protocol in protocols:
            pid = protocol['protocol_id']
            for interface in ('API', 'Agent'):
                row = {'model_family': family, 'model': model, 'interface': interface,
                       'protocol_id': pid, 'status': 'ok', 'sequence_similarity': None,
                       'prediction_parse_valid': False, 'exact_match': False,
                       'ground_truth_library_count': None, 'predicted_library_count': None,
                       'matched_library_count': None, 'error': '', 'prediction_path': '',
                       'empty_library_output': False, 'archived_api_errors': 0,
                       'historical_sequence_similarity': None, 'historical_exact_match': False,
                       'similarity_change_from_historical': None,
                       'vn_omitted_sensitivity_similarity': None, 'vn_omitted_sensitivity_exact': False,
                       'reference_variant_index': None, 'reference_length_choices': '',
                       'prediction_sha256': '', 'groundtruth_sha256': provenance['groundtruth'][pid]['sha256']}
                info = {'model_family': family, 'interface': interface, 'protocol_id': pid}
                result = None
                if interface == 'API':
                    case = api_root / pid
                    path = case / 'prediction.json'
                    if (case / 'error.json').exists() or not path.exists():
                        row.update(status='technical_failure', error='Recorded API error or missing prediction')
                    else:
                        request_path = case / 'request.json'
                        request = read(request_path)
                        assert request['model'] == model
                        assert request['reasoning']['effort'] == 'high'
                        assert request['max_tokens'] == 64000
                        response_path = case / 'response.json'
                        response = read(response_path)
                        assert response['model'] == model, ('response model mismatch', family, pid)
                        assert not response.get('error')
                        assert len(response['choices']) == 1
                        choice = response['choices'][0]
                        assert choice['finish_reason'] == 'stop', ('incomplete response', family, pid)
                        assert extract_json_document((case / 'raw_response.txt').read_text()) == read(path)
                        files = read(case / 'input_files.json')
                        assert [f['path'] for f in files] == protocol['input_paths']
                        assert all(f['mime_type'] == 'application/pdf' for f in files)
                        for f in files:
                            if f.get('input_revision'):
                                assert f['input_revision'] == manifest['input_revision']
                            if f.get('optimization', {}).get('applied'):
                                provenance['input_transformations'].append(
                                    {'model_family': family, 'protocol_id': pid, **f})
                        info.update(request_sha256=sha(request_path), response_sha256=sha(response_path),
                                    input_files_sha256=sha(case / 'input_files.json'),
                                    reasoning_effort='high', max_completion_tokens=64000,
                                    response_model=response['model'], finish_reason=choice['finish_reason'],
                                    archived_attempt_records=len(list((case / 'attempt_history').glob('*.zip'))))
                        for archive in (case / 'attempt_history').glob('*.zip'):
                            with ZipFile(archive) as previous:
                                row['archived_api_errors'] += int('error.json' in previous.namelist())
                        del request, response
                else:
                    if pid not in by_protocol:
                        row.update(status='technical_failure', error='Missing CLI trial')
                        path = agent_root / pid / 'prediction.json'
                    else:
                        result_path, result = by_protocol[pid]
                        ai = result['agent_info']
                        assert ai['name'] == agent_name and ai['model_info']['name'] == agent_model
                        assert ai['version'] == version
                        assert result['config']['agent']['kwargs']['reasoning_effort'] == 'high'
                        paths = list((result_path.parent / 'artifacts').rglob('prediction.json'))
                        assert len(paths) <= 1, ('duplicate prediction artifacts', pid, paths)
                        path = paths[0] if paths else result_path.parent / 'artifacts/prediction.json'
                        if result['exception_info'] or not path.exists():
                            row.update(status='technical_failure', error='CLI exception or missing prediction')
                        info.update(result_path=relative(result_path), result_sha256=sha(result_path),
                                    agent=agent_name, agent_model=agent_model, agent_version=version,
                                    reasoning_effort='high')

                if row['status'] != 'technical_failure':
                    prediction_text = path.read_text()
                    assert '[REDACTED]' not in prediction_text, ('redacted prediction', str(path))
                    prediction = json.loads(prediction_text)
                    assert prediction.get('protocol_id') == pid
                    row['empty_library_output'] = prediction.get('libraries') == []
                    try:
                        old_metrics, old_audit = grade_library_prediction(
                            prediction, historical_groundtruth[pid], expected_protocol_id=pid)
                    except LibraryStructureValidationError:
                        old_metrics, old_audit = zero_metrics(), {}
                    row['historical_sequence_similarity'] = old_metrics['sequence_similarity']
                    row['historical_exact_match'] = is_exact(old_metrics, old_audit)
                    if result is not None:
                        saved = result['verifier_result']['rewards']
                        assert all(abs(saved[k] - v) < 1e-12 for k, v in old_metrics.items()), (
                            'historical CLI metrics disagree with saved verifier', family, pid)
                    try:
                        metrics, audit = grade_reference_variants(
                            prediction, groundtruth[pid], expected_protocol_id=pid)
                        row['prediction_parse_valid'] = True
                        row['exact_match'] = is_exact(metrics, audit)
                        row['reference_variant_index'] = audit['reference_variant_index']
                        row['reference_length_choices'] = json.dumps(audit['reference_length_choices'])
                    except LibraryStructureValidationError as exc:
                        # Actual model output failure: identical to grade_library_v0's
                        # zero-metric policy. Missing/technical cases never enter here.
                        metrics = zero_metrics()
                        audit = {'error': str(exc)}
                        row.update(status='invalid_output', error=str(exc))
                    row.update(sequence_similarity=metrics['sequence_similarity'],
                               ground_truth_library_count=metrics['ground_truth_library_count'],
                               predicted_library_count=metrics['predicted_library_count'],
                               matched_library_count=metrics['matched_library_count'],
                               prediction_path=relative(path), prediction_sha256=sha(path))
                    if not row['prediction_parse_valid']:
                        row['ground_truth_library_count'] = len(parse_library_entries(groundtruth[pid][0]['document'])[1])
                    row['similarity_change_from_historical'] = metrics['sequence_similarity'] - old_metrics['sequence_similarity']
                    row['vn_omitted_sensitivity_similarity'] = metrics['sequence_similarity']
                    row['vn_omitted_sensitivity_exact'] = row['exact_match']
                    if pid == '10x_chromium_3_feature_barcoding' and row['prediction_parse_valid']:
                        vn_metrics, vn_audit = grade_library_prediction(
                            prediction, without_vn_reference(groundtruth[pid][0]['document']), expected_protocol_id=pid)
                        row['vn_omitted_sensitivity_similarity'] = vn_metrics['sequence_similarity']
                        row['vn_omitted_sensitivity_exact'] = is_exact(vn_metrics, vn_audit)
                    audits.append({'model_family': family, 'interface': interface,
                                   'protocol_id': pid, 'metrics': metrics, 'audit': audit})
                rows.append(row)
                provenance['runs'].append(info)
    assert len(rows) == 120
    return rows, audits, provenance


def is_exact(metrics, audit):
    return (metrics['sequence_similarity'] == 1.0
            and metrics['ground_truth_library_count'] == metrics['predicted_library_count']
            and bool(audit.get('library_matches'))
            and all(m['predicted_index'] is not None and m['edit_distance'] == 0
                    for m in audit['library_matches']))


def summarize(rows):
    result = []
    paired_rows = []
    for family, title, *_ in MODELS:
        model_rows = [r for r in rows if r['model_family'] == family]
        lookup = {(r['protocol_id'], r['interface']): r for r in model_rows}
        ids = [r['protocol_id'] for r in model_rows if r['interface'] == 'API']
        pairs = [(lookup[(pid, 'API')], lookup[(pid, 'Agent')]) for pid in ids
                 if all(lookup[(pid, method)]['sequence_similarity'] is not None for method in ('API', 'Agent'))]
        if not pairs:
            raise ValueError(f'No scorable pairs for {family}')
        api = np.array([a['sequence_similarity'] for a, _ in pairs])
        agent = np.array([b['sequence_similarity'] for _, b in pairs])
        delta = agent - api
        # Compute differences once; rounding only affects rank/tie recognition.
        ranked = np.round(delta, 12)
        nonzero = ranked[ranked != 0]
        if len(nonzero):
            test = wilcoxon(nonzero, zero_method='wilcox', alternative='two-sided',
                            method=PermutationMethod(n_resamples=np.inf, batch=4096))
            statistic, pvalue = float(test.statistic), float(test.pvalue)
        else:
            statistic, pvalue = 0.0, 1.0
        rng = np.random.default_rng(SEED)
        resampled_means = rng.choice(delta, size=(BOOTSTRAP_N, len(delta))).mean(axis=1)
        ci = np.quantile(resampled_means, [0.025, 0.975])
        summary = {'model_family': family, 'model_label': title, 'scheduled_protocols': len(ids),
                   'paired_n': len(pairs), 'api_mean_similarity': float(api.mean()),
                   'agent_mean_similarity': float(agent.mean()), 'mean_agent_minus_api': float(delta.mean()),
                   'ci95_low': float(ci[0]), 'ci95_high': float(ci[1]),
                   'wilcoxon_statistic': statistic, 'wilcoxon_nonzero_n': len(nonzero),
                   'p_raw': pvalue, 'p_holm': None, 'api_wins': int(sum(ranked < 0)),
                   'agent_wins': int(sum(ranked > 0)), 'ties': int(sum(ranked == 0))}
        summary['api_recovered_error_protocols'] = sum(
            r['interface'] == 'API' and r['archived_api_errors'] > 0
            and r['status'] != 'technical_failure' for r in model_rows)
        summary['api_archived_error_records'] = sum(r['archived_api_errors'] for r in model_rows)
        for method, prefix in [('API', 'api'), ('Agent', 'agent')]:
            attempts = [r for r in model_rows if r['interface'] == method]
            valid = [r for r in attempts if r['prediction_parse_valid']]
            exact_n = sum(r['exact_match'] for r in attempts)
            summary.update({f'{prefix}_technical_failures': sum(r['status'] == 'technical_failure' for r in attempts),
                            f'{prefix}_invalid_outputs': sum(r['status'] == 'invalid_output' for r in attempts),
                            f'{prefix}_valid_sequence_outputs': len(valid),
                            f'{prefix}_valid_output_mean_similarity': float(np.mean([r['sequence_similarity'] for r in valid])) if valid else None,
                            f'{prefix}_exact_n': exact_n,
                            f'{prefix}_exact_attempt_denominator': len(attempts),
                            f'{prefix}_exact_attempt_rate': exact_n / len(attempts),
                            f'{prefix}_exact_valid_output_rate': exact_n / len(valid) if valid else None})
        result.append(summary)
        for (a, b), d in zip(pairs, delta, strict=True):
            paired_rows.append({'model_family': family, 'protocol_id': a['protocol_id'],
                                'api_similarity': a['sequence_similarity'], 'agent_similarity': b['sequence_similarity'],
                                'agent_minus_api': float(d), 'api_exact': a['exact_match'], 'agent_exact': b['exact_match'],
                                'api_status': a['status'], 'agent_status': b['status']})
    order = np.argsort([r['p_raw'] for r in result])
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, result[index]['p_raw'] * (len(result) - rank)))
        result[index]['p_holm'] = running
    return result, paired_rows


CAPTION = r'''\textbf{Task 1 performance for direct APIs versus native coding agents.}
Each point is one paired protocol; the dashed line denotes equal performance.
$\Delta$ is mean similarity (Agent minus API); $p_{\mathrm{adj}}$ is the
two-sided paired Wilcoxon signed-rank $p$-value with Holm correction across
three models (20 pairs per model). Saved predictions are rescored with the
original Task 1 string scorer against approved ground truth based on Hugging Face
commit \texttt{5a81714}, with human-adjudicated corrections to the 10x Feature Barcoding
RNA anchor (T(30)VN[CDNA]), inDrop v1 W1 orientation (Supplementary Table S5),
and inDrop barcode-1 lengths (8, 9, 10, or 11 bases),
projected to the original payload-omitting scoring format.
The separate statistics table reports exact matches, requiring similarity 1 for every final
library, without missing or extra libraries, out of 20 scheduled protocols per
interface. Empty-output abstentions retain the verifier's zero score
(API/Agent: Claude 0/0, GPT 1/1, Gemini 3/4). No final technical failures remained;
these would be excluded from pairs and unsuccessful for exact/20. Paired 95\%
bootstrap CIs, valid-output exact rates, and failure details accompany the figure.
The explicit VN anchor is required; the former VN-omitted reference is reported
only as a sensitivity analysis. Unexpanded reference placeholders
in SPLiT-seq and the SMART-seq PE branch retain the original scorer's literal
handling; those scores have a reference-representation limitation.
For inDrop v1, the unchanged verifier evaluates all four allowed barcode-1
lengths and retains the highest similarity; each protocol remains one observation.
Claude's SeqWell API retry used 200-dpi figures while retaining all pages and text.'''


def draw(summary, paired):
    # The published Figure 2 was Matplotlib 3.9.4 / Arial, not an R plot.
    # Recover its exact page and panel geometry from the existing vector PDF.
    # The old final source is absent; an earlier Python plotting bytecode cache
    # survives at analysis/library_structure/__pycache__/02_make_figure.*.pyc.
    from matplotlib.font_manager import FontProperties, findfont
    findfont(FontProperties(family='Arial'), fallback_to_default=False)
    ink = '#202124'
    axis_color = (0.650904119, 0.651010931, 0.650904119)
    plt.rcParams.update({'font.family': 'Arial', 'font.size': 12.5,
                         'axes.titlesize': 14.5, 'axes.labelsize': 14,
                         'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'text.color': ink, 'axes.labelcolor': ink,
                         'xtick.color': ink, 'ytick.color': ink,
                         'mathtext.fontset': 'custom', 'mathtext.rm': 'Arial',
                         'mathtext.it': 'Arial:italic', 'mathtext.bf': 'Arial:bold'})
    width_pt, height_pt = 644.1125, 235.7319
    panel_side = 166.428
    panel_bottom = height_pt - 188.229874
    fig = plt.figure(figsize=(width_pt / 72, height_pt / 72))
    for idx, (s, left_pt) in enumerate(zip(summary, [56.073086, 265.438812, 474.804504], strict=True)):
        ax = fig.add_axes([left_pt / width_pt, panel_bottom / height_pt,
                           panel_side / width_pt, panel_side / height_pt])
        ax.set_aspect('equal', adjustable='box')
        data = [r for r in paired if r['model_family'] == s['model_family']]
        ax.plot([0, 1], [0, 1], color=axis_color, lw=1.5, ls=(0, (4, 3)), zorder=1)
        ax.scatter([r['api_similarity'] for r in data], [r['agent_similarity'] for r in data],
                   s=70, c='#4682b4', edgecolors='white', linewidths=.9, alpha=.92, zorder=3)
        ax.set(xlim=(-.025, 1.035), ylim=(-.025, 1.035))
        ax.set_title(s['model_label'], pad=8)
        ticks = [0, .25, .5, .75, 1]
        ax.set_xticks(ticks, ['0', '0.25', '0.50', '0.75', '1'])
        ax.set_yticks(ticks, ['0', '0.25', '0.50', '0.75', '1'] if idx == 0 else [])
        ax.tick_params(labelsize=12.5, length=4, width=1)
        ax.set_axisbelow(True)
        ax.grid(color='#e1e4e8', lw=.9)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        for side in ('bottom', 'left'):
            ax.spines[side].set_color(axis_color)
            ax.spines[side].set_linewidth(.8)
        # New annotations occupy the otherwise empty lower-right corner.
        ax.text(.96, .08, f'$\\Delta$ = {s["mean_agent_minus_api"]:+.3f}\n'
                + f'$p_{{\\mathrm{{adj}}}}$ = {s["p_holm"]:.3f}',
                transform=ax.transAxes, ha='right', va='bottom', fontsize=11.5,
                linespacing=1.5, bbox={'facecolor': 'white', 'alpha': .9, 'edgecolor': 'none', 'pad': 2})
        assert len(ax.collections[0].get_offsets()) == s['paired_n']
    fig.text(322.15681 / width_pt, (height_pt - 226.36748) / height_pt,
             'Direct API sequence similarity', ha='center', va='center', fontsize=14)
    fig.text(9.45031 / width_pt, (height_pt - 118.28187) / height_pt,
             'Coding-agent sequence similarity', rotation=90, ha='center', va='center', fontsize=14)
    figure = OUT / 'figures/fig2_task1_api_vs_coding_agent'
    fig.savefig(figure.with_suffix('.pdf'), metadata={'Title': 'Task 1: matched-model round-2 API versus agent', 'CreationDate': None})
    plt.close(fig)


def report(summary, rows, provenance):
    lines = ['# Figure 2: Task 1, matched-version round 2', '',
             'Only the 20 protocols in `benchmarks/library_structure/round2/protocols.json` are used. '
             'Each model/interface contributes one final attempt per protocol; preserved retry archives are not extra observations. '
             'No original 13-protocol results are included.', '',
             '| Model | Paired n | Mean API / Agent | Mean Agent − API | 95% paired bootstrap CI | Wilcoxon W | Raw p | Holm p | API wins / Agent wins / ties |',
             '|---|---:|---|---:|---|---:|---:|---:|---|']
    for s in summary:
        lines.append(f'| {s["model_label"]} | {s["paired_n"]} | {s["api_mean_similarity"]:.6f} / {s["agent_mean_similarity"]:.6f} | '
                     f'{s["mean_agent_minus_api"]:+.6f} | [{s["ci95_low"]:+.6f}, {s["ci95_high"]:+.6f}] | '
                     f'{s["wilcoxon_statistic"]:g} | {s["p_raw"]:.6f} | {s["p_holm"]:.6f} | '
                     f'{s["api_wins"]} / {s["agent_wins"]} / {s["ties"]} |')
    lines += ['', 'Exact-match rates count **all final libraries**, not a selected library. '
              'Every matched ground-truth library must have zero edit distance, similarity must be exactly 1, '
              'and predicted and ground-truth library counts must agree. No current similarity-1 prediction is excluded by the count check.', '',
              '| Model | Interface | Scheduled | Technical failures | Empty-output abstentions | Valid sequence outputs | Exact / scheduled (rate) | Exact / valid outputs (rate) | Mean among valid sequence outputs |',
              '|---|---|---:|---:|---:|---:|---|---|---:|']
    for s in summary:
        for prefix, interface in [('api', 'API'), ('agent', 'Agent')]:
            lines.append(f'| {s["model_label"]} | {interface} | {s["scheduled_protocols"]} | '
                         f'{s[prefix+"_technical_failures"]} | {s[prefix+"_invalid_outputs"]} | {s[prefix+"_valid_sequence_outputs"]} | '
                         f'{s[prefix+"_exact_n"]}/{s[prefix+"_exact_attempt_denominator"]} ({s[prefix+"_exact_attempt_rate"]:.1%}) | '
                         f'{s[prefix+"_exact_n"]}/{s[prefix+"_valid_sequence_outputs"]} ({s[prefix+"_exact_valid_output_rate"]:.1%}) | '
                         f'{s[prefix+"_valid_output_mean_similarity"]:.6f} |')
    lines += ['', '## Scoring, pairing, and inference', '',
              '- The existing `grade_library_prediction` scores each ground-truth final library with normalized edit similarity and averages across final libraries within the protocol. Protocols receive equal weight. Its existing matching and normalization rules are unchanged.',
              '- The scorer, matching module, and verifier CLI are SHA-256 identical to every generated round-2 task copy. Historical scores are recomputed against their original frozen references and checked against every saved CLI result. The revised paper scores use the current approved references instead, so they intentionally differ from historical rewards.',
              '- The base snapshot was byte-verified against `sequencing/scg-oligo-groundtruth-v1` at commit `' + provenance['reference_revision'] + '`. The effective snapshot is `' + provenance['reference_snapshot'] + '`: human-approved amendments add explicit VN to 10x Feature Barcoding restore the Table S5 W1 orientation for inDrop v1, and allow its barcode-1 lengths 8, 9, 10, or 11 nt with T(19)V segment length 20. The other 18 T1 files retain their verified base hashes. These local amendments have not been uploaded to Hugging Face. `task1_reference_lock.json` pins canonical and audit-record hashes; rerunning fails if they change. `task1_reference.py` records the downstream scoring projection: omit segments annotated as biological inserts; expand known barcode/randomer roles; select the explicitly stated phase-block maximum under the original Task 1 prompt. The scoring script does not edit canonical files or predictions. Current canonical library counts and modalities are used with the unchanged legacy matching function. For the approved inDrop range, all four complete reference alternatives are generated independently of predictions. Each is scored by the unchanged verifier, then the maximum protocol similarity is retained (ties use declaration order). Alternatives are not extra libraries or replicates. All candidate scores and the selected length are recorded in the audit; the same rule applies to every model and interface.',
              '- Pairing is by explicit protocol ID. Requested/returned API models, recorded CLI models/versions, high reasoning, 64,000 API completion tokens, PDF input paths and source revision, prediction IDs, and nontruncated API responses are checked. Older API manifests do not record per-file source hashes; those source revisions rely on the frozen round-2 manifest. Reproduction uses the hash-verified local references without a network call.',
              '- Empty library arrays are real completed model outputs, not missing runs. They receive zero under the existing verifier policy and remain in the primary 20-protocol comparison. The prompt permits omission when no scored sequence can be grounded, whereas the scorer requires at least one library: this is an omission-policy/scorer mismatch. The five empty agent outputs explicitly describe abstaining for insufficient source sequence evidence; the four API outputs provide no explanation. These claims were not independently adjudicated. Valid-output-only means and exact rates are reported separately above. Technical failures have missing scores, are excluded from pairs, and are unsuccessful attempts for exact/20.',
              '- All final attempts now have zero unresolved technical failures. Preserved API error archives document subsequently recovered protocol/model cases: ' + ', '.join(f'{s["model_family"]} {s["api_recovered_error_protocols"]}' for s in summary) + '. The CSV separately counts error archive records; these may include multiple retries of one case and are not independent protocol observations. Historical CLI retries are not reconstructed here.',
              f'- The two-sided Wilcoxon signed-rank test uses exhaustive sign permutations over nonzero paired differences (zero_method=wilcox). Differences are rounded to 12 decimals only for ranks and tie recognition. This tests the symmetric difference distribution about zero; it is not a direct test of the mean. Holm correction covers exactly three model comparisons. Nonzero pair counts: ' + ', '.join(f'{s["model_family"]} {s["wilcoxon_nonzero_n"]}' for s in summary) + '.',
              f'- 95% percentile bootstrap intervals use {BOOTSTRAP_N:,} paired protocol resamples, including ties, with NumPy default_rng seed {SEED} reset for each model. Intervals estimate the mean Agent − API difference and are not adjusted across models. They describe protocol sampling variability, not run-to-run model variability.',
              '- API exact matches / Agent exact matches in the separate statistics table are counts out of 20 scheduled protocols per interface. Missing, technical, or invalid outputs never qualify as exact matches. The figure’s means include existing zero-scored output failures; the valid-output-only means above do not.',
              '- Holm-adjusted p-values below 0.05: ' + str(sum(s['p_holm'] < 0.05 for s in summary)) + ' of 3 comparisons. Failure to reject a difference is not evidence of equivalence.', '',
              'Statistical reference: [SciPy Wilcoxon documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.wilcoxon.html).', '',
              '## Output failures (not technical failures)', '']
    for r in rows:
        if r['status'] != 'ok':
            lines.append(f'- {r["model_family"]} / {r["interface"]} / `{r["protocol_id"]}`: {r["status"]}; {r["error"]}.')
    lines += ['', '## Reference correction and remaining limitations', '',
              'The historical round-2 generation command explicitly selected `--groundtruth-dir analysis/library_structure/groundtruth`, overriding remote label retrieval. The former figure script reused those task copies. This was an outdated local reference selection, not an upload failure. The historical tasks and rewards remain immutable run records; the paper analysis now uses the pinned current approved references.', '',
              'Scores changed for ' + str(len({r['protocol_id'] for r in rows if abs(r['similarity_change_from_historical'] or 0) > 1e-12})) + ' protocols. `task1_reference_comparison.csv` lists every protocol, old/new library counts and hashes, and unresolved spans. Per-attempt historical scores and changes are in `task1_grades_long.csv`.', '',
              '**Unresolved reference representations:** No sequence or length is invented for the following spans. The unchanged v0 scorer retains their literal bracket notation, which is a limitation of these strict string scores, not a nucleotide-level biological assessment. They remain in the 20-protocol analysis, and cannot be declared newly resolved by this rescore:']
    for pid, info in provenance['groundtruth'].items():
        for issue in info['projection']['unresolved_placeholders']:
            lines.append(f'- `{pid}`, library {issue["library_index"] + 1}, `{issue["segment_id"]}`: `{issue["placeholder"]}`.')
    lines += ['', '**VN source conflict, adjudicated 2026-10-10:** The supplied 10x Feature Barcoding guide (`10xChromium3fb.pdf`, SHA-256 `4b03b7d4448836f839e0cbedb8012e402a99b152b6a23a8046fabb0c5381a819`) shows `Poly(dT)VN` in the final-library diagrams (pp.18, 56), while Appendix pp.65–66 omits VN in the literal strings. The user explicitly approved `T(30)VN[CDNA]`; the decision and deterministic application are hash-pinned in `task1_reference_lock.json`. The correction propagates through canonical T1/T2/T3 and local upload staging. Model agreement is not independent source evidence.', '',
              'The primary analysis now **requires VN** at this boundary. `task1_vn_sensitivity.csv` reports the former VN-omitted reference only as an alternative. `task1_vn_sensitivity_summary.csv` reruns the full paired analysis under that historical alternative. No other protocol, library, prediction, or scorer rule changes between the two analyses.', '',
              '| Model | Interface | Approved explicit-VN similarity / exact | Historical VN-omitted similarity / exact |',
              '|---|---|---|---|']
    for r in rows:
        if r['protocol_id'] == '10x_chromium_3_feature_barcoding':
            lines.append(f'| {r["model_family"]} | {r["interface"]} | {r["sequence_similarity"]:.6f} / {r["exact_match"]} | '
                         f'{r["vn_omitted_sensitivity_similarity"]:.6f} / {r["vn_omitted_sensitivity_exact"]} |')
    lines += ['', '**inDrop v1 W1 correction, adjudicated 2026-10-10:** The final-library reference strand now carries `GAGTGATTGCTTGTGACGCCTT`, as directly listed in the fully assembled primer in Supplementary Table S5 (p.7). Barcode-synthesis primers and the FAM-W1* probe carry its reverse complement, `AAGGCGTCACAAGCAATCACTC`; the assembly mechanism (supplement p.10) and Figure 2D/legend support that relationship. Figure 2E prints the opposite sequence and remains recorded as conflicting evidence. The user approved restoring the Table S5 orientation across T1/T2/T3 and local upload staging. This W1 amendment preserved T(19)V; the later barcode-length amendment is described below. The immutable proposal, decision, application and installation receipt are pinned in the reference lock. No prediction or historical reward was rewritten.', '',
              '**inDrop barcode-length amendment, applied 2026-10-10:** The accepted canonical records allow barcode 1 lengths of 8, 9, 10, or 11 nt; the T(19)V segment declares its full length of 20. The hash-pinned amendment includes the author parser and whitelist evidence, human decision, deterministic application, and installation receipt. The main analysis now evaluates every allowed length with the existing string scorer and takes the highest similarity, with no changes to predictions. This replaces the preceding fixed-10 scoring convention. The saved verifier receipt independently runs the CLI against every alternative and checks the selected score.', '',
              '**Scope of this refresh:** scRRBS, PIP-seq v4, and scDamID retain their currently installed canonical records. The subsequent source-review proposals were not applied as canonical amendments. Their previously identified source/representation limitations therefore remain; these strict scores should not be described as newly adjudicated biological correctness.', '',
              '## Input and runtime provenance', '',
              'All conditions use the same 20-protocol PDF-only source set; excluded XLSX attachments are not supplied. '
              'Source revision: `' + provenance['input_revision'] + '`. Both interfaces request high reasoning, '
              'but agent tool calls and compute are not equalized with the 64,000-token API cap.', '',
              '| Model | API model | Agent | Recorded CLI version |', '|---|---|---|---|']
    for family, title, model, job, agent, agent_model, version in MODELS:
        lines.append(f'| {title} | `{model}` | `{agent}` / `{agent_model}` | {version} |')
    lines += ['', '**Input-representation exception:** Claude SeqWell API figures were downsampled to 200 dpi '
              'to fit the provider’s 30 MB limit, keeping all 79 pages and preserving extracted text and positions '
              'within the recorded tolerance. Gemini retry PDFs were losslessly optimized (including SeqWell and Microwell). '
              'These runs use the same source evidence but do not all receive byte-identical PDFs. '
              'Per-file transformations and hashes are in `task1_provenance.json`.', '',
              '## Figure caption', '', CAPTION, '',
              '## Reproduce', '', '```bash',
              'python analysis/paper_results/prepare_task1_paper_data.py', 'python analysis/paper_results/export_task1_sequences.py', 'python analysis/paper_results/verify_task1_saved_outputs.py', '```', '',
              'Outputs remain in the existing `analysis/paper_results/` and `figures/` directories. '
              'The script does not alter raw predictions or ground truth.']
    (OUT / 'task1_statistics.md').write_text('\n'.join(lines) + '\n')


def main():
    rows, audits, provenance = collect()
    summary, paired = summarize(rows)
    provenance['analysis_script_sha256'] = sha(Path(__file__))
    write_csv(OUT / 'task1_grades_long.csv', rows)
    write_csv(OUT / 'task1_paired_protocols.csv', paired)
    write_csv(OUT / 'task1_summary.csv', summary)
    comparison = []
    for pid, info in provenance['groundtruth'].items():
        changed = [r for r in rows if r['protocol_id'] == pid and abs(r['similarity_change_from_historical'] or 0) > 1e-12]
        comparison.append({'protocol_id': pid, 'reference_revision': provenance['reference_revision'],
                           'reference_snapshot': provenance['reference_snapshot'],
                           'canonical_sha256': info['sha256'], 'historical_sha256': info['historical_sha256'],
                           'canonical_library_count': info['canonical_library_count'],
                           'historical_library_count': info['historical_library_count'],
                           'changed_attempt_scores': len(changed),
                           'unresolved_placeholders': json.dumps(info['projection']['unresolved_placeholders'])})
    write_csv(OUT / 'task1_reference_comparison.csv', comparison)
    vn_rows = [dict(r, sequence_similarity=r['vn_omitted_sensitivity_similarity'],
                    exact_match=r['vn_omitted_sensitivity_exact']) for r in rows]
    vn_summary, _ = summarize(vn_rows)
    write_csv(OUT / 'task1_vn_sensitivity_summary.csv', vn_summary)
    write_csv(OUT / 'task1_vn_sensitivity.csv', [r for r in rows if r['protocol_id'] == '10x_chromium_3_feature_barcoding'])
    (OUT / 'task1_grades_long.json').write_text(json.dumps(audits, indent=2) + '\n')
    (OUT / 'task1_provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    (OUT / 'task1_caption.tex').write_text(CAPTION + '\n')
    report(summary, rows, provenance)
    draw(summary, paired)
    print(json.dumps(summary, indent=2))
    print('Verified 20 reference hashes, amendment audit hashes, 120 final outcomes, and all historical CLI scores. Explicit VN is required; omission is sensitivity-only.')


if __name__ == '__main__':
    main()
