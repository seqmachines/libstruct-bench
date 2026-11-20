"""Paired n=1 vs n=1 name-only control; saved, final-verifier scores only.

Run: python analysis/paper_results/name_only_v4_7_0/analyze_n1_paired.py
Use --out for a fresh output directory; --compile-only verifies existing TeX.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import numpy as np
import scipy
from scipy.stats import permutation_test

from finalize_tables import PAPER, PROTOCOLS, SYSTEMS, METRICS, LABELS
from update_share_seq_retry import table as portrait_table

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent
OLD = BASE / 'results_v1'
LATEST = BASE / 'results_share_seq_retry_v1'
CAMERA = ROOT / 'analysis/paper_results/camera_ready_v4_7_0'
SEED = 20261023
N_BOOTSTRAP = 100_000
NATIVE_FAMILY = 12
API_FAMILY = 3


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    with Path(p).open(newline='') as f:
        return list(csv.DictReader(f))


def load(p):
    return json.loads(Path(p).read_text())


def dump(p, value):
    Path(p).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def csvout(p, rows):
    with Path(p).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def exact_sign_flip(d):
    """Enumerate all 2**n signed sums, including duplicate assignments at zeros.

    |mean| and |sum| give identical orderings because n is fixed. Numerical
    equality uses a scale-aware roundoff tolerance, not rounded input scores.
    """
    d = np.asarray(d, dtype=np.float64)
    assert d.ndim == 1 and 1 <= len(d) <= 20 and np.isfinite(d).all()
    null_sums = np.array([0.], dtype=np.float64)
    for x in d:
        null_sums = np.concatenate((null_sums - x, null_sums + x))
    observed = math.fsum(d)
    tolerance = 64 * np.finfo(float).eps * max(1., math.fsum(abs(x) for x in d))
    extreme = int(np.count_nonzero(np.abs(null_sums) >= abs(observed) - tolerance))
    assert len(null_sums) == 2 ** len(d)
    return dict(p_raw=extreme / len(null_sums), extreme_sign_assignments=extreme,
                sign_assignments=len(null_sums), sum_roundoff_tolerance=tolerance)


def validate_statistics():
    # Independent SciPy exact paired permutation calculation includes zeros,
    # tied magnitudes, an exactly zero mean, and a one-directional contrast.
    vectors = ([0., 0., 0.], [.2, .2, -.2, 0.], [.1, -.1, .2, -.2],
               [.1, .2, .3], [.17, -.31, .07, .43, -.29])
    for d in vectors:
        got = exact_sign_flip(d)
        reference = permutation_test((np.asarray(d),), np.mean,
                                     permutation_type='samples', n_resamples=np.inf,
                                     alternative='two-sided', vectorized=True)
        assert abs(got['p_raw'] - reference.pvalue) < 1e-14
        brute = [abs(math.fsum(s*x for s, x in zip(signs, d)))
                 for signs in itertools.product((-1, 1), repeat=len(d))]
        obs = abs(math.fsum(d))
        expected = sum(v >= obs-got['sum_roundoff_tolerance'] for v in brute) / len(brute)
        assert got['p_raw'] == expected
        assert exact_sign_flip([-x for x in d])['p_raw'] == got['p_raw']
    assert exact_sign_flip([1., 1., 1.])['p_raw'] == .25
    assert exact_sign_flip([0., 0., 0.])['p_raw'] == 1.


def paired_summary(d, *, family, system_index, family_code):
    d = np.asarray(d, dtype=np.float64)
    result = exact_sign_flip(d)
    # Shared resamples for metrics with the same protocol set and system.
    seed = [SEED, family_code, system_index, len(d)]
    rng = np.random.default_rng(np.random.SeedSequence(seed))
    indices = rng.integers(0, len(d), (N_BOOTSTRAP, len(d)))
    boot = d[indices].mean(axis=1)
    low, high = np.quantile(boot, [.025, .975], method='linear')
    result.update(delta=math.fsum(d) / len(d), ci_low=float(low), ci_high=float(high),
                  p_bonferroni=min(1., family * result['p_raw']), correction_family_size=family,
                  bootstrap_resamples=N_BOOTSTRAP, bootstrap_seed=json.dumps(seed),
                  name_wins=int((d > 1e-12).sum()), source_wins=int((d < -1e-12).sum()),
                  ties=int((abs(d) <= 1e-12).sum()))
    return result


def ptex(value):
    if value >= .0001:
        return f'{value:.4f}'
    mantissa, power = f'{value:.2e}'.split('e')
    return r'\(' + mantissa + r'\times10^{' + str(int(power)) + r'}\)'


def statistics_table(native, api):
    lines = [r'\begin{table}[htbp]', r'\centering', r'\small',
             r'\setlength{\tabcolsep}{3pt}', r'\renewcommand{\arraystretch}{1.1}',
             r'\begin{tabular}{@{}llrrcrr@{}}', r'\toprule',
             r'Model & Metric & Protocols & $\Delta$ & Pointwise 95\% CI & $p$ & $p_{\mathrm{Bonf.}}$ \\',
             r'\midrule', r'\multicolumn{7}{@{}l}{\textit{Tasks~2--3: native agents, Bundle versus Name (12 comparisons)}}\\[2pt]']
    for i, r in enumerate(native):
        lines.append(' & '.join([r['model'] if i % 4 == 0 else '', r['label'], str(r['n_protocols']),
                     f"{r['delta']:+.3f}", f"[{r['ci_low']:+.3f}, {r['ci_high']:+.3f}]",
                     ptex(r['p_raw']), ptex(r['p_bonferroni'])]) + r' \\')
        if i % 4 == 3 and i < 11:
            lines.append(r'\addlinespace[2pt]')
    lines += [r'\midrule', r'\multicolumn{7}{@{}l}{\textit{Task~1: separate API executions, PDF versus Name (3 comparisons)}}\\[2pt]']
    for r in api:
        lines.append(' & '.join([r['model'], 'Similarity', str(r['n_protocols']), f"{r['delta']:+.3f}",
                    f"[{r['ci_low']:+.3f}, {r['ci_high']:+.3f}]", ptex(r['p_raw']), ptex(r['p_bonferroni'])]) + r' \\')
    lines += [r'\bottomrule', r'\end{tabular}',
        r'\caption{\textbf{Paired document-supplied versus name-only comparisons.} '
        r'$\Delta$ is Name minus Bundle/PDF, averaged across protocols. Each condition contributes one selected execution per protocol; '
        r'the native Bundle executions are those used in Figure~3a. Intervals are pointwise percentile intervals from 100,000 paired '
        r'protocol-bootstrap resamples. Two-sided mean-difference tests enumerate all $2^n$ sign assignments, where $n$ is the number of '
        r'protocol pairs. Bonferroni correction is applied separately to 12 native-agent comparisons and three API comparisons. '
        r'Pointwise intervals are not multiplicity-adjusted. ddSEQ is excluded only from oligo F1; Claude SHARE-seq uses its successful retry. '
        r'Exact-reconstruction counts are descriptive and are not tested.}',
        r'\label{tab:name-only-paired-statistics}', r'\end{table}', '']
    return '\n'.join(lines)


def appendix(native, api):
    assert all(r['delta'] < 0 for r in native)
    assert all(r['p_bonferroni'] >= .05 for r in native)
    assert [r['model'] for r in api if r['p_bonferroni'] < .05] == ['Claude']
    c = next(r for r in api if r['model'] == 'Claude')
    return r'''\section{Reconstruction without protocol source bundles}
\label{app:name-only-control}

We assessed reconstruction from the protocol name and version without supplying
protocol source documents. Generic output schemas, instructions, and local
construction and validation tools remained available. Retrieval tools were
restricted and network-isolation checks confirmed blocked external access
apart from the model-provider route. This is a parametric-knowledge control;
it does not distinguish memorized source content from general learned knowledge
or reasoning.

\paragraph{Paired native-agent comparison.}
For Tasks~2 and~3, we compared the original Bundle execution selected for
Figure~3a with one selected Name execution for each system and protocol.
The primary comparison is therefore one execution versus one execution;
the additional Bundle executions are not averaged into this analysis.
All selected predictions were evaluated with the same scoring rules and
approved ground-truth references. Failed pairs would be excluded from both
condition means. The current selection contains 20 valid pairs for each
system, including the successful Claude SHARE-seq retry after an earlier
output-token-limit failure. That retry requested a 128,000-token output limit;
it is a replacement execution, not an additional replicate in the mean.
ddSEQ has no required recoverable oligo families and is excluded from Task~2,
giving 19 informative oligo pairs; Task~3 retains all 20 protocols.

The named models are GPT-5.6 Sol, Claude Opus~5, and Gemini~3.7 Flash.
Codex (0.147.0) and Claude Code (2.1.233) versions match between conditions.
The selected Gemini Bundle executions used Antigravity 1.1.13, whereas Name
used 1.3.2. Moreover, Name instructions permit internal knowledge, whereas
Bundle instructions require source grounding. Thus, this comparison does not
isolate the causal effect of document availability alone.

\paragraph{Paired statistical analysis.}
For each protocol $p$, system, and metric, we calculated
$d_p=F_{p,\mathrm{Name}}-F_{p,\mathrm{Bundle}}$ and
$\Delta=n^{-1}\sum_{p=1}^n d_p$, with equal protocol weights.
Pointwise 95\% confidence intervals are the 2.5th and 97.5th percentiles of
100,000 paired protocol-bootstrap means (seed 20261023).
We used two-sided paired sign-flip permutation tests of the mean difference,
enumerating all $2^n$ assignments:
\[
 p_{\mathrm{raw}}=2^{-n}\!\sum_{\boldsymbol{s}\in\{-1,+1\}^n}
 \mathbf{1}\!\left\{\left|n^{-1}\sum_{p=1}^n s_p d_p\right|
 \geq |\Delta|\right\}.
\]
Zero differences and tied values are retained. Bonferroni-adjusted values are
$p_{\mathrm{Bonf.}}=\min(1,12p_{\mathrm{raw}})$ across the three systems
and four native-agent metrics. The tests rely on within-pair label
exchangeability, or independent sign symmetry of the differences under the
null. These were not randomized condition assignments. Inference describes
protocol-level variation conditional on the selected executions and does not
estimate run-to-run variability. The intervals are pointwise, not
family-wise intervals; an interval excluding zero need not correspond to a
Bonferroni-adjusted $p<0.05$.

\paragraph{Native-agent results.}
Name-only macro-F1 was lower than Bundle macro-F1 for every native system
and metric. Oligo F1 differences were -0.111 for GPT, -0.085 for Claude,
and -0.113 for Gemini. Task~3 differences ranged from -0.042 to -0.129.
None of the 12 comparisons met the Bonferroni-adjusted 0.05 threshold.
These consistently negative point estimates support a descriptive Bundle
advantage on this panel, but the adjusted tests do not establish a difference
for an individual system--metric comparison. They also do not establish
equivalence between conditions.

\paragraph{Separate Task~1 API control.}
Task~1 compares one PDF-input API execution with one Name API execution per
model and protocol. All 20 paired protocols are retained, including generated
empty PDF outputs scored as zero. We apply the same paired mean-difference
procedure to sequence similarity, with a separate Bonferroni family of three
models: $p_{\mathrm{Bonf.}}=\min(1,3p_{\mathrm{raw}})$.
Mean similarity increased from 0.561 to 0.748 for GPT, 0.591 to 0.813 for
Claude, and 0.597 to 0.783 for Gemini. Only Claude met the adjusted 0.05
    threshold ''' + rf"($\Delta={c['delta']:+.3f}$, $p_{{\mathrm{{Bonf.}}}}={c['p_bonferroni']:.4f}$)." + '\n' + r'''
Exact reconstructions decreased from 4 to 2, 5 to 3, and 4 to 3 protocols,
respectively, out of 20; these counts are descriptive and were not tested.
Task~1 uses separate predictions and prompts from the native-agent study.
Gemini also used different provider routes (OpenRouter for PDF and Vertex for
Name). Higher average similarity therefore does not establish more frequent
exact reconstruction or demonstrate training-data contamination.

Table~\ref{tab:name-only-all-tasks} gives all protocol scores and
Table~\ref{tab:name-only-paired-statistics} reports paired effects and tests.

\input{tables/name_only_combined_tasks1-3.tex}
\input{tables/name_only_paired_statistics.tex}
'''


def analyze(out):
    assert not out.exists(), f'Use a fresh output directory: {out}'
    validate_statistics()
    pinned = {}

    def pin(p, expected=None):
        p = Path(p).resolve()
        h = sha(p)
        assert expected is None or expected == h, f'Changed input: {p}'
        pinned[str(p)] = h
        return h

    old = load(OLD / 'input_manifest.json')
    latest = load(LATEST / 'verification.json')
    camera = load(CAMERA / 'input_manifest.json')
    for p in (OLD / 'input_manifest.json', LATEST / 'verification.json', CAMERA / 'input_manifest.json',
              OLD / 'task1_per_protocol.csv', OLD / 'task1_summary.csv', LATEST / 'per_protocol.csv',
              LATEST / 'retry_scored_v4_7_0.json', LATEST / 'name_only_combined_tasks1-3.tex',
              Path(__file__), BASE / 'update_share_seq_retry.py', BASE / 'finalize_tables.py',
              PAPER / 'main.tex', PAPER / 'neurips_2026.sty'):
        pin(p)
    for name in ('per_protocol.csv', 'retry_scored_v4_7_0.json', 'name_only_combined_tasks1-3.tex'):
        assert pin(LATEST / name) == latest['output_sha256'][name]
    assert camera['reference_set_id'] == old['reference_set_id'] == latest['reference_set_id']
    scorer = ROOT / 'src/libstruct_bench/libgen/v4_7_0/scoring.py'
    pin(scorer, camera['scorer_sha256'])
    pin(CAMERA / 'selected_protocol_scores.csv', old['inputs'][str(CAMERA / 'selected_protocol_scores.csv')])
    source_rows = read(LATEST / 'per_protocol.csv')
    selected = {(r['group'], r['protocol_id']): r for r in read(CAMERA / 'selected_protocol_scores.csv')
                if r['panel'] == 'fig3a' and r['group'] in {x[3] for x in (
                    ('', '', '', 'gpt_codex'), ('', '', '', 'claude_code'), ('', '', '', 'gemini_antigravity'))}}
    assert len(selected) == 60
    groups = {'codex': 'gpt_codex', 'claude-code': 'claude_code', 'antigravity-cli': 'gemini_antigravity'}
    cases = {c['case_id']: c for c in old['cases']}
    names = {(m['system'], m['protocol_id']): m for m in old['memberships'] if m['condition'] == 'name_only'}
    memberships, settings, native = [], [], []
    completion = defaultdict(Counter)
    refs_checked = set()
    retry_scored = load(LATEST / 'retry_scored_v4_7_0.json')

    for system, model, display, _ in SYSTEMS:
        for pid in PROTOCOLS:
            b = selected[groups[system], pid]
            bresult_path = Path(b['trial']) / 'result.json'
            pin(bresult_path, old['inputs'][str(bresult_path)])
            bresult = load(bresult_path)
            bvalid = b['valid_completion'] == 'True' and bresult.get('exception_info') is None
            member = names[system, pid]
            is_retry = system == 'claude-code' and pid == 'share_seq'
            case = latest['retry_case'] if is_retry else cases[member['case_id']]
            expected_hashes = latest['inputs_sha256'] if is_retry else old['inputs']
            ntrial = Path(case['trial'])
            nrpath = ntrial / 'result.json'
            pin(nrpath, expected_hashes[str(nrpath)])
            nresult = load(nrpath)
            scored_path = LATEST / 'retry_scored_v4_7_0.json' if is_retry else OLD / 'case_results' / (case['case_id'] + '.json')
            pin(scored_path)
            nscore = retry_scored if is_retry else load(scored_path)
            nvalid = nscore['status'] == 'valid_completion' and nresult.get('exception_info') is None
            assert nscore['details']['benchmark_version'] == '4.7.0'
            for task in ('t2', 't3'):
                pin(b[f'prediction_{task}_path'], b[f'prediction_{task}_sha256'])
                pin(case['p2' if task == 't2' else 'p3'], expected_hashes[case['p2' if task == 't2' else 'p3']])
            assert case['truth']['transform'] == 'none'
            for task, path in case['truth']['paths'].items():
                expected = b[f'reference_{task.lower()}_sha256']
                pin(path, expected)
                assert old['inputs'][path] == expected
                refs_checked.add((pid, task))
            receipt_path = ntrial / 'agent/name-only-isolation.json'
            pin(receipt_path, expected_hashes[str(receipt_path)])
            receipt = load(receipt_path)
            assert receipt['ready'] and receipt['direct_tcp_blocked']
            assert all(' 403 ' in v for v in receipt['blocked'].values())
            for condition, result, trial, valid in [('Bundle', bresult, b['trial'], bvalid),
                                                     ('Name', nresult, str(ntrial), nvalid)]:
                a = result['config']['agent']
                settings.append(dict(system=system, protocol_id=pid, condition=condition, trial=trial,
                    model=a['model_name'], cli_version=result['agent_info']['version'],
                    configured_reasoning_effort=a.get('kwargs', {}).get('reasoning_effort'),
                    started_at=result['started_at'], exception_type=(result.get('exception_info') or {}).get('exception_type')))
                completion[system, condition]['selected'] += 1
                completion[system, condition]['valid' if valid else 'failed'] += 1
                memberships.append(dict(system=system, protocol_id=pid, condition=condition, trial=trial,
                    source_row_id=b['row_id'] if condition == 'Bundle' else '',
                    case_id=b['case_id'] if condition == 'Bundle' else case['case_id'],
                    valid_completion=valid, replaces_failed_execution=bool(is_retry and condition == 'Name')))
            for metric, label in zip(METRICS, LABELS):
                prior = next(r for r in source_rows if r['system'] == system and r['protocol_id'] == pid and r['metric'] == metric)
                bundle = float(b[metric]); name = float(nscore['scores'][metric])
                assert math.isclose(bundle, float(prior['bundle_fig3a']), rel_tol=0, abs_tol=1e-12)
                assert math.isclose(name, float(prior['name_only']), rel_tol=0, abs_tol=1e-12)
                nreq = int(float(b['required_families']))
                assert nreq == int(prior['n_required'])
                eligible = metric != METRICS[0] or nreq > 0
                native.append(dict(system=system, model=model, protocol_id=pid,
                    protocol_name=PROTOCOLS[pid].replace("3$'$", "3'"), metric=metric, label=label,
                    n_required=nreq, eligible=eligible, valid_pair=bvalid and nvalid,
                    bundle=bundle, name_only=name, delta=name-bundle,
                    bundle_trial=b['trial'], name_trial=str(ntrial), bundle_case_id=b['case_id'],
                    name_case_id=case['case_id'], name_is_retry=is_retry))
    assert len(native) == 240 and len(refs_checked) == 60
    native_summary = []
    for si, (system, model, _, _) in enumerate(SYSTEMS):
        for metric, label in zip(METRICS, LABELS):
            pairs = [r for r in native if r['system'] == system and r['metric'] == metric and r['eligible'] and r['valid_pair']]
            assert len(pairs) == (19 if metric == METRICS[0] else 20)
            result = paired_summary([r['delta'] for r in pairs], family=NATIVE_FAMILY, system_index=si, family_code=0)
            native_summary.append(dict(system=system, model=model, metric=metric, label=label,
                n_protocols=len(pairs), bundle_mean=math.fsum(r['bundle'] for r in pairs)/len(pairs),
                name_only_mean=math.fsum(r['name_only'] for r in pairs)/len(pairs), **result))

    t1 = read(OLD / 'task1_per_protocol.csv')
    current_t1 = read(CAMERA / 'task1_controls_recomputed.csv')
    pin(CAMERA / 'reproducibility_manifest.json')
    pin(CAMERA / 'task1_controls_recomputed.csv',
        load(CAMERA / 'reproducibility_manifest.json')['artifacts']['task1_controls_recomputed.csv'])
    lookup = {(r['model_family'], r['protocol_id']): r for r in current_t1}
    api_summary, api_rows = [], []
    t1_prior = {r['model']: r for r in read(OLD / 'task1_summary.csv')}
    for si, (_, model, _, _) in enumerate(SYSTEMS):
        pairs = sorted([r for r in t1 if r['model_family'] == model], key=lambda r: list(PROTOCOLS).index(r['protocol_id']))
        assert len(pairs) == 20 and all(r['paired'] == 'True' for r in pairs)
        for r in pairs:
            c = lookup[model, r['protocol_id']]
            for k in ('pdf_similarity', 'name_only_similarity'):
                assert math.isclose(float(r[k]), float(c[k]), rel_tol=0, abs_tol=1e-12)
            assert r['pdf_invalid_output'] == r['name_only_invalid_output'] == 'False'
            delta = float(r['name_only_similarity']) - float(r['pdf_similarity'])
            api_rows.append(dict(r, delta=delta))
        result = paired_summary([float(r['name_only_similarity'])-float(r['pdf_similarity']) for r in pairs],
                                family=API_FAMILY, system_index=si, family_code=1)
        r = dict(model=model, metric='sequence_similarity', n_protocols=20,
            pdf_mean=math.fsum(float(x['pdf_similarity']) for x in pairs)/20,
            name_only_mean=math.fsum(float(x['name_only_similarity']) for x in pairs)/20,
            pdf_exact=sum(x['pdf_exact']=='True' for x in pairs),
            name_only_exact=sum(x['name_only_exact']=='True' for x in pairs),
            pdf_empty_outputs=sum(x['pdf_abstention']=='True' for x in pairs), **result)
        for k in ('pdf_mean', 'name_only_mean', 'pdf_exact', 'name_only_exact'):
            assert math.isclose(r[k], float(t1_prior[model][k]), abs_tol=1e-12)
        api_summary.append(r)

    out.mkdir()
    csvout(out/'native_per_protocol.csv', native)
    csvout(out/'native_statistics.csv', native_summary)
    csvout(out/'task1_per_protocol.csv', api_rows)
    csvout(out/'task1_statistics.csv', api_summary)
    csvout(out/'execution_settings.csv', settings)
    csvout(out/'completion_counts.csv', [dict(system=s, condition=c, selected_executions=v['selected'],
        valid_completions=v['valid'], failed_completions=v['failed']) for (s,c), v in completion.items()])

    # Reuse the user-approved portrait layout without changing any layout command.
    render_rows = []
    for r in native:
        render_rows.append(dict(system=r['system'], protocol_id=r['protocol_id'], metric=r['metric'],
            eligible=str(r['eligible']), valid_pair=str(r['valid_pair']), bundle_n3=r['bundle'], name_only=r['name_only']))
    render_summary = [dict(r, name_minus_bundle=r['delta']) for r in native_summary]
    tex = portrait_table(render_rows, render_summary, recovered=True)
    tex = tex.replace('with Bundle averaged over three runs and Name from one.',
                      'with one selected execution per condition; Bundle is the Figure~3a execution.')
    tex = tex.replace('Bold indicates the higher observed score within a pair; ties are unbolded.',
                      'Bold indicates the higher score within a pair at displayed precision; ties are unbolded.')
    for setting in (r'\fontsize{8.1pt}{9.4pt}\selectfont', r'\setlength{\tabcolsep}{1.65pt}',
                    r'\renewcommand{\arraystretch}{1.01}',
                    r'\begin{longtable}{@{}>{\raggedright\arraybackslash}p{0.235\linewidth}l*{10}{r}@{}}'):
        assert setting in tex and setting in (LATEST/'name_only_combined_tasks1-3.tex').read_text()
    assert 'averaged over three' not in tex and r'\multicolumn{1}{c}{F}' not in tex
    (out/'name_only_combined_tasks1-3.tex').write_text(tex)
    (out/'name_only_paired_statistics.tex').write_text(statistics_table(native_summary, api_summary))
    (out/'appendix_section.tex').write_text(appendix(native_summary, api_summary))

    report = ['Protocol-name-only control: primary n=1 versus n=1 comparison',
        f"Scorer 4.7.0; reference set {old['reference_set_id']}.",
        'Bundle = original Figure 3a selected execution; Name = one selected valid name-only execution.',
        'Claude SHARE-seq uses the successful retry now stored in the original name-only job.',
        'No predictions, references, historical scores, or main.tex were changed; no agent inference.',
        'All 120 selected native executions (60 Bundle + 60 Name) are valid. No failed pairs remain.',
        'Task 2 excludes ddSEQ: n=19; Task 3: n=20. Units are protocols, not repeated executions.',
        'Two-sided exact sign-flip test of mean(Name - source); all 2^n sign assignments including zeros/ties.',
        'Bonferroni: 12 native comparisons; separate family of 3 Task 1 comparisons.',
        f'Pointwise percentile paired protocol bootstrap: {N_BOOTSTRAP} resamples; master seed {SEED}.',
        'These CIs are not multiplicity-adjusted or run-to-run uncertainty intervals.',
        'Sign-flip validity requires within-pair exchangeability/sign symmetry under the null; this is not a randomized causal experiment.',
        '', 'NATIVE: Model | Metric | n | Bundle | Name | Delta [pointwise 95% CI] | raw p | Bonferroni p']
    for r in native_summary:
        report.append(f"{r['model']} | {r['label']} | {r['n_protocols']} | {r['bundle_mean']:.3f} | {r['name_only_mean']:.3f} | "
                      f"{r['delta']:+.3f} [{r['ci_low']:+.3f}, {r['ci_high']:+.3f}] | {r['p_raw']:.8f} | {r['p_bonferroni']:.8f}")
    report += ['', 'TASK 1 API: Model | n | PDF | Name | Delta [pointwise 95% CI] | raw p | Bonferroni p | exact PDF/Name']
    for r in api_summary:
        report.append(f"{r['model']} | 20 | {r['pdf_mean']:.3f} | {r['name_only_mean']:.3f} | "
                      f"{r['delta']:+.3f} [{r['ci_low']:+.3f}, {r['ci_high']:+.3f}] | {r['p_raw']:.8f} | {r['p_bonferroni']:.8f} | {r['pdf_exact']}/20, {r['name_only_exact']}/20")
    report += ['', 'Interpretation:',
        '- All 12 native point estimates favor Bundle; none meets Bonferroni-adjusted p<0.05.',
        '- Task 1 mean similarity favors Name for all three models; only Claude meets its separate Bonferroni-adjusted threshold.',
        '- Exact reconstruction counts remain descriptive and are lower for Name in all three API models.',
        '- Failure to reject is not evidence of equivalence; successful reconstruction without documents is not proof of memorization.',
        '- Gemini native CLI versions differ: Antigravity 1.1.13 (selected Bundle) versus 1.3.2 (Name). GPT/Claude CLI versions match.',
        '- Bundle requires evidence grounding; Name permits internal knowledge. Gemini Task 1 routes differ (PDF OpenRouter / Name Vertex).',
        '- Claude SHARE-seq requested 128000 output tokens on retry; its terminal modelUsage still reports 64000, so effective-cap increase is unverified.',
        '', 'Reproduce:', 'python analysis/paper_results/name_only_v4_7_0/analyze_n1_paired.py --out <fresh-directory>',
        '', 'Methods references:', 'https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html',
        'https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html']
    (out/'summary.txt').write_text('\n'.join(report)+'\n')
    assert all(sha(p) == h for p,h in pinned.items())
    dump(out/'input_manifest.json', dict(inputs_sha256=pinned, memberships=memberships,
        scorer_version='4.7.0', scorer_sha256=camera['scorer_sha256'], reference_set_id=old['reference_set_id'],
        baseline_selection='Original Figure 3a, one selected execution per system/protocol',
        name_selection='Single name-only execution, with verified Claude SHARE-seq recovery',
        native_bonferroni_family=12, task1_bonferroni_family=3,
        bootstrap_seed=SEED, bootstrap_resamples=N_BOOTSTRAP, bootstrap_interval='pointwise percentile',
        permutation='exact enumeration of all 2^n sign assignments; statistic absolute paired mean difference',
        software=dict(python=sys.version, numpy=np.__version__, scipy=scipy.__version__), new_agent_inference=False,
        reference_files_verified=len(refs_checked), statistical_validation='Independent SciPy and brute-force checks on five edge-case vectors passed'))
    print('\n'.join(report[:12]+report[13:30]), flush=True)


def compile_appendix(out):
    work = Path(tempfile.mkdtemp(prefix='libstruct-name-only-n1-'))
    (work/'tables').mkdir()
    shutil.copy2(PAPER/'neurips_2026.sty', work/'neurips_2026.sty')
    for name in ('name_only_combined_tasks1-3.tex', 'name_only_paired_statistics.tex'):
        shutil.copy2(out/name, work/'tables'/name)
    shutil.copy2(out/'appendix_section.tex', work/'appendix_section.tex')
    preamble = (PAPER/'main.tex').read_text().split(r'\title{',1)[0]
    (work/'check.tex').write_text(preamble+'\n'+r'\usepackage{longtable}'+'\n'+
        r'\begin{document}\appendix\renewcommand{\thetable}{S\arabic{table}}'+ '\n'+
        r'\input{appendix_section.tex}\end{document}')
    command=['/opt/homebrew/bin/tectonic','--only-cached','--keep-logs','--keep-intermediates','check.tex']
    proc=subprocess.run(command,cwd=work,capture_output=True,text=True)
    (out/'compile.log').write_text(proc.stdout+proc.stderr)
    log=(work/'check.log').read_text() if (work/'check.log').exists() else ''
    result=dict(command=command,temporary_directory=str(work),returncode=proc.returncode,
        errors=re.findall(r'^!.*',log,re.M),overfull_boxes=re.findall(r'Overfull .+',log),
        unresolved_references=re.findall(r'Reference .+ undefined',log))
    if proc.returncode==0:
        import fitz
        pdf=fitz.open(work/'check.pdf')
        result['pages']=len(pdf);result['previews']=[]
        for i,page in enumerate(pdf):
            p=work/f'page-{i+1}.png';page.get_pixmap(matrix=fitz.Matrix(1.6,1.6)).save(p)
            result['previews'].append(str(p))
    dump(out/'compile_verification.json',result)
    manifest=load(out/'input_manifest.json')
    assert all(sha(p)==h for p,h in manifest['inputs_sha256'].items())
    dump(out/'verification.json',dict(inputs_unchanged=True,agent_inference=False,main_tex_unchanged=True,
        script_sha256=sha(__file__),outputs_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='verification.json'},
        compile=result))
    print(json.dumps(result,indent=2),flush=True)
    assert proc.returncode==0 and not result['errors'] and not result['overfull_boxes'] and not result['unresolved_references']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=BASE/'results_n1_v1')
    parser.add_argument('--compile-only',action='store_true')
    args=parser.parse_args()
    if not args.compile_only:
        analyze(args.out.resolve())
    compile_appendix(args.out.resolve())
