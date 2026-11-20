#!/usr/bin/env python3
"""Analyze the protocol-name-only control without changing saved predictions or labels."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from libstruct_bench.cli import run_task1_name_only as control
from libstruct_bench.library_structure import LibraryStructureValidationError, zero_metrics
from task1_reference import project_reference_variants, grade_reference_variants
# Reuse the Figure 2 exact-match definition and plotting environment.
from prepare_task1_paper_data import is_exact, write_csv, np, plt, matplotlib

OUT = ROOT / 'analysis/paper_results'
SEED = 20261010
RESAMPLES = 100_000
EXCLUDED = {'missing', 'technical_failure', 'condition_violation', 'interrupted'}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def references(config):
    lock = control.read(ROOT / config['reference_lock'])
    variants = {}
    for amendment in lock['amendments']:
        for artifact in amendment['artifacts'].values():
            check(control.sha(artifact['path']) == artifact['sha256'], 'Approved amendment record changed')
    by_id = {p['protocol_id']: p for p in config['protocols']}
    for entry in lock['files']:
        pid = entry['protocol_id']
        path = Path(lock['canonical_root']) / pid / 'groundtruth_final_lib_struct.json'
        check(control.sha(path) == entry['sha256'], f'Approved reference changed: {pid}')
        document = control.read(path)
        check(document['protocol_name'] == by_id[pid]['canonical_name'], f'Canonical name mismatch: {pid}')
        check(document.get('protocol_scope', {}).get('protocol_version') == by_id[pid]['version'],
              f'Canonical version mismatch: {pid}')
        variants[pid] = project_reference_variants(document, lock['length_alternatives'])
    check(set(variants) == set(by_id), 'Reference set differs from the 20 protocols')
    return variants, lock


def score(prediction, variants, pid, status='completed', error=''):
    result = {'status': status, 'similarity': None, 'exact': False, 'abstention': False,
              'valid_sequence_output': False, 'invalid_output': False, 'error': error}
    if status in EXCLUDED:
        return result, {}
    result['abstention'] = isinstance(prediction, dict) and prediction.get('libraries') == []
    audit = {}
    try:
        if prediction is None:
            raise LibraryStructureValidationError(error or 'No parseable prediction')
        metrics, audit = grade_reference_variants(prediction, variants, expected_protocol_id=pid)
        result.update(status='ok', valid_sequence_output=True, exact=is_exact(metrics, audit))
    except LibraryStructureValidationError as exc:
        metrics = zero_metrics()
        result.update(status='abstention' if result['abstention'] else 'invalid_output',
                      invalid_output=not result['abstention'], error=str(exc))
    result['similarity'] = metrics['sequence_similarity']
    return result, {'metrics': metrics, 'audit': audit}


def pdf_baseline(config, variants):
    provenance = control.read(ROOT / config['pdf_provenance'])
    check(provenance['reference_lock_sha256'] == control.sha(ROOT / config['reference_lock']),
          'PDF scores use a different reference lock')
    run_info = {(r['model_family'], r['protocol_id']): r for r in provenance['runs'] if r['interface'] == 'API'}
    with (ROOT / config['pdf_scores']).open(newline='') as stream:
        rows = [r for r in csv.DictReader(stream) if r['interface'] == 'API']
    check(len(rows) == 60, 'Expected exactly 60 PDF direct API cases')
    results, audits = {}, {}
    for r in rows:
        model, pid = r['model'], r['protocol_id']
        check(model in control.MODELS and pid in variants, 'Unexpected PDF model/protocol')
        check((model, pid) not in results, 'Duplicate PDF model/protocol')
        check(r['model_family'] == control.MODELS[model][0], 'Mismatched PDF family')
        prediction_path = ROOT / r['prediction_path']
        check(control.sha(prediction_path) == r['prediction_sha256'], f'PDF prediction changed: {prediction_path}')
        info = run_info[(r['model_family'], pid)]
        case = prediction_path.parent
        hashes = {}
        for filename, key in [('request.json', 'request_sha256'), ('response.json', 'response_sha256'),
                              ('input_files.json', 'input_files_sha256')]:
            hashes[filename] = control.sha(case / filename)
            check(hashes[filename] == info[key], f'PDF baseline artifact changed: {case / filename}')
        check(info['response_model'] == model and info['reasoning_effort'] == 'high'
              and info['max_completion_tokens'] == 64000 and info['finish_reason'] == 'stop',
              'PDF model/settings/completion mismatch')
        result, audit = score(control.read(prediction_path), variants[pid], pid)
        check(abs(result['similarity'] - float(r['sequence_similarity'])) < 1e-12,
              f'Rescored PDF similarity disagrees with locked Figure 2 data: {model}/{pid}')
        check(result['exact'] == (r['exact_match'] == 'True'), 'PDF exact-match definition differs')
        hashes['prediction.json'] = r['prediction_sha256']
        results[(model, pid)] = result
        audits[(model, pid)] = {'files_sha256': hashes, **audit}
    return results, audits


def name_only_case(root, protocol, model, variants):
    pid = protocol['protocol_id']
    case = root / control.api._slug(model) / pid
    metadata = {'case_path': str(case), 'files_sha256': {}}
    for name in ('request.json', 'attempt.json', 'response.json', 'prediction.json', 'result.json',
                 'raw_response.txt', 'failed_response.txt'):
        if (case / name).exists():
            metadata['files_sha256'][name] = control.sha(case / name)
    if not (case / 'attempt.json').exists():
        return *score(None, variants, pid, 'missing', 'No API attempt recorded'), metadata
    attempt = control.read(case / 'attempt.json')
    provider = attempt.get('provider', 'openrouter')
    check(control.read(case / 'request.json') == control.payload(protocol, model, provider), f'Control request differs: {case}')
    metadata.update(provider=provider, project=attempt.get('project'), sandboxed=attempt.get('sandboxed', False))
    if provider == 'vertex':
        check(attempt.get('project') == control.read(root / 'experiment.json').get('gemini_transport', {}).get('project'),
              'Vertex project differs from recorded transport amendment')
    check(attempt['model'] == model and attempt['protocol_id'] == pid, 'Attempt identity mismatch')
    prediction = None
    if (case / 'response.json').exists():
        interpreter = control.interpret_vertex if provider == 'vertex' else control.interpret_response
        result, prediction, text = interpreter(control.read(case / 'response.json'), model, pid)
        if (case / 'prediction.json').exists():
            check(control.read(case / 'prediction.json') == prediction, f'Prediction differs from raw response: {case}')
        if (case / 'raw_response.txt').exists():
            check((case / 'raw_response.txt').read_text() == text, f'Raw response differs: {case}')
    elif (case / 'result.json').exists():
        result = control.read(case / 'result.json')
        check(result['status'] in ('technical_failure', 'interrupted'), f'Missing response for completed case: {case}')
    else:
        result = {'status': 'interrupted', 'error': 'Attempt started but no terminal response recorded; outcome unknown'}
    scored, audit = score(prediction, variants, pid, result['status'], result['error'])
    return scored, audit, metadata


def collect(root, config, allow_incomplete=False):
    experiment = control.validate_experiment(root, config)
    variants, reference_lock = references(config)
    baseline, baseline_audits = pdf_baseline(config, variants)
    rows, audits = [], []
    for model, (family, title) in control.MODELS.items():
        for protocol in config['protocols']:
            pid = protocol['protocol_id']
            name, audit, meta = name_only_case(root, protocol, model, variants[pid])
            pdf = baseline[(model, pid)]
            row = {'model_family': family, 'model_label': title, 'model': model, 'protocol_id': pid,
                   'canonical_name': protocol['canonical_name'], 'version': protocol['version'],
                   'method_group': protocol['method_group'],
                   'name_only_provider': meta.get('provider'), 'name_only_project': meta.get('project'),
                   'name_only_sandboxed': meta.get('sandboxed')}
            for prefix, result in [('pdf', pdf), ('name_only', name)]:
                row.update({prefix + '_' + k: v for k, v in result.items()})
            row['paired'] = pdf['similarity'] is not None and name['similarity'] is not None
            row['pdf_minus_name_only'] = pdf['similarity'] - name['similarity'] if row['paired'] else None
            rows.append(row)
            audits.append({'model': model, 'protocol_id': pid, 'pdf': baseline_audits[(model, pid)],
                           'name_only': {**meta, **audit}})
    missing = [r for r in rows if r['name_only_status'] == 'missing']
    check(allow_incomplete or not missing,
          f'{len(missing)} cases have no attempt yet. Run the control first; --allow-incomplete is for explicitly labeled previews.')
    return rows, {'condition': control.CONDITION, 'reference_snapshot': reference_lock['snapshot'],
                  'experiment_sha256': control.sha(root / 'experiment.json'),
                  'analysis_script_sha256': control.sha(__file__),
                  'transport_amendments': experiment.get('amendments', []),
                  'gemini_transport': experiment.get('gemini_transport'),
                  'sandbox_receipt_sha256': control.sha(root / 'sandbox.json') if (root / 'sandbox.json').exists() else None,
                  'exact_match_helper_sha256': control.sha(OUT / 'prepare_task1_paper_data.py'),
                  'bootstrap': {'unit': 'paired protocol', 'method': 'percentile', 'seed': SEED, 'resamples': RESAMPLES},
                  'versions': {'numpy': np.__version__, 'matplotlib': matplotlib.__version__},
                  'complete': not missing, 'runs': audits}


def summarize(rows, resamples=RESAMPLES):
    summaries = []
    for model, (_, title) in control.MODELS.items():
        for group_index, group in enumerate(('all', 'commercial', 'academic')):
            cases = [r for r in rows if r['model'] == model and (group == 'all' or r['method_group'] == group)]
            pairs = [r for r in cases if r['paired']]
            delta = np.array([r['pdf_minus_name_only'] for r in pairs])
            s = {'model': model, 'model_label': title, 'method_group': group,
                 'scheduled_n': len(cases), 'paired_n': len(pairs)}
            for prefix in ('pdf', 'name_only'):
                eligible = [r for r in cases if r[prefix + '_similarity'] is not None]
                valid = [r for r in cases if r[prefix + '_valid_sequence_output']]
                exact = sum(r[prefix + '_exact'] for r in cases)
                s.update({prefix + '_scored_n': len(eligible),
                          prefix + '_mean_similarity': float(np.mean([r[prefix + '_similarity'] for r in eligible])) if eligible else None,
                          prefix + '_paired_mean_similarity': float(np.mean([r[prefix + '_similarity'] for r in pairs])) if pairs else None,
                          prefix + '_exact_n': exact,
                          prefix + '_exact_attempt_rate': exact / len(cases) if cases else None,
                          prefix + '_valid_sequence_n': len(valid),
                          prefix + '_valid_sequence_mean_similarity': float(np.mean([r[prefix + '_similarity'] for r in valid])) if valid else None,
                          prefix + '_exact_valid_rate': exact / len(valid) if valid else None,
                          prefix + '_abstentions': sum(r[prefix + '_abstention'] for r in cases),
                          prefix + '_abstention_attempt_rate': sum(r[prefix + '_abstention'] for r in cases) / len(cases) if cases else None,
                          prefix + '_invalid_outputs': sum(r[prefix + '_invalid_output'] for r in cases),
                          prefix + '_technical_failures': sum(r[prefix + '_status'] == 'technical_failure' for r in cases),
                          prefix + '_condition_violations': sum(r[prefix + '_status'] == 'condition_violation' for r in cases),
                          prefix + '_interrupted': sum(r[prefix + '_status'] == 'interrupted' for r in cases),
                          prefix + '_missing': sum(r[prefix + '_status'] == 'missing' for r in cases)})
            ci = (None, None)
            if len(delta):
                # One bootstrap draw selects paired protocols, preserving their two condition scores.
                # Shared seed/draws across models in a stratum avoids Monte Carlo differences when paired sets agree.
                rng = np.random.default_rng(SEED + group_index)
                boot = delta[rng.integers(0, len(delta), size=(resamples, len(delta)))].mean(axis=1)
                ci = tuple(float(v) for v in np.quantile(boot, [0.025, 0.975]))
            ranked = np.round(delta, 12)
            s.update(mean_pdf_minus_name_only=float(delta.mean()) if len(delta) else None,
                     ci95_low=ci[0], ci95_high=ci[1], pdf_wins=int(sum(ranked > 0)),
                     name_only_wins=int(sum(ranked < 0)), ties=int(sum(ranked == 0)))
            summaries.append(s)
    return summaries


def draw(rows, summaries, path, complete=True):
    plt.rcParams.update({'font.family': 'Arial', 'font.size': 11, 'axes.titlesize': 14,
                         'axes.labelsize': 13, 'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'text.color': '#202124', 'axes.labelcolor': '#202124'})
    fig, axes = plt.subplots(1, 3, figsize=(9, 3.9))
    for ax, s in zip(axes, [s for s in summaries if s['method_group'] == 'all'], strict=True):
        pairs = [r for r in rows if r['model'] == s['model'] and r['paired']]
        ax.plot([0, 1], [0, 1], color='#a6a6a6', lw=1.5, ls=(0, (4, 3)), zorder=1)
        ax.scatter([r['name_only_similarity'] for r in pairs], [r['pdf_similarity'] for r in pairs],
                   s=60, c='#4682b4', edgecolors='white', linewidths=.9, alpha=.92, zorder=3)
        ax.set(xlim=(-.025, 1.035), ylim=(-.025, 1.035), title=s['model_label'], aspect='equal')
        ax.set_title(s['model_label'], pad=37)
        ticks = [0, .25, .5, .75, 1]
        ax.set_xticks(ticks, ['0', '0.25', '0.50', '0.75', '1'])
        ax.set_yticks(ticks, ['0', '0.25', '0.50', '0.75', '1'] if ax is axes[0] else [])
        ax.set_axisbelow(True)
        ax.grid(color='#e1e4e8', lw=.9)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        for side in ('left', 'bottom'):
            ax.spines[side].set_color('#a6a6a6')
            ax.spines[side].set_linewidth(.8)
        annotation = (f"Δ = {s['mean_pdf_minus_name_only']:+.3f}; n = {s['paired_n']}\n"
                      f"95% CI [{s['ci95_low']:+.3f}, {s['ci95_high']:+.3f}]"
                      if pairs else 'No paired observations')
        # Keep statistics outside the data rectangle so no protocol is hidden by a label.
        ax.text(.5, 1.025, annotation, transform=ax.transAxes, ha='center', va='bottom', fontsize=9,
                linespacing=1.35)
    fig.suptitle(('PREVIEW — ' if not complete else '') + control.CONDITION, fontsize=13, y=.98)
    fig.supxlabel('Protocol-name-only sequence similarity', fontsize=13, y=.035)
    fig.supylabel('PDF-provided sequence similarity', fontsize=13, x=.018)
    fig.subplots_adjust(left=.085, right=.985, bottom=.18, top=.74, wspace=.15)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, metadata={'Title': control.CONDITION, 'CreationDate': None})
    plt.close(fig)


def formatted(value):
    return 'NA' if value is None else f'{value:.3f}'


def transport_note(rows):
    counts = {}
    for row in rows:
        provider = row.get('name_only_provider')
        if provider:
            key = (row['model_family'], provider)
            counts[key] = counts.get(key, 0) + 1
    text = 'Recorded name-only attempt routes: ' + ', '.join(
        f'{family} via {provider}: {n}' for (family, provider), n in sorted(counts.items())) + '. '
    if any(provider == 'vertex' for _, provider in counts):
        text += 'Gemini uses Vertex AI. '
        if counts.get(('Gemini', 'openrouter')):
            text += 'Earlier OpenRouter attempts are retained in this comparison. '
        text += ('The PDF baseline uses OpenRouter. '
                 'These are matched model versions, but provider/API routes differ and may affect the comparison. ')
    return text


def restart_note(provenance):
    restarts = [a for a in provenance['transport_amendments'] if a.get('kind') == 'run_restart']
    if not restarts:
        return ''
    return ('Gemini was explicitly restarted for all 20 protocols through Vertex AI. '
            'Two prior OpenRouter attempts (one completed, one interrupted) were retired after their '
            'artifact folder was removed; their surviving metadata is retained in the experiment history, '
            'and they are excluded from this comparison. The current run uses one attempt per protocol. ')


def report(rows, summaries, config, provenance):
    lines = ['# Task 1 protocol-name-only (parametric-knowledge control)', '',
             '**Complete 60-case analysis.**' if provenance['complete'] else '**INCOMPLETE PREVIEW: missing cases are not scores.**', '',
             'One independent name-only API attempt per model/protocol. Only canonical name/version varies across '
             'requests; generic Task 1 schema and reconstruction instructions are shared. All models request high '
             'reasoning and a 64,000-token completion cap. No documents, history, tools or retrieval are supplied. '
             'This tests reconstruction from parametric knowledge under these instructions; it does not directly '
             'measure training-data memorization. The PDF condition also differs in its evidence-grounding instruction.', '',
             f"Reference: `{config['reference_snapshot']}`. The approved VN and inDrop amendments and all permitted "
             'inDrop length alternatives are applied identically to both conditions, using the existing verifier. '
             'No predictions or ground-truth files are modified.', '',
             transport_note(rows) + restart_note(provenance), '',
             '## Paired comparisons and exploratory groups', '',
             '| Model | Group | Paired n | Mean PDF / name-only (paired) | PDF − name-only | 95% paired bootstrap CI | PDF wins / name-only wins / ties |',
             '|---|---|---:|---|---|---|---|']
    for s in summaries:
        lines.append(f"| {s['model_label']} | {s['method_group']} | {s['paired_n']} | "
                     f"{formatted(s['pdf_paired_mean_similarity'])} / {formatted(s['name_only_paired_mean_similarity'])} | "
                     f"{formatted(s['mean_pdf_minus_name_only'])} | [{formatted(s['ci95_low'])}, {formatted(s['ci95_high'])}] | "
                     f"{s['pdf_wins']} / {s['name_only_wins']} / {s['ties']} |")
    lines += ['', config['group_definition'], '',
              'Subgroup estimates are exploratory and use only 5 commercial and 15 academic protocols per model '
              'before exclusions. No causal commercial/academic interpretation or multiplicity-adjusted subgroup claim is made.', '',
              '## Outcome denominators', '',
              '| Model | Condition | Scored n / scheduled | Mean similarity | Exact / scheduled (rate) | Exact / valid sequence outputs (rate) | Abstentions | Other invalid outputs | Technical / interrupted / condition violations / missing |',
              '|---|---|---|---|---|---|---:|---:|---|']
    for s in summaries:
        if s['method_group'] != 'all':
            continue
        for p in ('pdf', 'name_only'):
            lines.append(f"| {s['model_label']} | {p} | {s[p+'_scored_n']}/{s['scheduled_n']} | "
                         f"{formatted(s[p+'_mean_similarity'])} | {s[p+'_exact_n']}/{s['scheduled_n']} ({formatted(s[p+'_exact_attempt_rate'])}) | "
                         f"{s[p+'_exact_n']}/{s[p+'_valid_sequence_n']} ({formatted(s[p+'_exact_valid_rate'])}) | "
                         f"{s[p+'_abstentions']} | {s[p+'_invalid_outputs']} | {s[p+'_technical_failures']} / "
                         f"{s[p+'_interrupted']} / {s[p+'_condition_violations']} / {s[p+'_missing']} |")
    lines += ['', 'Abstention means an actual `libraries: []` output; partial omissions are handled by the existing '
              'scorer. Abstentions and other generated invalid outputs receive zero, matching Task 1 verifier behavior. '
              'Transport failures, uncertain interrupted requests, and condition violations have no sequence score '
              'and are excluded from paired comparisons. They remain unsuccessful scheduled attempts for exact-match '
              'rates. Missing cases are shown only in explicitly incomplete previews, never imputed as zero similarity. '
              'The overall mean uses scored protocols (including abstentions); the paired mean may have a different '
              'denominator. Exactness requires similarity 1, equal predicted/reference library counts, and every '
              'final library matched with edit distance zero.', '',
              f'95% percentile intervals resample paired protocols {RESAMPLES:,} times with seed {SEED} '
              '(group seed offsets 0/1/2 for all/commercial/academic). Protocols, not libraries or bootstrap draws, '
              'are independent observations. Win/tie comparisons round differences to 12 decimal places. '
              'There is one control attempt per case: intervals reflect variation across the sampled protocols, '
              'not repeated-generation uncertainty. The existing PDF comparison uses its saved final results after '
              'historical infrastructure retries; raw PDF hashes and transport transformations remain in the locked '
              'Figure 2 provenance. Provider routing and equal effort labels do not guarantee equal internal compute.', '',
              '## Protocol-level results', '',
              '| Model | Protocol | Group | PDF | Name-only | PDF − name-only | PDF exact | Name-only exact | Name-only status |',
              '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['model_family']} | {r['protocol_id']} | {r['method_group']} | {formatted(r['pdf_similarity'])} | "
                     f"{formatted(r['name_only_similarity'])} | {formatted(r['pdf_minus_name_only'])} | "
                     f"{r['pdf_exact']} | {r['name_only_exact']} | {r['name_only_status']} |")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, default=ROOT / 'runs/library_structure/round2/name_only')
    parser.add_argument('--protocols', type=Path, default=control.DEFAULT_CONFIG)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--allow-incomplete', action='store_true', help='Explicitly label outputs as incomplete previews')
    args = parser.parse_args(argv)
    config = control.load_config(args.protocols)
    rows, provenance = collect(args.runs, config, args.allow_incomplete)
    summaries = summarize(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / 'task1_name_only_protocols.csv', rows)
    write_csv(args.out / 'task1_name_only_summary.csv', summaries)
    control.write(args.out / 'task1_name_only_provenance.json', provenance)
    (args.out / 'task1_name_only_statistics.md').write_text(report(rows, summaries, config, provenance))
    (args.out / 'task1_name_only_caption.tex').write_text(
        r'\textbf{Protocol-name-only (parametric-knowledge control).} Matched Claude Opus 5, GPT-5.6 Sol, '
        r'and Gemini 3.7 Flash direct APIs reconstruct Task 1 final libraries from canonical protocol name/version '
        r'alone, versus the existing PDF-provided condition. Both request high reasoning and a 64,000-token cap. '
        r'Each point is one protocol with scores in both conditions; coincident points may overlap. '
        r'The dashed line denotes identity; $\Delta$ is the paired mean PDF-minus-name-only similarity difference, '
        r'with a 95\% percentile CI from 100,000 paired protocol-bootstrap resamples; $n$ is the paired denominator. '
        r'The unchanged Task 1 verifier uses the locked human-approved VN/inDrop reference amendments. '
        r'Actual empty-library abstentions and generated invalid outputs score zero; technical failures, '
        r'interrupted requests, and condition violations are excluded from paired comparisons and reported separately. '
        r'The accompanying table reports exact matches across all final libraries, both per 20 scheduled attempts '
        r'(failures unsuccessful) and per valid sequence output. Commercial/academic summaries are exploratory. '
        r'This condition assesses reconstruction from parametric knowledge under the task instructions and does '
        r'not directly establish training-data memorization. ' + transport_note(rows) + restart_note(provenance) + '\n')
    target = args.out / 'figures/figS_task1_name_only.pdf'
    draw(rows, summaries, target, provenance['complete'])
    print(f'Wrote {target} and task1_name_only_*.csv/.md/.json plus caption; no model calls or source edits.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
