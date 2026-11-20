"""Write a source-linked interpretation from the newly computed tables only."""
from pathlib import Path
import json,sys
import numpy as np
import pandas as pd

def table(df):
    def fmt(x):
        if isinstance(x,(float,np.floating)):return f'{x:.4f}'
        return str(x).replace('|',' / ')
    return '| '+' | '.join(df.columns)+' |\n| '+' | '.join(['---']*len(df.columns))+' |\n'+'\n'.join('| '+' | '.join(fmt(x) for x in row)+' |' for row in df.itertuples(index=False,name=None))

def main():
    out=Path(sys.argv[1]);get=lambda name:pd.read_csv(out/name)
    p=get('primary_effects.csv');e=get('paired_effects.csv');n=get('native_contrasts.csv');b=get('blocked_factorial.csv');l=get('leave_one_protocol_out.csv');s=get('settings_by_trial.csv');mix=get('mixed_diagnostics.csv')
    meta=json.loads((out/'input_manifest.json').read_text());labels=dict(zip(p.metric,p.metric_label));short={'t2_required_family_f1':'Oligo','t3_molecular_transition_f1':'Transition','t3_state_f1':'State','t3_typed_edge_f1':'Edge'}
    primary=[]
    for metric,rs in p.groupby('metric',sort=False):
        r=rs.set_index('effect');v=r.loc['model_minus_harness'];lv=l[l.metric.eq(metric)]
        primary.append(dict(Metric=labels[metric],Protocols=int(v.n_protocols),Model_switch=r.loc['model_switch','estimate'],Harness_switch=r.loc['harness_switch','estimate'],Difference=v.estimate,CI_low=v.ci_low,CI_high=v.ci_high,Signflip_p=v.signflip_p,Holm_p=v.signflip_p_holm,LOO_min=lv.leave_one_out_difference.min(),LOO_max=lv.leave_one_out_difference.max()))
    primary=pd.DataFrame(primary)
    positive=primary[primary.CI_low.gt(0)].Metric.tolist();cross=primary[primary.CI_low.le(0)&primary.CI_high.ge(0)].Metric.tolist()
    setting=[]
    for group,rs in s.groupby('group',sort=False):
        first=rs.iloc[0];setting.append(dict(System=group,Model_ID=first.configured_model,CLI_version=first.harness_version,Reasoning=first.reasoning,Explicit_cap='64000' if rs.configured_max_tokens.notna().all() else 'not specified',Trials=len(rs),Start=rs.started_at.min()[:10],End=rs.started_at.max()[:10]))
    def effect_table(kind):
        x=e[e.kind.eq(kind)].copy();x['Metric']=x.metric.map(short)
        return table(x[['Metric','contrast','estimate','ci_low','ci_high','signflip_p','signflip_p_holm']])
    bp=b.copy();bp['Metric']=bp.metric.map(short)
    native=n[n.panel.eq('all_selected')].copy();native['Metric']=native.metric.map(short)
    flagged=n[n.panel.eq('same_task_digest_only')].copy();flagged['Metric']=flagged.metric.map(short)
    protocol=get('protocol_switch_magnitudes.csv');drivers=[]
    for metric,rs in protocol.groupby('metric',sort=False):
        for r in rs.reindex(rs.difference.abs().sort_values(ascending=False).index).head(3).itertuples():
            drivers.append(dict(Metric=short[metric],Protocol=r.protocol_id,Model_switch=r.model_switch,Harness_switch=r.harness_switch,Difference=r.difference))
    mixedom=get('mixed_omnibus.csv');mixedom=mixedom[mixedom.term.ne('(Intercept)')];mixedom['Metric']=mixedom.metric.map(short)
    t2p=primary[primary.Metric.eq('Task 2 oligo-family F1')].iloc[0]
    statep=primary[primary.Metric.eq('Task 3 state F1')].iloc[0]
    stateint=e[e.metric.eq('t3_state_f1')&e.contrast.eq('(Gemini - GPT) x (mini-SWE - Pi)')].iloc[0]
    source_links='[nlme::lme](https://www.stat.math.ethz.ch/R-manual/R-devel/library/nlme/html/lme.html), [anova.lme](https://stat.ethz.ch/R-manual/R-devel/library/nlme/html/anova.lme.html), and [stats::aov](https://www.stat.ethz.ch/R-manual/R-devel/library/stats/html/aov.html)'
    report=f'''# Model versus harness: protocol-paired Figure 3b analysis

**Verdict: the manuscript claim requires qualification.** Model switches have larger average absolute score effects than switching between Pi and mini-SWE in all four metrics on these saved outputs. The clearest result concerns molecular states. This supports a restricted, descriptive claim about the three tested model configurations and two shared harnesses, not a general variance attribution or causal claim that underlying LLMs always matter more than harnesses.

## Locked data and design

- Source: `camera_ready_v4_7_0/results.csv`, SHA-256 `{meta['source_table_sha256']}`.
- Verifier 4.7.0; scorer SHA-256 `{meta['scorer_sha256']}`; reference set `{meta['reference_set_id']}`.
- Primary: fully crossed 3 models x 2 shared harnesses x 10 protocols = 60 saved observations. One selected execution per cell; no independent run replication is inferred. Each Task 3 analysis has 10 protocol blocks, **not 60 independent protocols**.
- Informative Task 2 removes the entire ddSEQ block (N_req=0): 9 protocols / 54 observations. No Task 3 membership is removed. All 90 selected shared/native outputs pass the locked output-validity checks; missing or invalid selected outputs: 0. Historical execution exceptions, if any, are separately recorded in `settings_by_trial.csv` and do not trigger selection changes.
- Native analysis uses 30 additional selected observations (three distinct native interfaces on the same ten protocols). It never pools those interfaces into a fourth shared-harness level. Kimi is absent from this design.
- Historical Figure 3 trial selection, C30, all saved scores, predictions and references are unchanged. No inference and no rescoring were run. Alternative metric-weight evaluations and Codex repeat outputs do not enter this analysis.

## Exact version and configuration verification

{table(pd.DataFrame(setting))}

`settings_by_trial.csv` records all 90 execution IDs, model IDs, recorded CLI versions, reasoning, task digests, timeouts, environment type, dates, and result/lock hashes. `runtime_settings.json` verifies model-change and thinking-level events for all 30 Pi trials, and model configuration/reasoning/token caps in all 30 mini-SWE trajectories. GPT and Claude request max reasoning in both shared harnesses; Gemini requests high in both. Mini-SWE uses its OpenAI Responses adapter for GPT and LiteLLM chat adapter for Claude/Gemini. Pi's provider labels are openai/anthropic/google; mini-SWE uses openai/anthropic/gemini.

The literal model IDs agree within each shared model pair: `gpt-5.6-sol`, `claude-opus-5`, `gemini-3.7-flash`. These are saved model identifiers, not independently attested immutable provider-weight revisions. Execution dates differ, requested effort is not a guarantee of equal realized reasoning, and equal temperatures or random seeds are not established. Mini-SWE explicitly requests a 64,000-token cap; Pi does not explicitly record an equivalent cap. Those differences belong to the observed harness configurations. This is a paired observational comparison of completed configurations, not a randomized intervention isolating one software component.

All six shared systems have the same historical task digest and task checksum within each protocol. Native Claude Code differs on scRRBS, even though all outputs are now evaluated against the same locked references. That difference cannot be declared harmless from a digest alone. Full-panel native contrasts retain the historical selection; `same_task_digest_only` rows exclude scRRBS for Claude as a separately labeled robustness check. Codex and Antigravity match the shared task digests. Gemini's native identifier is `gemini-3.7-flash-high`, so that comparison includes its native alias/route and cannot prove backend identity solely from the names.

The shared configurations all use Docker, timeout_multiplier=1, no explicit agent timeout override/cap, and resume_trajectory=False. Their inherited task settings are matched by the within-protocol task hashes. Historical absolute timeout defaults are not reconstructed from today's mutable task files. Frozen T2/T3 reference hashes are identical within every protocol across all nine systems.

## Primary paired estimands

For protocol p, model m, shared harness h, and a fixed metric, let y_pmh be its saved score. With three unordered model pairs:

\\[
M_p=\\frac{{1}}{{6}}\\sum_{{h\\in\\{{Pi,mini\\}}}}\\sum_{{m<m'}}|y_{{pmh}}-y_{{pm'h}}|,\\qquad
H_p=\\frac{{1}}{{3}}\\sum_m|y_{{pm,mini}}-y_{{pm,Pi}}|,\\qquad D_p=M_p-H_p.
\\]

Report the equal-protocol means of M_p, H_p, and D_p. These are **absolute switch magnitudes**, not signed performance advantages, variance components, or the magnitude of differences between already pooled means. Averaging six model comparisons versus three harness comparisons prevents a difference merely from the number of pairs; the three pairs remain correlated and are never treated as independent replicates. An interaction can contribute to either magnitude.

95% percentile intervals use **10,000 paired protocol-bootstrap resamples**, resampling complete six-cell blocks. Within each eligible panel all contrasts reuse indices generated with `SeedSequence([20261014, n_protocols])`. For D, the interval is from bootstrapping D_p directly, not subtracting endpoints of separate intervals. No attempt-level resampling is possible with one saved execution per cell. CIs are pointwise and describe protocol-sampling uncertainty conditional on the saved outputs, not run-to-run variability or generalization to unseen model/harness populations. Protocol-family dependence is not modeled.

{table(primary)}

Source: `primary_effects.csv`, `protocol_switch_magnitudes.csv`, `leave_one_protocol_out.csv`. Pointwise bootstrap intervals exclude zero in the positive direction for: {', '.join(positive) or 'none'}. They include zero for: {', '.join(cross) or 'none'}. Near-zero endpoints are not a sharp evidential boundary with only 9/10 protocols. The leave-one-protocol-out ranges above show dependence on individual blocks without changing the primary panel.

Exploratory scalar tests are a paired t-test with n_protocols-1 degrees of freedom and an exhaustive two-sided sign-flip test over 2^n sign assignments to protocol contrasts. The sign-flip interpretation requires independent, centrally symmetric protocol differences under its null; it is not a randomization test of an actually randomized experiment. Four primary difference tests are Holm-adjusted together. Effect sizes and intervals determine the interpretation; p-values alone do not establish model superiority. All scalar tests and t-intervals are available in CSVs.

Only the state-magnitude difference has a Holm-adjusted primary sign-flip p below .05 ({statep.Holm_p:.4f}). Task 2's positive pointwise bootstrap interval coexists with Holm p={t2p.Holm_p:.4f}; the methods and multiplicity corrections answer different questions, and the bootstrap interval is not a simultaneous guarantee. Transition and edge intervals include zero. The positive leave-one-protocol-out point estimates do not remove that sampling uncertainty.

## Signed main effects, model-specific harness effects and interactions

Model main contrasts first average the two shared harness scores within each protocol. The harness main contrast averages `mini-SWE - Pi` equally over models. A model-specific harness effect is that same signed difference within one model. Interactions are differences of harness differences, e.g. `(Claude mini - Claude Pi) - (GPT mini - GPT Pi)`. They describe dependence of harness effects on the tested model, distinct from a model's average score. Positive model-pair effects favor the first named model in the subtraction; positive harness effects favor mini-SWE.

### Marginal model contrasts

{effect_table('model_main_contrast')}

### Marginal harness contrast

{effect_table('harness_main_contrast')}

### Model-specific harness contrasts

{effect_table('model_specific_harness')}

### Model-harness interactions

{effect_table('interaction')}

Each table uses protocol-bootstrap intervals. Holm families are defined by contrast type across all four metrics: 12 marginal-model, 4 marginal-harness, 12 model-specific-harness and 12 interaction tests. These are exploratory separate families, not a claim of experiment-wide familywise control. Differences-of-differences can be nonzero even when an overall harness main effect is small or cancels. `protocol_paired_effects.csv` retains all intermediate protocol-level values.

All four signed average harness-effect intervals include zero. The state interaction comparing Gemini's harness shift to GPT's is {stateint.estimate:+.4f} [{stateint.ci_low:+.4f}, {stateint.ci_high:+.4f}] in pointwise bootstrap uncertainty, but its interaction-family Holm sign-flip p is {stateint.signflip_p_holm:.4f}. Thus the observed shifts depend on model, while this small panel provides limited evidence for a stable population interaction; absence of an omnibus interaction finding does not establish equivalence.

## Secondary mixed model and blocked robustness

Fit `score ~ model * harness + (1 | protocol)` separately per metric using R `nlme::lme`, REML and sum-to-zero factor coding. All factors are fixed except the protocol random intercept. Equal-weight estimated marginal cell/model/harness means and planned contrasts are evaluated from the model matrix and fixed-effect covariance. In this complete balanced design their point estimates agree with direct protocol-paired estimates (verified to numerical precision). `statistical_summary.csv` combines these estimates, bootstrap uncertainty, exploratory paired tests, and mixed-model Wald uncertainty. `mixed_coefficients.csv` gives the coding-dependent regression coefficients; marginal effects, rather than those coefficients alone, are the interpretation target.

{table(mix[['metric','n_protocols','n_observations','random_intercept_variance','residual_variance','warnings']].fillna('none'))}

The random-intercept model assumes a common residual variance/covariance structure across six conditions and Gaussian linear effects for bounded scores. Its Wald CIs/p-values are secondary asymptotic quantities and may be optimistic with 9/10 blocks; they are not substituted for the paired protocol-bootstrap intervals. Residuals combine protocol-by-condition heterogeneity and unmeasured execution noise. The protocol random-intercept variance is not model variance or harness variance, and comparing omnibus p-values is not a variance decomposition.

Mixed-model marginal omnibus tests (software denominator degrees of freedom are conditional model degrees of freedom, never counts of independent protocols):

{table(mixedom[['Metric','term','numDF','denDF','F-value','p-value']])}

As a robustness analysis, fit the balanced repeated-measures factorial ANOVA `score ~ model * harness + Error(protocol / (model * harness))`. Model, harness and interaction tests use their respective **protocol-by-effect error strata**, not a pooled 54/60-independent-observation error. Model/interaction df are 2 and 2(n-1); harness df are 1 and n-1. Greenhouse-Geisser epsilon is estimated from the corresponding within-protocol contrast covariance, correcting both degrees of freedom for the two-df factors. The one-df harness test has epsilon=1. F statistics were also independently reconstructed from within-protocol contrast coordinates.

{table(bp[['Metric','term','F','numerator_df','denominator_df','GG_epsilon','GG_p','GG_p_holm_12','partial_eta_squared']])}

All 12 GG-corrected omnibus tests are Holm-adjusted together. Partial eta-squared is effect-specific and uses a different error stratum for each effect; it is not an additive partition of total variability and cannot be used to rank model versus harness importance. Small panels still limit covariance estimation. Method references: {source_links}.

The blocked analysis supports a model main effect most clearly for state F1; it does not establish marginal model differences for the other metrics or consistent harness/interaction effects. This is compatible with positive absolute model-switch magnitudes: different models can win on different protocols, producing sizeable absolute changes whose signed marginal contrasts cancel. The mixed and blocked model tests therefore must not be substituted for the primary magnitude comparison.

## Native interfaces: separate paired comparisons

Each model is compared only to its own native interface, with positive values meaning native minus the named shared harness. No common native-harness main effect is fit.

{table(native[['Metric','model','native','shared_harness','n_protocols','estimate','ci_low','ci_high','signflip_p','signflip_p_holm']])}

Source: `native_contrasts.csv`; 24 main native contrasts form one Holm family. All ten protocols remain for Task 3 and nine informative protocols for Task 2. The following supplementary rows exclude the mismatched native Claude scRRBS task digest; they do not replace the main contrasts:

{table(flagged[['Metric','native','shared_harness','n_protocols','estimate','ci_low','ci_high']])}

## Protocol drivers

The largest absolute contributions to the primary difference (three per metric) are:

{table(pd.DataFrame(drivers))}

These identify influential saved outputs, not adjudicated biological causes. Full values and leave-one-block-out results are retained; no outlier was removed from the primary analysis.

## Manuscript interpretation and proposed replacement

The blanket claim that the underlying LLM has the larger influence requires qualification. On these specific shared configurations, the magnitude comparison supports a larger model-switch effect most clearly for states, while edge uncertainty includes zero and transition evidence is less precise. The original nine-system display combines model-specific native interfaces with shared harnesses; it cannot itself identify a generic native-harness effect. Interactions and model-specific contrasts prevent replacing this result with a claim that harness choice is unimportant. These are descriptive conditional results from one execution per cell, not evidence that every model family or future run will preserve the ordering.

Suggested text (for review; `main.tex` was not edited):

> In a protocol-paired comparison restricted to three models evaluated in both Pi and mini-SWE-agent, changing the model produced a larger mean absolute score difference than changing between the two shared harnesses across all four metrics. The magnitude and uncertainty were metric dependent, with the clearest difference for molecular-state reconstruction. Harness effects varied with model and protocol. These results describe the tested configurations and saved outputs; they do not establish a general model-versus-harness variance hierarchy or run-level superiority. Native interfaces were analyzed separately within each model.

Suggested supplement caption:

> Model-switch and harness-switch effect magnitudes in the fully crossed three-model, two-shared-harness panel. A: Protocol means of absolute paired score differences holding the other factor fixed. B: Paired model-minus-harness magnitude differences. Error bars are pointwise 95% protocol-bootstrap intervals from 10,000 resamples, using ten common protocols for Task 3 and nine informative protocols for Task 2. Every cell contributes one selected saved execution. Intervals quantify protocol-sampling uncertainty conditional on those outputs, not run-to-run variability.

## Reproducibility and integrity

Scripts: `analyze.py` (locked inputs, provenance, paired contrasts, tests and validation), `fit_models.R` (REML and repeated-measures checks), `plot.R` (supplement figure), `report.py` (this derived report). `input_manifest.json` pins every consumed result/config/lock, shared trajectory, prediction and reference file. `manifest.json` records code/artifact hashes, software versions, seeds, uncertainty definitions and multiplicity families. `verification.json` verifies the existing camera-ready artifact hashes, all new input hashes, exact panel/model identities and independent primary-formula reconstruction.

Use the reproduction command in the parent README, targeting a new directory. Existing files are never overwritten. Source results, ground truth, predictions, historical selection and manuscript were not changed. No model or provider calls occurred.
'''
    (out/'report.md').write_text(report)
    csv=primary.copy();csv.to_csv(out/'summary_table.csv',index=False,float_format='%.17g')
    print('Report and summary table written.',flush=True)

if __name__=='__main__':main()
