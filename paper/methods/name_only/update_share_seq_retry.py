"""Rescore one saved retry and update the supplied portrait table.

Writes a new analysis directory; never changes historical scores or the paper.
The authentication-error job is removed separately, after inspection.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from finalize_tables import DATA, PAPER, PROTOCOLS, SYSTEMS, METRICS, LABELS, sha, mean
from summarize import ROOT, score

OUT = DATA.parent / 'results_share_seq_retry_v1'
TRIAL = ROOT / 'runs/libgen/claude-code/libgen-claude-code-opus-5-name-only/share_seq__wxw7rYi'
EXPERIMENT = ROOT / 'benchmarks/libgen/name_only/experiment.json'
TABLE_NAME = 'name_only_combined_tasks1-3.tex'


def read(name):
    with (DATA / name).open(newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def aggregate(rows):
    output = []
    for system, model, _, _ in SYSTEMS:
        for metric, label in zip(METRICS, LABELS):
            pairs = [r for r in rows if r['system'] == system and r['metric'] == metric
                     and r['eligible'] == 'True' and r['valid_pair'] == 'True']
            b = mean([float(r['bundle_n3']) for r in pairs])
            n = mean([float(r['name_only']) for r in pairs])
            output.append(dict(system=system, model=model, metric=metric, label=label,
                               n_protocols=len(pairs), bundle_mean=b, name_only_mean=n,
                               name_minus_bundle=n-b))
    return output


def pair(a, b):
    # Preserve the supplied table's convention: displayed ties are unbolded.
    a, b = f'{float(a):.3f}', f'{float(b):.3f}'
    if float(a) > float(b):
        a = r'\textbf{' + a + '}'
    elif float(b) > float(a):
        b = r'\textbf{' + b + '}'
    return [a, b]


def row(cells, keep=False):
    return ' & '.join(cells) + r' \\' + ('*' if keep else '')


def table(rows, summary, recovered):
    native = {(r['system'], r['protocol_id'], r['metric']): r for r in rows}
    sums = {(r['system'], r['metric']): r for r in summary}
    t1 = {(r['model_family'], r['protocol_id']): r for r in read('task1_per_protocol.csv')}
    t1sum = {r['model']: r for r in read('task1_summary.csv')}
    header = r'''\toprule
 & & \multicolumn{2}{c}{\textbf{Task~1}} & \multicolumn{2}{c}{\textbf{Task~2}} & \multicolumn{6}{c}{\textbf{Task~3}} \\
\cmidrule(lr){3-4}\cmidrule(lr){5-6}\cmidrule(l){7-12}
 & & \multicolumn{2}{c}{Similarity} & \multicolumn{2}{c}{Oligo F1} & \multicolumn{2}{c}{Transition F1} & \multicolumn{2}{c}{State F1} & \multicolumn{2}{c}{Edge F1} \\
\cmidrule(lr){3-4}\cmidrule(lr){5-6}\cmidrule(lr){7-8}\cmidrule(lr){9-10}\cmidrule(l){11-12}
Protocol & Model & PDF & Name & Bundle & Name & Bundle & Name & Bundle & Name & Bundle & Name \\
\midrule'''
    caption_end = (
        r'-- indicates excluded ddSEQ oligo scores. Claude SHARE-seq uses a successful name-only retry.'
        if recovered else
        r'-- indicates excluded ddSEQ oligo scores and F the failed Claude SHARE-seq name-only output.')
    lines = [r'''% Single portrait longtable, standard NeurIPS text block (no landscape).
% Requires \usepackage{booktabs,longtable,array} in the preamble.
% Use with \input{tables/name_only_combined_tasks1-3.tex}; do not wrap in table.
\begingroup
\fontsize{8.1pt}{9.4pt}\selectfont
\setlength{\tabcolsep}{1.65pt}
\renewcommand{\arraystretch}{1.01}
\setlength{\LTcapwidth}{\linewidth}
\begin{longtable}{@{}>{\raggedright\arraybackslash}p{0.235\linewidth}l*{10}{r}@{}}
\caption{%
\textbf{Protocol-name-only reconstruction across Tasks~1--3.}
Task~1 reports API sequence similarity (PDF versus Name);
Tasks~2--3 report native-agent F1 (Bundle versus Name),
with Bundle averaged over three runs and Name from one.
Bold indicates the higher observed score within a pair; ties are unbolded.
Macro means use valid pairs; $\Delta$ is Name minus PDF/Bundle.''',
        caption_end + '}', r'\label{tab:name-only-all-tasks}\\', header,
        r'\endfirsthead',
        r'\multicolumn{12}{@{}l}{\textit{Table~\thetable\ (continued)}}\\[2pt]',
        header, r'\endhead', r'\midrule',
        r'\multicolumn{12}{r@{}}{\textit{Continued on next page}}\\',
        r'\endfoot', r'\bottomrule', r'\endlastfoot']
    for pid, label in PROTOCOLS.items():
        label = label.replace("3$'$", "3'")
        for i, (system, model, _, _) in enumerate(SYSTEMS):
            t = t1[model, pid]
            cells = [r'\textbf{' + label + '}' if i == 0 else '', model]
            cells += pair(t['pdf_similarity'], t['name_only_similarity'])
            for metric in METRICS:
                r = native[system, pid, metric]
                if r['eligible'] != 'True':
                    cells += [r'\multicolumn{1}{c}{--}'] * 2
                elif r['valid_pair'] != 'True':
                    assert system == 'claude-code' and pid == 'share_seq'
                    cells += [f"{float(r['bundle_n3']):.3f}", r'\multicolumn{1}{c}{F}']
                else:
                    cells += pair(r['bundle_n3'], r['name_only'])
            lines.append(row(cells, keep=i < 2))
        if pid != 'petri_seq':
            lines.append(r'\addlinespace[1.7pt]')
    lines.append(r'\midrule')
    for i, (system, model, _, _) in enumerate(SYSTEMS):
        t = t1sum[model]
        cells = [r'\textbf{Macro mean}' if i == 0 else '', model]
        cells += pair(t['pdf_mean'], t['name_only_mean'])
        for metric in METRICS:
            r = sums[system, metric]
            cells += pair(r['bundle_mean'], r['name_only_mean'])
        lines.append(row(cells, keep=i < 2))
    lines.append(r'\addlinespace[2pt]')
    for i, (system, model, _, _) in enumerate(SYSTEMS):
        values = [float(t1sum[model]['difference'])] + [sums[system, m]['name_minus_bundle'] for m in METRICS]
        cells = [r'\textbf{$\Delta$ (Name$-$source)}' if i == 0 else '', model]
        cells += [r'\multicolumn{2}{c}{$' + f'{v:+.3f}' + '$}' for v in values]
        lines.append(row(cells, keep=i < 2))
    lines.append(r'\addlinespace[2pt]')
    for i, (_, model, _, _) in enumerate(SYSTEMS):
        t = t1sum[model]
        cells = [r'\textbf{Task~1 exact}' if i == 0 else '', model,
                 r'\textbf{' + t['pdf_exact'] + '/20}', t['name_only_exact'] + '/20', r'\multicolumn{8}{c}{}']
        lines.append(row(cells, keep=i < 2))
    lines += [r'\end{longtable}', r'\endgroup', '']
    return '\n'.join(lines)


def main():
    assert not OUT.exists(), f'Use a fresh output directory: {OUT}'
    protected = {str(p.resolve()): sha(p) for p in DATA.rglob('*') if p.is_file()}
    protected[str(PAPER / 'main.tex')] = sha(PAPER / 'main.tex')
    manifest = json.loads((DATA / 'input_manifest.json').read_text())
    old_case = next(c for c in manifest['cases'] if c['exception'] == 'OutputTokenExceededError'
                    and 'claude-code-opus-5-name-only/share_seq' in c['trial'])
    pinned = {}

    def pin(path, expected=None):
        path = Path(path).resolve()
        value = sha(path)
        assert expected is None or value == expected, f'Changed input: {path}'
        pinned[str(path)] = value
        return value

    scorer = ROOT / 'src/libstruct_bench/libgen/v4_7_0/scoring.py'
    pin(scorer, manifest['scorer_sha256'])
    for path in old_case['truth']['paths'].values():
        pin(path, manifest['inputs'][path])
    for name in ('per_protocol.csv', 'summary.csv', 'task1_per_protocol.csv', 'task1_summary.csv', 'input_manifest.json'):
        pin(DATA / name)
    spec = json.loads(EXPERIMENT.read_text())
    pin(EXPERIMENT, manifest['experiment_sha256'])
    for path, h in spec['generated_files'].items():
        pin(EXPERIMENT.parent / path, h)
    for path, h in spec['controller_files'].items():
        pin(path, h)
    result = json.loads((TRIAL / 'result.json').read_text())
    assert result['exception_info'] is None and result['finished_at']
    agent = result['config']['agent']
    expected = spec['systems']['claude-code']
    assert agent['model_name'] == expected['model']
    assert agent['kwargs']['version'] == expected['version']
    assert agent['kwargs']['reasoning_effort'] == expected['effort']
    receipt = json.loads((TRIAL / 'agent/name-only-isolation.json').read_text())
    assert receipt['ready'] and receipt['direct_tcp_blocked']
    assert all(' 403 ' in s for s in receipt['blocked'].values())
    assert receipt['experiment_lock_sha256'] == sha(EXPERIMENT)
    assert receipt['workspace_hashes'] == json.loads((EXPERIMENT.parent / 'tasks/share_seq/environment/name_only_inventory.json').read_text())
    trajectory = json.loads((TRIAL / 'agent/trajectory.json').read_text())
    tool_names = []
    for step in trajectory.get('steps', []):
        for tool in step.get('tool_calls', []) or []:
            name = str(tool.get('function_name') or tool.get('tool_name') or '')
            assert not any(w in name.lower() for w in ('web_search', 'search_web', 'read_url', 'browser', 'mcp', 'invoke_subagent', 'spawn_agent'))
            tool_names.append(name)
    for p in TRIAL.rglob('*'):
        if p.is_file():
            pin(p)
    case = dict(old_case, case_id=hashlib.sha256(str(TRIAL).encode()).hexdigest()[:24], trial=str(TRIAL), exception=None,
                p2=str(TRIAL / 'artifacts/logs/artifacts/t2_prediction.json'),
                p3=str(TRIAL / 'artifacts/logs/artifacts/t3_prediction.json'))
    scored = score(case)
    assert scored['status'] == 'valid_completion' and scored['prediction_valid']
    original = read('per_protocol.csv')
    before = aggregate(original)
    prior_summary = {(r['system'], r['metric']): r for r in read('summary.csv')
                     if r['policy'] == 'valid_complete_pairs' and r['baseline'] == 'bundle_n3'}
    for r in before:
        old = prior_summary[r['system'], r['metric']]
        assert r['n_protocols'] == int(old['n_protocols'])
        for k in ('bundle_mean', 'name_only_mean', 'name_minus_bundle'):
            assert math.isclose(r[k], float(old[k]), abs_tol=1e-12)
    updated = [dict(r) for r in original]
    changes = 0
    for r in updated:
        if r['system'] == 'claude-code' and r['protocol_id'] == 'share_seq':
            value = scored['scores'][r['metric']]
            r.update(valid_pair='True', name_only_status='valid_completion', name_only_exception='', name_only=value,
                     name_minus_bundle_n3=value-float(r['bundle_n3']),
                     name_minus_bundle_fig3a=value-float(r['bundle_fig3a']))
            changes += 1
    assert changes == 4
    after = aggregate(updated)
    for a, b in zip(before, after):
        if a['system'] != 'claude-code':
            assert a == b
        else:
            assert b['n_protocols'] == (19 if b['metric'] == METRICS[0] else 20)
    original_tex, updated_tex = table(original, before, False), table(updated, after, True)
    old_lines, new_lines = original_tex.splitlines(), updated_tex.splitlines()
    assert len(old_lines) == len(new_lines)
    edited_lines = [(i+1, a, b) for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(edited_lines) == 4, edited_lines  # Caption, SHARE-seq, macro, delta.
    OUT.mkdir()
    (OUT / TABLE_NAME).write_text(updated_tex)
    (OUT / 'table_changes.diff').write_text(''.join(difflib.unified_diff(
        original_tex.splitlines(True), updated_tex.splitlines(True), fromfile='supplied_table', tofile=TABLE_NAME)))
    (OUT / 'replacement_rows.tex').write_text('\n'.join(b for _, _, b in edited_lines) + '\n')
    write_csv(OUT / 'per_protocol.csv', updated)
    write_csv(OUT / 'summary_primary.csv', after)
    dump(OUT / 'retry_scored_v4_7_0.json', scored)
    terminal = None
    for line in (TRIAL / 'agent/claude-code.txt').read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'result':
            terminal = event
    assert terminal and not terminal['is_error'] and terminal['terminal_reason'] == 'completed'
    metadata_limit = terminal['modelUsage']['claude-opus-5']['maxOutputTokens']
    report = ['Updated Claude SHARE-seq name-only comparison',
              f"Scorer: 4.7.0; references: {manifest['reference_set_id']}.",
              'Only saved retry predictions were rescored. No agent inference was performed.',
              'Claude now uses 19 informative oligo protocols and 20 Task 3 protocols in both conditions.',
              'Bundle macro-means change because SHARE-seq rejoins the paired population.',
              'The original failed execution was replaced at user request; historical analysis results_v1 remain unchanged.',
              'The requested 128000-token cap comes from the user-reported launch command.',
              f'The CLI terminal modelUsage metadata still reports maxOutputTokens={metadata_limit};',
              'this metadata does not verify the effective per-request cap or establish why this retry succeeded.',
              '', 'Metric | SHARE-seq Bundle | SHARE-seq Name | Claude Bundle macro | Claude Name macro | Delta']
    for r in after:
        if r['system'] != 'claude-code':
            continue
        p = next(x for x in updated if x['system'] == 'claude-code' and x['protocol_id'] == 'share_seq' and x['metric'] == r['metric'])
        report.append(f"{r['label']} | {float(p['bundle_n3']):.3f} | {float(p['name_only']):.3f} | {r['bundle_mean']:.3f} | {r['name_only_mean']:.3f} | {r['name_minus_bundle']:+.3f}")
    (OUT / 'summary.txt').write_text('\n'.join(report) + '\n')
    assert all(sha(p) == h for p, h in protected.items())
    assert all(sha(p) == h for p, h in pinned.items())
    dump(OUT / 'verification.json', dict(script_sha256=sha(__file__), inputs_sha256=pinned,
         output_sha256={p.name: sha(p) for p in OUT.iterdir() if p.is_file()},
         scorer_version='4.7.0', reference_set_id=manifest['reference_set_id'], retry_case=case,
         original_failed_case=old_case, name_only_isolation_passed=True, tool_names=sorted(set(tool_names)),
         requested_output_limit=128000, output_limit_source='User-reported launch command',
         reported_model_usage_max_output_tokens=metadata_limit, new_agent_inference=False,
         historical_results_and_main_tex_unchanged=True, changed_table_lines=[i for i, _, _ in edited_lines]))
    print('\n'.join(report))
    compile_table()


def compile_table():
    work = Path(tempfile.mkdtemp(prefix='libstruct-name-only-retry-table-'))
    shutil.copy2(PAPER / 'neurips_2026.sty', work / 'neurips_2026.sty')
    previous = json.loads((DATA / 'publication_tables_v1/compile_verification.json').read_text())
    for p in Path(previous['temporary_directory']).iterdir():
        if p.suffix in {'.tfm', '.vf', '.pfb', '.enc', '.map', '.otf'}:
            shutil.copy2(p, work / p.name)
    shutil.copy2(OUT / TABLE_NAME, work / TABLE_NAME)
    preamble = (PAPER / 'main.tex').read_text().split(r'\title{', 1)[0]
    (work / 'check.tex').write_text(preamble + '\n' + r'\usepackage{longtable,array}' + '\n' +
        r'\begin{document}\appendix\renewcommand{\thetable}{S\arabic{table}}' + '\n' +
        r'\input{' + TABLE_NAME + r'}\end{document}')
    command = ['/opt/homebrew/bin/tectonic', '--only-cached', '--keep-logs', '--keep-intermediates', 'check.tex']
    run = subprocess.run(command, cwd=work, capture_output=True, text=True)
    (OUT / 'compile.log').write_text(run.stdout + run.stderr)
    log = (work / 'check.log').read_text() if (work / 'check.log').exists() else ''
    check = dict(command=command, temporary_directory=str(work), returncode=run.returncode,
                 errors=re.findall(r'^!.*', log, re.M), overfull_boxes=re.findall(r'Overfull .+', log),
                 unresolved_references=re.findall(r'Reference .+ undefined', log))
    if run.returncode == 0:
        import fitz
        pdf = fitz.open(work / 'check.pdf')
        check['pages'] = len(pdf)
        check['previews'] = []
        for i, page in enumerate(pdf):
            p = work / f'page-{i+1}.png'
            page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7)).save(p)
            check['previews'].append(str(p))
    dump(OUT / 'compile_verification.json', check)
    print(json.dumps(check, indent=2))
    assert run.returncode == 0 and not check['errors'] and not check['overfull_boxes'] and not check['unresolved_references']


if __name__ == '__main__':
    if sys.argv[1:] == ['--compile-only']:
        compile_table()
    else:
        main()
