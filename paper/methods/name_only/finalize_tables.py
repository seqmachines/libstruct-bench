"""Format five publication tables from verified results without rescoring.

All aggregate values are checked against summary.txt and the underlying rows.
The existing results and manuscript are read-only inputs.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / 'results_v1'
OUT = DATA / 'publication_tables_v1'
PAPER = Path('external/manuscript')
METRICS = ['t2_required_family_f1', 't3_molecular_transition_f1',
           't3_state_f1', 't3_typed_edge_f1']
LABELS = ['Oligo', 'Transition', 'State', 'Typed edge']
SYSTEMS = [('codex', 'GPT', 'GPT/Codex', 'gpt'),
           ('claude-code', 'Claude', 'Claude Code', 'claude'),
           ('antigravity-cli', 'Gemini', 'Gemini/Antigravity', 'gemini')]
PROTOCOLS = {
    '10x_chromium_single_cell_atac_v2': '10x ATAC v2',
    '10x_chromium_3_gene_expression_v4': r"10x 3$'$ GEX v4",
    '10x_chromium_3_feature_barcoding': r"10x 3$'$ Feature Barcoding",
    'drop_seq': 'Drop-seq', 'seq_well_s3': 'Seq-Well S3',
    'indrop_v1': 'inDrop v1', 's3_atac': 's3-ATAC', 'cel_seq': 'CEL-seq',
    'split_seq': 'SPLiT-seq', 'sci_rna_seq': 'sci-RNA-seq',
    'sci_atac_seq': 'sci-ATAC-seq', 'microwell_seq': 'Microwell-seq',
    'scrrbs': 'scRRBS', 'pip_seq_v4': 'PIP-seq v4', 'scdamid': 'scDamID',
    'ddseq_single_cell_3_rna_seq_kit': 'ddSEQ', 'smart_seq': 'SMART-seq',
    'share_seq': 'SHARE-seq', 'dr_seq': 'DR-seq', 'petri_seq': 'PETRI-seq',
}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(name):
    with (DATA/name).open(newline='') as f: return list(csv.DictReader(f))
def mean(xs): return math.fsum(xs)/len(xs)
def close(a, b): assert math.isclose(float(a), float(b), abs_tol=1e-12, rel_tol=0), (a,b)
def num(x, signed=False): return r'\(' + (f'{float(x):+.3f}' if signed else f'{float(x):.3f}') + r'\)'
def center(s): return r'\multicolumn{1}{c}{' + s + '}'
def row(cells): return ' & '.join(cells) + r' \\'
def opening(spec, spacing=3.5):
    return ['% Requires booktabs; all values are from the verified saved results.',
            '% Fixed three-decimal math-mode values in r columns align decimal points.',
            r'\begin{table}[tbp]', r'\centering', r'\small',
            r'\setlength{\tabcolsep}{'+str(spacing)+'pt}',
            r'\renewcommand{\arraystretch}{1.08}',
            r'\begin{tabular}{@{}'+spec+'@{}}', r'\toprule']
def finish(lines, caption, label, filename):
    lines += [r'\bottomrule', r'\end{tabular}', r'\caption{'+caption+'}',
              r'\label{tab:name-only-'+label+'}', r'\end{table}', '']
    (OUT/filename).write_text('\n'.join(lines))


def main():
    assert not OUT.exists(), 'Use a new publication-tables directory for another finalization.'
    protected = {str(p.resolve()):sha(p) for p in DATA.rglob('*') if p.is_file()}
    protected[str(PAPER/'main.tex')] = sha(PAPER/'main.tex')
    native, aggregate, task1, t1agg = [read(n) for n in
        ['per_protocol.csv', 'summary.csv', 'task1_per_protocol.csv', 'task1_summary.csv']]
    summary_text = (DATA/'summary.txt').read_text()
    native_summary = {(r['system'],r['metric']):r for r in aggregate
                      if r['policy']=='valid_complete_pairs' and r['baseline']=='bundle_n3'}
    t1_summary = {r['model']:r for r in t1agg}
    native_rows = {(r['system'],r['protocol_id'],r['metric']):r for r in native}
    t1_rows = {(r['model_family'],r['protocol_id']):r for r in task1}
    assert len(native_rows)==len(native)==240 and len(t1_rows)==len(task1)==60
    assert {r['protocol_id'] for r in native}==set(PROTOCOLS)
    assert {r['protocol_id'] for r in task1}==set(PROTOCOLS)
    checked_aggregates=[]
    for system, model, display, slug in SYSTEMS:
        for metric, label in zip(METRICS,LABELS):
            r=native_summary[system,metric]
            pairs=[x for x in native if x['system']==system and x['metric']==metric
                   and x['eligible']=='True' and x['valid_pair']=='True']
            assert len(pairs)==int(r['n_protocols'])
            close(mean([float(x['bundle_n3']) for x in pairs]),r['bundle_mean'])
            close(mean([float(x['name_only']) for x in pairs]),r['name_only_mean'])
            close(float(r['name_only_mean'])-float(r['bundle_mean']),r['name_minus_bundle'])
            prefix=f"{display} | {label} | {r['n_protocols']} | {float(r['bundle_mean']):.3f} | {float(r['name_only_mean']):.3f} | {float(r['name_minus_bundle']):+.3f} ["
            assert prefix in summary_text, prefix
            checked_aggregates.append(dict(system=system,metric=metric,n=len(pairs),
                bundle=r['bundle_mean'],name_only=r['name_only_mean'],delta=r['name_minus_bundle'],
                summary_text_match=True,protocol_row_means_match=True))
        t=t1_summary[model]; cases=[x for x in task1 if x['model_family']==model]
        assert len(cases)==20
        close(mean([float(x['pdf_similarity']) for x in cases]),t['pdf_mean'])
        close(mean([float(x['name_only_similarity']) for x in cases]),t['name_only_mean'])
        close(float(t['name_only_mean'])-float(t['pdf_mean']),t['difference'])
        assert sum(x['pdf_exact']=='True' for x in cases)==int(t['pdf_exact'])
        assert sum(x['name_only_exact']=='True' for x in cases)==int(t['name_only_exact'])
        expected=f"{model} | {float(t['pdf_mean']):.3f} | {float(t['name_only_mean']):.3f} | {float(t['difference']):+.3f} | {t['pdf_exact']} | {t['name_only_exact']}"
        assert expected in summary_text
        checked_aggregates.append(dict(system=model,metric='task1_sequence_similarity',n=20,
            bundle=t['pdf_mean'],name_only=t['name_only_mean'],delta=t['difference'],
            pdf_exact=int(t['pdf_exact']),name_only_exact=int(t['name_only_exact']),
            summary_text_match=True,protocol_row_means_match=True))
    OUT.mkdir()

    # Cross-task table: API Task 1 and native Tasks 2/3 remain explicitly distinct.
    lines=opening('lrrrrr',4)
    lines += [row(['Model',center('Task~1'),center('Task~2'),r'\multicolumn{3}{c}{Task~3}']),
              r'\cmidrule(lr){2-2}\cmidrule(lr){3-3}\cmidrule(lr){4-6}',
              row(['',center(r'Seq. sim. $\Delta$'),center(r'Oligo $\Delta$'),
                   center(r'Transition $\Delta$'),center(r'State $\Delta$'),center(r'Typed edge $\Delta$')]),r'\midrule']
    for system, model, display, slug in SYSTEMS:
        lines.append(row([model,num(t1_summary[model]['difference'],True)] +
                         [num(native_summary[system,m]['name_minus_bundle'],True) for m in METRICS]))
    finish(lines,
        r'\textbf{Protocol-name-only minus document-supplied performance.} '
        r'$\Delta$ is the difference in macro-averaged sequence similarity (Task~1) or F1 (Tasks~2--3), computed before rounding. '
        r'Task~1 uses separate API executions on 20 protocols. Tasks~2--3 use native agents: three bundle executions averaged per protocol versus one name-only execution. '
        r'Valid pairs give $n=19$ for Task~2 and $n=20$ for Task~3 (Claude: 18 and 19). '
        r'ddSEQ is excluded from Task~2; Claude\textquotesingle s failed SHARE-seq execution is excluded from both paired means.',
        'summary','table_name_only_summary.tex')

    # Task 1 table: one API output per condition, all 20 protocols.
    lines=opening('lrrrrrr',4)
    lines += [row(['Protocol']+[r'\multicolumn{2}{c}{'+model+'}' for _,model,_,_ in SYSTEMS]),
              r'\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}',
              row(['']+[center(s) for s in ['PDF','N']*3]),r'\midrule']
    for pid,label in PROTOCOLS.items():
        cells=[label]
        for _,model,_,_ in SYSTEMS:
            r=t1_rows[model,pid];cells += [num(r['pdf_similarity']),num(r['name_only_similarity'])]
        lines.append(row(cells))
    lines += [r'\midrule',row(['Macro similarity']+[num(t1_summary[m][k]) for _,m,_,_ in SYSTEMS for k in ['pdf_mean','name_only_mean']]),
              row(['Exact reconstruction']+[center(r'\('+t1_summary[m][k]+r'/20\)') for _,m,_,_ in SYSTEMS for k in ['pdf_exact','name_only_exact']])]
    finish(lines,
        r'\textbf{Task~1 sequence similarity with PDFs versus protocol name only.} '
        r'PDF and N denote one document-supplied and one name-only API execution per model and protocol. '
        r'Macro similarity includes all 20 protocols; generated empty outputs score zero. '
        r'Exact reconstruction requires zero edit distance for every final library and the correct library count, assessed before rounding. '
        r'These API executions are separate from the native-agent Tasks~2--3 runs.',
        'task1','table_name_only_task1.tex')

    # Native tables: all rows displayed; means only over valid matched pairs.
    for system, model, display, slug in SYSTEMS:
        lines=opening('lrrrrrrrr',3)
        lines += [row(['Protocol']+[r'\multicolumn{2}{c}{'+label+r' F1}' for label in LABELS]),
                  r'\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}',
                  row(['']+[center(s) for s in ['B','N']*4]),r'\midrule']
        for pid,label in PROTOCOLS.items():
            cells=[label]
            for metric in METRICS:
                r=native_rows[system,pid,metric]
                if r['eligible']!='True':
                    assert pid=='ddseq_single_cell_3_rna_seq_kit' and metric==METRICS[0]
                    cells += [center('--'),center('--')]
                else:
                    cells += [num(r['bundle_n3'])]
                    if r['name_only_status']=='valid_completion':cells.append(num(r['name_only']))
                    else:
                        assert system=='claude-code' and pid=='share_seq'
                        assert r['name_only_exception']=='OutputTokenExceededError'
                        cells.append(center('F'))
            lines.append(row(cells))
        lines += [r'\midrule',row(['Macro-F1 (valid pairs)']+
                 [num(native_summary[system,m][k]) for m in METRICS for k in ['bundle_mean','name_only_mean']])]
        caption=r'\textbf{Bundle versus protocol-name-only reconstruction: '+display+r'.} '
        caption += r'B is the mean of three bundle executions; N is one name-only execution. '
        caption += r'Macro-F1 weights valid paired protocols equally. ddSEQ is excluded from oligo F1 (--, $N_{\mathrm{req}}=0$) and retained for Task~3. '
        if system=='claude-code':
            caption += r'F marks the SHARE-seq output-token-limit failure; SHARE-seq is excluded from both condition means, leaving 18 oligo and 19 Task~3 pairs.'
        else:caption += r'There are 19 oligo and 20 Task~3 pairs.'
        finish(lines,caption,slug,f'table_name_only_{slug}.tex')

    # No additional package is needed for apostrophes.
    summary_path=OUT/'table_name_only_summary.tex'
    summary_path.write_text(summary_path.read_text().replace(r'Claude\textquotesingle s',"Claude's"))
    files=sorted(OUT.glob('*.tex'));assert len(files)==5
    labels=[]
    for f in files:
        text=f.read_text()
        assert '\\begin{tabularx}' not in text and '\\resizebox' not in text
        assert text.count('\\begin{table}')==text.count('\\end{table}')==1
        assert text.count('\\begin{tabular}')==text.count('\\end{tabular}')==1
        labels += [line for line in text.splitlines() if line.startswith(r'\label')]
    assert len(set(labels))==5
    assert all(sha(p)==h for p,h in protected.items())
    manifest=dict(script_sha256=sha(__file__),
        inputs_sha256={n:sha(DATA/n) for n in ['summary.txt','summary.csv','per_protocol.csv','task1_summary.csv','task1_per_protocol.csv']},
        aggregate_checks=checked_aggregates, existing_artifacts_unchanged=True,
        manuscript_unchanged=True, manuscript_sha256=sha(PAPER/'main.tex'),
        protected_file_count=len(protected), scores_recomputed=False, agent_inference=False,
        primary_comparison='Mean of three bundle executions versus one name-only execution; valid complete pairs',
        numeric_format='Three decimals; fixed-width math digits in right-aligned columns',
        outputs_sha256={f.name:sha(f) for f in files})
    (OUT/'verification.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (OUT/'README.txt').write_text(
        'Final protocol-name-only appendix tables\n'
        '=======================================\n'
        'Five independent table floats; only booktabs is required.\n'
        'Copy the .tex files into the paper and use \\input{path/to/file} as needed.\n'
        'All means and changes are checked against summary.txt and recomputed from the supplied score rows (no rescoring).\n'
        'Changes are calculated before rounding. Native comparisons use valid complete pairs and three bundle executions versus one name-only execution.\n'
        'Claude SHARE-seq is shown as F and excluded from both means. ddSEQ is excluded only from Task 2.\n'
        'Task 1 API executions remain separate from native Tasks 2/3.\n'
        'Old tables, predictions, references, scores, and main.tex are unchanged.\n')
    print('Wrote five separate tables; verified 15 metric aggregates and Task 1 exact counts against summary.txt.')
    print(OUT)


if __name__=='__main__':main()
