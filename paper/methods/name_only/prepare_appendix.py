"""Prepare manuscript tables and a descriptive summary from verified saved scores."""
import csv
import hashlib
import json
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent/'results_v1'
CAMERA=ROOT/'analysis/paper_results/camera_ready_v4_7_0'
METRICS=['t2_required_family_f1','t3_molecular_transition_f1','t3_state_f1','t3_typed_edge_f1']
LABELS=['Oligo','Transition','State','Typed edge']
SYSTEMS={'codex':'GPT/Codex','claude-code':'Claude Code','antigravity-cli':'Gemini/Antigravity'}
NAMES={'10x_chromium_single_cell_atac_v2':'10x ATAC v2',
       '10x_chromium_3_gene_expression_v4':"10x 3' GEX v4",
       '10x_chromium_3_feature_barcoding':"10x 3' Feature Barcoding",
       'drop_seq':'Drop-seq','seq_well_s3':'Seq-Well S3','indrop_v1':'inDrop v1',
       's3_atac':'s3-ATAC','cel_seq':'CEL-seq','split_seq':'SPLiT-seq',
       'sci_rna_seq':'sci-RNA-seq','sci_atac_seq':'sci-ATAC-seq','microwell_seq':'Microwell-seq',
       'scrrbs':'scRRBS','pip_seq_v4':'PIP-seq v4','scdamid':'scDamID',
       'ddseq_single_cell_3_rna_seq_kit':'ddSEQ','smart_seq':'SMART-seq',
       'share_seq':'SHARE-seq','dr_seq':'DR-seq','petri_seq':'PETRI-seq'}


def tex(s):return s.replace('&',r'\&').replace('_',r'\_')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def row(cells):return ' & '.join(cells)+r' \\'


def main():
    verify=json.loads((OUT/'verification.json').read_text())
    assert verify['inputs_unchanged']
    scores=pd.read_csv(OUT/'per_protocol.csv')
    summary=pd.read_csv(OUT/'summary.csv')
    counts=pd.read_csv(OUT/'completion_counts.csv')
    t1=pd.read_csv(OUT/'task1_per_protocol.csv')
    original_manifest=json.loads((CAMERA/'reproducibility_manifest.json').read_text())
    expected=original_manifest['artifacts']['task1_controls_recomputed.csv']
    assert sha(CAMERA/'task1_controls_recomputed.csv')==expected
    assert set(scores.protocol_id)==set(NAMES)
    primary=summary.query("policy=='valid_complete_pairs' and baseline=='bundle_n3'")
    report=['Bundle versus protocol-name-only control',
            '=========================================',
            'Final scorer: 4.7.0; references: approved-20261010-b672aab997a2fdc0.',
            'No agent inference; source predictions, references, scores and main.tex unchanged.',
            'Primary: frozen experiment policy, mean of three bundle attempts versus one name-only attempt; complete valid pairs.',
            'All-20-protocol scheduled comparison is also supplied, with completed failures assigned zero.',
            'Task 2 excludes ddSEQ (N_req=0); Task 3 retains ddSEQ. Claude SHARE-seq failed with OutputTokenExceededError and no prediction files.',
            'Each bootstrap resamples paired protocols (100,000 resamples, seed 20261022); intervals do not measure repeated-generation uncertainty.',
            '', 'Task 2/3 primary comparison: name-only minus bundle',
            'System | Metric | n | Bundle | Name-only | Difference [95% protocol-bootstrap CI] | Bundle/name-only/tie counts']
    for _,r in primary.iterrows():
        report.append(f'{SYSTEMS[r.system]} | {r.label} | {r.n_protocols} | {r.bundle_mean:.3f} | {r.name_only_mean:.3f} | {r.name_minus_bundle:+.3f} [{r.ci_low:+.3f}, {r.ci_high:+.3f}] | {r.bundle_wins}/{r.name_only_wins}/{r.ties}')
    report+=['','Completion counts',counts.to_string(index=False),
             '', 'Scheduled-attempt comparison (failure = 0):',
             summary.query("policy=='all_scheduled_failure_zero' and baseline=='bundle_n3'")[['system','label','n_protocols','bundle_mean','name_only_mean','name_minus_bundle','ci_low','ci_high']].to_string(index=False),
             '', 'Single selected Figure 3a baseline comparison (complete valid pairs):',
             summary.query("policy=='valid_complete_pairs' and baseline=='bundle_fig3a'")[['system','label','n_protocols','bundle_mean','name_only_mean','name_minus_bundle','ci_low','ci_high']].to_string(index=False),
             '', 'Largest absolute protocol contrasts (name-only minus three-bundle mean):']
    for system in SYSTEMS:
        for metric in METRICS:
            d=scores[(scores.system==system)&(scores.metric==metric)&scores.eligible&scores.valid_pair]
            for _,r in d.loc[d.name_minus_bundle_n3.abs().nlargest(3).index].iterrows():
                report.append(f'{SYSTEMS[system]} | {r.label} | {NAMES[r.protocol_id]} | {r.bundle_n3:.3f} -> {r.name_only:.3f} ({r.name_minus_bundle_n3:+.3f})')
    report+=['','Task 1: separate API control, not outputs of the Task 2/3 native-agent runs.',
             'Model | PDF mean | Name-only mean | Change | PDF exact/20 | Name-only exact/20']
    t1summary=[]
    for system in ['GPT','Claude','Gemini']:
        d=t1[t1.model_family==system];assert len(d)==20
        r=dict(model=system,pdf_mean=float(d.pdf_similarity.mean()),name_only_mean=float(d.name_only_similarity.mean()),
               difference=float((d.name_only_similarity-d.pdf_similarity).mean()),
               pdf_exact=int(d.pdf_exact.sum()),name_only_exact=int(d.name_only_exact.sum()))
        t1summary.append(r)
        report.append(f"{system} | {r['pdf_mean']:.3f} | {r['name_only_mean']:.3f} | {r['difference']:+.3f} | {r['pdf_exact']} | {r['name_only_exact']}")
    report+=['','Interpretation and limitations:',
        '- This is a parametric-knowledge control, not proof of contamination or verbatim training-data recall.',
        '- Both conditions supply generic schemas/rules; name-only omits protocol evidence and explicitly permits internal knowledge. Bundle instructions prohibit remembered kit sequences and require evidence grounding.',
        '- Local construction/validation tools remain available in native name-only runs; web, retrieval, and subagent tools are restricted. Provider-only egress isolation was verified.',
        '- One name-only execution per model/protocol: no run-to-run uncertainty estimate for the condition effect.',
        '- Task 1 uses API calls, separate predictions/prompts and a separate score; do not combine its values with native Task 2/3 as one end-to-end execution.',
        '- Gemini Task 1 uses Vertex for name-only and OpenRouter for PDF; Task 2/3 locked comparison uses matched GCP-native configuration.',
        '- Kimi has no name-only run in this frozen experiment.',
        '- The separate name-only experiment manifest contains three bundle attempts for all three systems; the completed execution-variability appendix only analyzes GPT/Codex. Do not generalize its scope to assert that repeats for other systems do not exist.',
        '- Main Figures 3a/3b and historical checkpoint C30 are unchanged.',
        '', 'Table layout: three 20-protocol tables (one per native system, four paired F1 columns), plus a separate 20-protocol Task 1 table with all three API models.',
        'F denotes the failed name-only completion. A dash denotes the zero-required Task 2 cell, not zero performance.']
    (OUT/'summary.txt').write_text('\n'.join(report)+'\n')
    with (OUT/'task1_summary.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(t1summary[0]));w.writeheader();w.writerows(t1summary)

    def native_tables(baseline, filename, suffix):
        blocks=['% Requires booktabs and tabularx. Insert after author review.']
        for system,name in SYSTEMS.items():
            blocks += [r'\begin{table}[p]',r'\centering',r'\small',r'\setlength{\tabcolsep}{3pt}',
                       r'\renewcommand{\arraystretch}{1.10}',r'\begin{tabularx}{\textwidth}{@{}Xrrrrrrrr@{}}',r'\toprule',
                       row(['Protocol']+[r'\multicolumn{2}{c}{'+lab+' F1}' for lab in LABELS]),
                       r'\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}',
                       row(['']+['B','N']*4),r'\midrule']
            d=scores[scores.system==system]
            for pid in NAMES:
                cells=[tex(NAMES[pid])]
                for metric in METRICS:
                    r=d[(d.protocol_id==pid)&(d.metric==metric)].iloc[0]
                    cells += ([r'--',r'--'] if not r.eligible else
                              [f'{r[baseline]:.3f}',f'{r.name_only:.3f}' if r.valid_pair else r'F'])
                blocks.append(row(cells))
            blocks.append(r'\midrule')
            for policy,label in [('all_scheduled_failure_zero','Macro (scheduled)'),('valid_complete_pairs','Macro (valid pairs)')]:
                cells=[label]
                for metric in METRICS:
                    r=summary[(summary.system==system)&(summary.metric==metric)&(summary.policy==policy)&(summary.baseline==baseline)].iloc[0]
                    cells += [f'{r.bundle_mean:.3f}',f'{r.name_only_mean:.3f}']
                blocks.append(row(cells))
            definition=('B averages three frozen bundle executions per protocol; N is one name-only execution.' if baseline=='bundle_n3'
                        else 'B is the single bundle execution selected for Figure~3a; N is one name-only execution.')
            failure=('Claude\'s SHARE-seq execution exceeded the output-token limit without producing predictions (F). The scheduled mean assigns this completed failure zero; valid-pair means exclude SHARE-seq in both conditions, giving 18 oligo and 19 Task~3 pairs.' if system=='claude-code'
                     else 'All 20 name-only executions produced valid completions.')
            blocks += [r'\bottomrule',r'\end{tabularx}',r'\caption{\textbf{Bundle (B) versus name-only (N): '+tex(name)+'.} '+definition+' '+failure+
                       ' ddSEQ has no required recoverable oligo families; its oligo F1 is excluded (--) while its Task~3 scores are retained. Scheduled means use 19 informative protocols for oligo F1 and 20 for Task~3. Each protocol receives equal weight.}',
                       r'\label{tab:name-only-'+system+suffix+'}',r'\end{table}','']
        (OUT/filename).write_text('\n'.join(blocks))
    native_tables('bundle_n3','appendix_native_tables.tex','')
    native_tables('bundle_fig3a','appendix_native_tables_fig3a_selected.tex','-selected')
    lines=[r'\begin{table}[p]',r'\centering',r'\small',r'\setlength{\tabcolsep}{4pt}',
           r'\renewcommand{\arraystretch}{1.10}',r'\begin{tabularx}{\textwidth}{@{}Xrrrrrr@{}}',r'\toprule',
           row(['Protocol',r'\multicolumn{2}{c}{GPT}',r'\multicolumn{2}{c}{Claude}',r'\multicolumn{2}{c}{Gemini}']),
           r'\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}',row(['']+['PDF','Name-only']*3),r'\midrule']
    for pid in NAMES:
        cells=[tex(NAMES[pid])]
        for system in ['GPT','Claude','Gemini']:
            r=t1[(t1.model_family==system)&(t1.protocol_id==pid)].iloc[0]
            cells += [f'{r.pdf_similarity:.3f}',f'{r.name_only_similarity:.3f}']
        lines.append(row(cells))
    lines += [r'\midrule',row(['Macro similarity']+[f"{r[k]:.3f}" for r in t1summary for k in ['pdf_mean','name_only_mean']]),
              row(['Exact reconstruction']+[f"{r[k]}/20" for r in t1summary for k in ['pdf_exact','name_only_exact']]),
              r'\bottomrule',r'\end{tabularx}',
              r'\caption{\textbf{Task~1 PDF versus protocol-name-only API control.} Mean normalized sequence similarity for one saved output per model, condition, and protocol. These are separate API executions from the native Task~2/3 comparison. All 20 scheduled protocols are included; generated empty outputs receive zero. Exact reconstruction additionally requires the correct number of libraries and zero edit distance for every matched library. Evidence-grounding instructions differ between conditions; Gemini also uses different provider routes.}',
              r'\label{tab:task1-name-only-protocols}',r'\end{table}']
    (OUT/'appendix_task1_table.tex').write_text('\n'.join(lines)+'\n')
    section=r'''\section{Reconstruction without protocol source bundles}
\label{app:name-only-control}

We evaluated reconstruction from the protocol name and version without
providing protocol source documents. This is a parametric-knowledge control:
performance without a bundle does not distinguish memorized source content
from general learned knowledge or reasoning. Generic output schemas and
reconstruction instructions remained available.

\paragraph{Native-agent comparison for Tasks~2 and~3.}
GPT-5.6 Sol with Codex, Claude Opus~5 with Claude Code, and Gemini~3.7 Flash
with Antigravity each had one name-only execution on the 20 baseline protocols.
The model, CLI version, and requested reasoning effort matched the corresponding
bundle condition. Local file construction and validation tools were retained;
retrieval tools were restricted and network-isolation receipts confirmed
blocked external access apart from the model-provider route.
The name-only prompt explicitly permitted internal knowledge, whereas the
bundle prompt required source grounding and prohibited remembered kit
sequences. Thus, the comparison changes both evidence availability and
grounding instructions; it does not isolate a causal effect of document
availability alone.

Following the frozen control-experiment plan, we compared each name-only
execution with the mean of three bundle executions for the same protocol.
These are observed executions, not controlled random seeds. All saved
predictions were evaluated with identical scoring rules and the same approved
references. Task~2 excludes ddSEQ because it has no required recoverable
oligo families; Task~3 retains all 20 protocols. GPT and Gemini produced
20 valid name-only completions each. Claude produced 19; its SHARE-seq
execution exceeded the output-token limit without producing predictions.
Primary paired means exclude this failed case from both conditions, giving
18 informative Task~2 pairs and 19 Task~3 pairs for Claude. The accompanying
tables also report all-scheduled means, assigning the completed failure zero.
Intervals for paired differences use 100,000 protocol-bootstrap resamples,
retaining all bundle executions within their protocol. They quantify
across-protocol uncertainty conditional on the saved executions, not
run-to-run variability of the name-only condition.

Mean name-only scores were lower than bundle scores for all four native-agent
metrics in all three systems. Oligo F1 decreased by 0.106 for GPT, 0.079 for
Claude, and 0.106 for Gemini on valid paired protocols. The largest mean
Task~3 decrease was Gemini molecular-state F1 (0.511 to 0.376).
However, protocol-level effects varied: most Task~3 difference intervals
included zero, while Gemini transition and state intervals remained below
zero. These descriptive contrasts do not establish uniform bundle benefits
or multiplicity-adjusted significance across metrics. Comparisons against
the single bundle executions selected for Figure~3a also yielded lower
name-only means on all four metrics for all three systems; the main-figure
selections were not changed.

\paragraph{Separate Task~1 API control.}
One name-only API execution per model and protocol was compared with the
saved PDF-input API execution on the same 20 protocols. Mean sequence
similarity increased from 0.561 to 0.748 for GPT, 0.591 to 0.813 for Claude,
and 0.597 to 0.783 for Gemini. Exact reconstructions nevertheless decreased
from 4 to 2, 5 to 3, and 4 to 3 protocols, respectively, out of 20.
Empty generated outputs in the PDF condition were retained at zero.
These Task~1 API outputs are separate from the native Task~2/3 executions;
Gemini additionally used different provider routes for its PDF and name-only
API conditions. Higher average similarity therefore does not demonstrate
more frequent exact reconstruction or establish training-data contamination.

Together, the controls show substantial reconstruction ability without
supplied protocol evidence, alongside task-dependent differences between
conditions. They support neither a general claim that bundles are unnecessary
nor a direct attribution of benchmark performance to memorization.
'''
    assert (primary.name_minus_bundle < 0).all()
    (OUT/'appendix_section.tex').write_text(section)
    settings=json.loads((OUT/'settings.json').read_text())
    manifest=dict(script_sha256=sha(__file__), source_analysis_script_sha256=verify['script_sha256'],
                  input_hashes={f:sha(OUT/f) for f in ['summary.csv','per_protocol.csv','completion_counts.csv','task1_per_protocol.csv']},
                  authoritative_task1_table_sha256=expected,
                  outputs={f.name:sha(f) for f in OUT.glob('*.tex')})
    (OUT/'appendix_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('\n'.join(report[:25]))


if __name__=='__main__':main()
