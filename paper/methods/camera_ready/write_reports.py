"""Generate proposed manuscript text only after verified camera-ready tables exist."""
from tables import *
from figures import LABELS,SHORT
import inspect,re
from libstruct_bench.libgen.v4_7_0 import scoring

def md(frame,cols=None,digits=3):
    if cols:frame=frame[cols]
    def cell(v):
        if isinstance(v,(float,np.floating)):return f'{v:.{digits}f}'
        return str(v).replace('|','/').replace('\n',' ')
    return '| '+' | '.join(frame.columns)+' |\n| '+' | '.join(['---']*len(frame.columns))+' |\n'+'\n'.join('| '+' | '.join(cell(v) for v in row)+' |' for row in frame.itertuples(index=False,name=None))
def tex_escape(s):
    return str(s).replace('_',r'\_').replace('%',r'\%').replace('&',r'\&')
def main():
    lock=load(OUT/'input_manifest.json');b=original();a=pd.read_csv(OUT/'aggregates.csv');changes=pd.read_csv(OUT/'score_changes.csv');counts=pd.read_csv(OUT/'completion_counts.csv');rep=pd.read_csv(OUT/'repeated_run_summary.csv');t1=pd.read_csv(OUT/'task1_summary.csv');t1pairs=pd.read_csv(OUT/'task1_paired_contrasts.csv');contrasts=pd.read_csv(OUT/'paired_contrasts.csv');zero=pd.read_csv(OUT/'zero_required.csv')
    sens=OUT/'sensitivity';sa=pd.read_csv(sens/'aggregates.csv');ranks=pd.read_csv(sens/'ranking_correlations.csv');freq=pd.read_csv(sens/'rank_first_frequencies.csv');drivers=pd.read_csv(sens/'protocol_drivers.csv');sc=pd.read_csv(sens/'paired_contrasts.csv')
    def values(panel,group):return [float(a[(a.panel==panel)&(a.group==group)&(a.metric==m)&(a.policy=='primary')]['mean'].iloc[0]) for m in METRICS]
    def fmt(panel,group):return ', '.join(f'{v:.3f}' for v in values(panel,group))
    def source(panel,group='*',metric='*',policy='primary'):return f'aggregates.csv: panel={panel}, group={group}, metric={metric}, policy={policy}'
    paneltable=a[(a.policy=='primary')&a.metric.isin(METRICS)].pivot(index=['panel','group'],columns='metric',values='mean').reset_index()
    paneltable.columns.name=None
    primary=paneltable[paneltable.panel!='fig3common10']
    reference=b[b.panel.isin(['fig3a','fig3b','fig4a','fig4c','fig4d'])].drop_duplicates('case_id')
    affected=reference[[f'reference_delta_{m}' for m in METRICS]].abs().max(axis=1)>1e-12
    assert affected.sum()==14
    bymetric=[]
    for panel in ['fig3a','fig3b','fig4a','fig4c','fig4d']:
        for metric in METRICS:
            pol='informative_only' if metric==METRICS[0] else 'unchanged_panel'
            rs=ranks[(ranks.panel==panel)&(ranks.metric==metric)&(ranks.aggregation==pol)]
            rr=sa[(sa.panel==panel)&(sa.metric==metric)&(sa.aggregation==pol)]
            bymetric.append(dict(panel=panel,metric=metric,min_tau_b=rs.tau_b.min(),configurations_with_strict_reversal=int((rs.reversed_pairs>0).sum()),max_absolute_mean_change=rr.delta.abs().max()))
    stability=pd.DataFrame(bymetric);stability.to_csv(OUT/'sensitivity_summary.csv',index=False)
    winners=freq[(freq.kind=='dirichlet')&((freq.metric!=METRICS[0])|freq.aggregation.eq('informative_only'))&(freq.rank_first_frequency>0)].copy()
    winners.to_csv(OUT/'sensitivity_rank_first.csv',index=False)
    # The two validation panels are intentionally distinct, not repeated runs.
    memberships=b[b.panel.isin(['fig3a','fig3b','fig4a','fig4c','fig4d'])][['panel','group','protocol_id','trial','case_id']]
    memberships.to_csv(OUT/'panel_membership.csv',index=False)
    def source_link(name):
        fn=getattr(scoring,name);line=inspect.getsourcelines(fn)[1]
        return f'[`{name}`]({ROOT}/src/libstruct_bench/libgen/v4_7_0/scoring.py:{line})'
    defs=r'''## Metric definitions and reporting policy

Task 1 uses the unchanged legacy string scorer, not the linked T2/T3 evaluator. Canonical references are projected to technical sequence structure, excluding biological payload; predefined inDrop barcode-length alternatives (8–11) are evaluated identically across conditions. Recognized typed placeholders expand to role-specific characters. Similarity is $1-d_{Lev}(x,y)/\max(|x|,|y|)$, with empty/empty defined as one. GT libraries are visited in order and matched greedily, preferring ID then modality before sequence similarity. The protocol score is the mean over GT libraries; missing libraries score zero, while extra predictions do not lower similarity. Protocol exactness additionally requires equal library counts and zero edit distance for every matched reference. These limitations remain; global matching and extra-library penalties were diagnostic alternatives, not silently adopted camera-ready metrics.

For Tasks 2/3 define $H(a,p,g)=2a/(p+g)$ when $p+g>0$, and $H(0,0,0)=1$. Let $E$ denote source support `explicit` or `derivable`. With enabled components $e_k$, $W(d,e;w)=\sum e_kw_kd_k/\sum e_kw_k$, or zero if no component is enabled. Support masks and neutralization rules are unchanged in 4.7.0.

For Task 2 let $O$ be GT oligo records and $U$ the IDs referenced by any GT transition or state oligo derivation. $G_{req}=\{o\in O:id(o)\in U,\ o\text{ has a recoverable sequence claim}\}$, $G_{neutral}=\{o:id(o)\in U\}\setminus G_{req}$, and $G_{optional}=\{o:id(o)\notin U\}$. GT records are not deduplicated. Predicted records form the deterministic, GT-aware family set $F$; $Z$ is the subset neutralized by optional/non-recoverable correspondence. Precisely, $N_{req}=|G_{req}|$ and $N_{out}^{eff}=|F\setminus Z|$: the latter counts predictions, not GT records. All predicted families initially count, even if unused in the predicted workflow.

Family similarity is the best member's supported weighted average of sequence, modification, molecular-kind, orientation, and controlled-role agreement with weights $(.65,.15,.10,.05,.05)$. Sequence uses directional token edit distance; chemistry is projected into separate modification claims; kind and orientation use equality; modifications and controlled roles use the implemented set/claim F1 rules. Optional exactness in 4.7.0 requires all enabled primitive components to equal one. Exact neutral-used sequence matches and the existing post-assignment neutral correspondence rule (floor .50) can also remove prediction families. Required assignment maximizes $s_{seq}+10^{-6}w$ for positive sequence evidence, with a $10^{-12}$ partial-assignment floor; there is no .25 Task 2 threshold. Credited scientific score is recomputed for each assigned family, and $F_2=H(\sum w_{fg},N_{out}^{eff},N_{req})$.

If $N_{req}=0$, the implementation gives one for no effective output and zero for any effective extra family. We retain those values in the locked table but exclude these protocols from **primary Task 2 macro-F1**, reporting their effective spurious-family counts separately. All-protocol T2 is a labeled secondary view. This is an estimand/denominator change, not a modification to the scorer or to the historical checkpoint selection. ddSEQ is the only zero-required protocol in the selected Figure 3/4 panels. Task 3 retains every original protocol.

Task 3 first matches whole workflows globally using $.45F_{terminal\ modalities}+.35F_{transition,all}+.20F_{state,all}$ and then recomputes internal state/transition alignments. State scientific similarity is $W((r,a,l,b),e;(.50,.15,.20,.15))$: reference-strand token similarity; mean architecture-enum/count-ratio/molecule-type agreement; soft F1 over globally aligned strands with ordered positional segment matching; and mean pairing/discontinuity descriptor similarity. Inner segment score is $.75s_{sequence}+.25I_{role}$; strand score is $.75F_{segments}+.15I_{molecule}+.10I_{orientation}$. Segment splitting can therefore reduce similarity even with unchanged bases. Eligible terminal matches allow a token-aware reverse-complement alternative.

State assignment uses unmasked scientific similarity plus boundary and event-context terms: $A_S=\max(0,(s_{S,unmasked}+.10B+.15C)/1.25-D)$, after hard compatibility gates; $D$ is .25 for incompatible incoming events, .15 for missing producers under the specified initial-state condition, otherwise zero. Transition scientific similarity is $.30o+.15u+.20v+.15d+.20q$. Operation agreement is exact except the extension/strand-synthesis alias; substrate/product terms are F1 after state-ID alignment; disposition averages carried/discarded F1; oligo agreement is a fresh transition-local sequence-family F1, not a reuse of global T2 assignments. Transition assignment is $A_\tau=\max(0,(s_\tau+.15I)/1.15-D_\tau)$, with operation/oligo identity bonus and .20 incompatible-operation or .15 handling mismatch penalties. These finite adjustments affect matching, not just exact ties.

Partial Hungarian matching applies the .25 floor to **adjusted $A_S$ and $A_\tau$**, both during workflow-pair evaluation and final internal assignment. It does not threshold raw scientific similarity. Residual exact ties follow deterministic serialization order. Effective predicted and scorable GT counts, and credited scientific score masses, are pooled across workflows before protocol-level transition/state F1. Typed substrate, carried-product and discarded-product edges are derived and compared through both entity maps; GT edge support follows the transition status, not both endpoint states; only predicted edges mapped exactly to neutral GT edges are removed from the effective denominator. Edge F1 is $H(m_E,N_{out}^{E,eff},N_{gt}^{E,score})$.

Protocols, not entities, are the macro-averaging unit. Unmatched outputs reduce precision and missing required entities reduce recall. Linked schema-invalid/missing outputs receive zero in the production wrapper; valid empty outputs follow the empty-set rules. Every selected Figure 3/4 output in this integration validates; this does not imply that historical failed/retried executions never occurred. State-alignment errors propagate into transition endpoint and edge scores; discrepancy categories are not independent biological failure mechanisms. Chemically valid graph refinements can remain penalized because one-to-one matching is not invariant to chain contraction. No granularity-invariant score or new expert adjudication was performed.

The execution reward remains $R=.30F_2+.70F_{transition}$. It is not the primary four-metric reporting composite. Historical C30 was selected under verifier 4.5.0 using an equal mean of four validation metrics (with validity/weakest-metric/reward/earliest-checkpoint tie-breaks); neither the new primary T2 denominator nor 4.7.0 rescoring was used to reselect it. Cost/performance plots use the equal mean of the four **reported macro-F1** values (informative T2 and full-panel T3), explicitly as a display composite.
'''
    corrections=r'''## Verifier corrections and reference amendments

Verifier 4.7.0 is opt-in; production 4.6.0 and its historical outputs remain unchanged. A predicted family placeholder can no longer receive exact credit for fixed GT A/C/G/T/U. Family construction preserves explicitly supplied sequence bases instead of replacing them because of component names, roles or placeholders. N/V ambiguity remains distinct from fixed bases under the existing family compatibility policy; this is not a full new IUPAC ambiguity calculus. Transition-local oligo matching uses the corrected sequence machinery as well.

Numerical neutralization is now decided by exactness of enabled primitive scientific components, not equality of their weighted floating-point sum to 1. This avoids tolerance-based acceptance of genuinely imperfect matches. In the saved SMART-seq combined-knowledge prediction, an exact neutral adapter-ligation transition had all five components equal to one. Multiplying the operation weight by .8 and renormalizing gave a weighted sum of .9999999999999998: 4.6.0 counted an extra transition (F1 .735920), while 4.7.0 retains neutralization (F1 .781915). The original-setting value is .787500. The prior validated 640-setting regression found 71 spurious neutralization failures in 4.6.0 and zero in 4.7.0. This protocol's references are unchanged here, so that exact regression remains applicable. The 4.7.0 tests include near-perfect non-exact controls; scientific weights, support masks, graph thresholds and canonical references were not changed by the verifier fix.

The camera-ready reference gate separately adopts already finalized VN/W1/barcode-range amendments. It verifies 96 reference files, identifies six differences from the earlier sensitivity freeze, and records every before/after hash and decision. Four older modality-alias files are semantically equivalent to promoted records but not byte-identical promotion artifacts. The gate distinguishes final scientific approval from proof of later installation authorization. See `reference_consistency.md`; these are existing approved records, not changes made by this analysis.
'''
    sensitivity_intro='''## Weight and matching-threshold sensitivity (W4)

Every selected prediction was evaluated with 640 full matching/scoring pipelines against the final reference set: original weights; a uniform vector; each component independently multiplied by 0.8 or 1.2 and renormalized; and 200 Dirichlet draws per scientific vector, concentration 100 centered at the original vector. Other vectors remain fixed. NumPy seeds are `SeedSequence([20261012, vector_index])`, with vector order T2, transition, state. The nine state/transition threshold combinations use 0.15, 0.25 and 0.40 at original weights, changing all four actual adjusted-assignment floor sites. Support masks, neutralization and unrelated constants are unchanged. Each scientific vector is perturbed independently; no optimal vector is selected.

There are 247 unique selected predictions (158,080 evaluations) and 285 main-panel memberships (182,400 config/membership rows). The authoritative table additionally contains original-setting Task 1 controls, Codex repeats, and the unchanged 100-output cost/discrepancy panel. All 247 original-setting scores and full details were independently reproduced with uncached 4.7.0 calls. Cached alternative evaluations use freshly computed score matrices, never frozen entity matches; the prior evaluator's independent uncached-validation receipts are retained as historical evidence, not claimed as new runs.

Paired percentile 95% intervals resample protocols 5,000 times with seed `SeedSequence([20261013, n])`, retaining pairing across systems/conditions. Kendall tau-b uses tied ranks (tolerance 1e-12); strict reversals are distinct from ties created/resolved. Rank-first frequencies count each tied leader, with fractional allocation also available. Weight samples represent alternative scoring assumptions, **not independent model runs**, so their distribution is not a confidence interval. CIs are pointwise and do not correct historical checkpoint selection or multiple comparisons.

Direct weight effects hold the original pairs, best-member/count choices and support masks fixed as a counterfactual; actual metrics always recompute the full pipeline. The residual is the effect of rematching, changing best members, family grouping and neutralization/counts. These two contributions sum to each score change and are reported separately in `sensitivity/aggregates.csv` and the authoritative table. Excluding zero-required protocols is separately labeled and is not assigned a paired CI against a differently sized all-protocol estimand.

'''
    sens_findings='''Across all 640 settings, Kimi remains the Fig. 3a informative T2 leader, Claude the state leader and GPT the edge leader. The transition leader changes: varying transition weights gives Gemini 179/200 Dirichlet first places and GPT 21/200; varying state weights gives Gemini 160/200 and GPT 40/200 through rematching. In Fig. 3b the primary T2, transition, state and edge leaders remain Gemini, GPT, Claude and GPT, respectively, although secondary ranks change. Combined knowledge remains the Fig. 4a informative T2 leader under every setting (combined minus baseline range +.021855 to +.129093); baseline leads all three Task 3 metrics. C30 remains the highest informative T2 checkpoint, but no checkpoint is reselected. C58−C0 informative T2 ranges from −.021430 to +.015488; the positive endpoint is the uniform-weight alternative. Both fixed C30 held-out conditions remain below C0 on every Task 3 metric across the sweep. This robustness concerns scoring assumptions conditional on the saved outputs, not biological equivalence or run-level statistical superiority.

'''
    sensitive=sens_findings+md(stability)
    nametext='''## Task 1 protocol-name-only control

The completed control contains one name-only API attempt for each of three models and 20 protocols. Only the canonical protocol name/version and generic reconstruction instructions were provided: no source bundle, tools, retrieval or prior conversation. Models were Claude Opus 5, GPT-5.6 Sol and Gemini 3.7 Flash, with requested high reasoning and a 64,000-token cap. The evidence-grounding instruction also differs from the PDF condition, and Gemini uses Vertex for name-only versus OpenRouter for PDF. Thus this is a parametric-knowledge control, not a direct contamination test or a clean causal estimate of PDF availability. Retired Gemini OpenRouter attempts are excluded under the recorded restart amendment; only the completed final 20-case Vertex run enters this comparison.

Both conditions use the same approved technical-structure projection, VN requirement and predefined inDrop length alternatives. Actual generated empty outputs are retained at zero; final selected technical failures are absent. Historical infrastructure retries remain in provenance. Paired protocol-bootstrap intervals use 100,000 draws; exact reconstruction means exact projected technical structure, not proven downstream biological usability. Name-only mean similarity is higher in all three completed comparisons, while exact-match counts remain low.

'''+md(t1[['group','n','valid','abstentions','mean','exact_count','exact_rate']])+ '\n\n'+md(t1pairs[['family','contrast','n','difference','ci_low','ci_high','api_or_pdf_wins','other_wins','ties','holm_p']])
    repeattext='''## Completed repeated-execution variability

Native GPT-5.6 Sol/Codex has three completed attempts on each of the 20 native protocols: the original selected attempt and two additional saved attempts, all 60 now rescored with 4.7.0 against the same final references. The supplied task prompt, Codex version 0.147.0 and requested max reasoning match. Originals ran on August 16 and repeats on October 10; runtime context such as dates/plugin lists differs. No controlled random seeds or synchronized replicate rounds were recorded. These data measure observed execution variability, not isolated sampling noise.

All attempts are retained equally; no best-of-three selection is used. The estimate is a protocol macro-average of the three-attempt protocol means. Protocol-cluster bootstrap CIs (100,000 draws, seed 20261011) retain all attempts together. The descriptive one-per-protocol range samples one of the three observed outputs independently for each protocol; it is neither a confidence nor a prediction interval for future model runs. Task 2 uses 19 informative protocols; Task 3 retains 20.

'''+md(rep[rep.policy=='primary'][['metric','n_protocols','original_mean','mean_three_attempts','ci_low','ci_high','n3_minus_original','delta_ci_low','delta_ci_high','rms_within_protocol_sd','protocols_range_gt_point1']])+'''

This completed experiment does not establish variability for Claude, Gemini, Kimi, the external-knowledge conditions, or learning C0/C30. In particular, no matched repeated C0-versus-C30 learning experiment is available in the frozen selections. The external-knowledge validation panel (sci-ATAC-seq, scRRBS, SMART-seq, SHARE-seq, ddSEQ) and the learning validation panel (CEL-seq, chATAC-seq, ddSEQ, HyDrop-RNA, scRRBS) are different; their baseline values must not be presented as replicate estimates. Results for learning remain descriptive conditional on one trajectory, one output per checkpoint/protocol and the preserved historical C30 selection.
'''
    supplement='# Proposed camera-ready supplementary sections\n\nDraft for author review; generated from verified locked data. No manuscript file was edited.\n\n'+defs+'\n'+corrections+'\n'+sensitivity_intro+sensitive+'\n\n'+nametext+'\n\n'+repeattext
    supplement+='\n\n## Source map\n\n'+', '.join(source_link(n) for n in ['score_t2','score_t3','_family_tokens_match','_family_template_sequence','_dimensions_are_exact','_state_assignment_similarity','_transition_assignment_similarity','_workflow_assignment_similarity'])+'. Full component/edge/support specifications and limitations are in the historical `../verifier_audit.md`, with the four corrections in `../verifier_corrected_v4_7_0/revision_report.md` taking precedence. Table/formula version is 4.7.0; Task 1 retains its separately pinned legacy scorer.\n'
    (OUT/'supplement_sections.md').write_text(supplement)
    # LaTeX-ready concise sections; detailed executable specification remains in
    # the Markdown supplement/source map instead of inventing unavailable data.
    tex=r'''% Proposed additions only; review before inserting into main.tex.
\section{Verifier version, metric definitions, and reference lock}
\label{app:verifier-camera-ready}
All camera-ready linked-task scores use verifier 4.7.0 with the finalized
human-approved reference set \texttt{REFSET}. The original trial selections,
panel membership, and historical $C_{30}$ are fixed. The reference audit found
six changed task files across 10x 3' Feature Barcoding and inDrop v1 relative
to the earlier sensitivity freeze; finalized approval and before/after hashes
are recorded. Four additional files differ from old promotion records only
in canonical modality aliases. No reference or prediction was edited.

Let $H(a,p,g)=2a/(p+g)$ for $p+g>0$, and $H(0,0,0)=1$. Supported dimensions
are renormalized: $W(d,e;w)=\sum_k e_kw_kd_k/\sum_k e_kw_k$ (zero with no
enabled component). For Task 2, $N_{\rm req}$ counts workflow-linked GT
oligo records with source-recoverable sequence claims. If $F$ is the predicted
family set and $Z$ its neutralized subset, $N_{\rm out}^{\rm eff}=|F\setminus Z|$.
Optional or non-recoverable GT records define correspondence rules; they are
not directly subtracted from a prediction count. Primary Task 2 macro-F1
averages only protocols with $N_{\rm req}>0$. We separately report effective
spurious-family counts on zero-required protocols. Task 3 panels are unchanged.
Task 2 assignment is sequence-first with a $10^{-6}$ scientific-score term
and a $10^{-12}$ positive-evidence floor; the scientific numerator uses the
five original component weights $(.65,.15,.10,.05,.05)$ for sequence,
modifications, kind, orientation and role. State scientific weights are
$(.50,.15,.20,.15)$ for reference strand, architecture, segments and pairing;
transition weights are $(.30,.15,.20,.15,.20)$ for operation, substrates,
products, disposition and oligos. The state and transition floors of .25
apply to adjusted assignment scores, including the existing context/identity
bonuses and incompatibility penalties, rather than raw scientific similarity.
Score masses and effective entity counts are pooled across workflows before
protocol F1, followed by protocol macro-averaging. State alignment affects
transition endpoint and typed-edge agreement, so discrepancy categories
are not independent. One-to-one alignment is not invariant to chemically
valid workflow refinements; no chain-contraction metric was implemented.

\paragraph{Bounded verifier corrections.}
Predicted placeholders no longer obtain exact credit for fixed GT A/C/G/T/U;
explicit bases are preserved during family construction, while N/V ambiguity
retains the existing compatibility policy. Exact neutralization now requires
exact primitive scientific components rather than a weighted floating-point
sum equal to one. This removes the saved SMART-seq numerical discontinuity
without neutralizing genuinely imperfect matches. Scientific component
weights, support masks and unrelated scoring constants remain unchanged.
Execution reward remains $.30F_2+.70F_{\rm transition}$.
Historical $C_{30}$ was selected under 4.5.0 using an equal mean of four
validation metrics, with recorded tie-breaks; it was not reselected after
rescoring or changing the Task 2 reporting denominator.

\section{Metric-weight and assignment-threshold sensitivity}
\label{app:metric-sensitivity}
We recomputed the complete matching/scoring pipeline for 247 unique saved
predictions under 640 settings: original weights, uniform weights,
individual $\pm20\%$ renormalized component perturbations, and 200 Dirichlet
draws per scientific vector (concentration 100 centered at its original
weights), plus all nine combinations of state/transition assignment floors
in $\{.15,.25,.40\}$ at original weights. Other weights/constants and support
masks were fixed. Seeds were \texttt{SeedSequence([20261012, i])} for vector
indices $i=0,1,2$. Every original-setting score and full alignment detail
was independently reproduced without feature caching.
Paired percentile 95\% intervals resample protocols 5,000 times; scoring
assumptions are not independent model runs. We report Kendall's $\tau_b$,
strict pairwise reversals and tied-first frequencies. Fixed-match
counterfactuals separate direct weight effects from changes in assignment,
best members, grouping and effective counts. Pointwise intervals do not
adjust for multiple comparisons or historical checkpoint selection.

\section{Task 1 protocol-name-only control}
\label{app:task1-name-only}
All 60 selected name-only API attempts completed: 20 each for Claude Opus 5,
GPT-5.6 Sol and Gemini 3.7 Flash. Requests supplied canonical name/version
and generic instructions, without documents, retrieval, tools or history.
High reasoning and a 64,000-token cap were requested. Both conditions use
the unchanged Task 1 scorer and identical approved reference projections,
including predefined inDrop barcode-length alternatives. Empty generated
outputs score zero. The PDF grounding instruction differs; Gemini uses
Vertex for name-only and OpenRouter for PDF. Thus this is a parametric
knowledge control, not proof of pretraining contamination.
TASK1TEXT

\section{Completed repeated-execution variability}
\label{app:completed-repeats}
We rescored all three saved native Codex attempts per protocol against the
same final references (60 valid outputs; 20 protocols). CLI/model/reasoning
settings and supplied task prompts match, but original and repeat executions
occurred on August 16 and October 10 with differing runtime context.
These are not controlled random seeds or synchronized replicate rounds.
Protocol means retain all three attempts equally, without best-of-three
selection. We bootstrap protocol clusters 100,000 times, preserving all
attempts within each sampled protocol; Task 2 includes 19 informative
protocols and Task 3 all 20. REPEATTEXT
The two five-protocol validation panels are different: the external-knowledge
panel contains sci-ATAC-seq, scRRBS, SMART-seq, SHARE-seq and ddSEQ, whereas
learning validation contains CEL-seq, chATAC-seq, ddSEQ, HyDrop-RNA and scRRBS.
Their baselines are not replicate measurements. No completed matched
repeated C0/C30 learning comparison is available; learning conclusions
remain conditional on one trajectory and historical checkpoint selection.
'''.replace('REFSET',tex_escape(lock['reference_set_id']))
    pairnames=t1pairs[t1pairs.contrast=='name_only_minus_pdf']
    tasktext='Name-only minus PDF mean similarities were '+', '.join(f"{r.family} {r.difference:+.3f} (95\\% CI [{r.ci_low:+.3f}, {r.ci_high:+.3f}])" for r in pairnames.itertuples())+'. Exact name-only reconstruction counts were 3, 2 and 3 of 20, respectively; PDF counts were 5, 4 and 4.'
    reptext='The three-attempt mean F1 values (Task 2, transition, state, edge) were '+', '.join(f"{rep[(rep.metric==m)&(rep.policy=='primary')].mean_three_attempts.iloc[0]:.3f}" for m in METRICS)+'.'
    tex=tex.replace('TASK1TEXT',tasktext).replace('REPEATTEXT',reptext)
    tex=tex.replace(r'\section{Task 1 protocol-name-only control}',r'''At all 640 settings, the Fig.~3a informative Task~2, state and edge leaders
+remain Kimi, Claude and GPT. The transition leader changes: under 200
+transition-weight Dirichlet draws Gemini ranks first 179 times and GPT 21;
+under state-weight draws the corresponding counts are 160 and 40. Fig.~3b
+metric leaders remain Gemini (Task~2), GPT (transition/edge) and Claude
+(state), while secondary rankings vary. Combined knowledge remains the
+Fig.~4a informative Task~2 leader (combined minus baseline range
+$+.021855$ to $+.129093$); baseline leads all Task~3 metrics. The learning
+$C_{58}-C_0$ informative Task~2 difference ranges from $-.021430$ to
+$+.015488$, changing sign under uniform weights. Both historical C30
+held-out conditions remain below C0 on all Task~3 metrics throughout the
+sweep. These are robustness statements conditional on the saved outputs,
+not probabilities of winning independent model runs.
+
+\section{Task 1 protocol-name-only control}'''.replace('\n+','\n'))
    (OUT/'supplement_sections.tex').write_text(tex)
    # Scope all manuscript edits to the pinned current snapshot and locate
    # literal text anchors, so line numbers fail visibly if the paper changes.
    paper=Path('external/manuscript/main.tex');assert sha(paper)==lock['main_tex_sha256'];lines=paper.read_text().splitlines();edits=[]
    def add(cid,start,end,reason,replacement,refs):
        st=next(i for i,l in enumerate(lines,1) if start in l);en=next(i for i,l in enumerate(lines[st-1:],st) if end in l)
        edits.append(dict(change_id=cid,start_line=st,end_line=en,old_text='\n'.join(lines[st-1:en]),reason=reason,proposed_replacement=replacement,source_tables=refs))
    add('M01','show that frontier agents are often approximately correct but remain','accumulation of rules and cross-protocol examples produced reliable gains.', 'Scope parsing/model/harness and intervention conclusions to completed evidence.',r'show that frontier agents are often approximately correct but remain unreliable at exact reconstruction. Molecular-state and connectivity discrepancies remained common. Text-layer nucleotide extraction was reliable in the tested parser control. Model and harness effects were not independently decomposed. External knowledge improved informative-protocol oligo recovery in the combined condition, while the tested interventions showed lower Task~3 point estimates than their respective baselines.', 'aggregates.csv: fig4a/fig4d; repeated_run_summary.csv; no completed model-versus-harness variance decomposition')
    add('M02','Our experiments reveal that frontier LLMs often reconstruct final-library','and internally consistent molecular representation.', 'Avoid broad parsing, vendor-family and no-gain claims.',r'Frontier agents often reconstruct final-library sequences approximately correctly but are not reliably exact. The tested text-layer PDF extraction control was reliable. No native system dominated all metrics. Clustering alone did not establish whether the model or harness explains more performance variance. Informative-protocol oligo recovery improved in the combined-knowledge pilot and with C30 knowledge-base supplementation, whereas Task~3 point estimates remained below baseline in those completed comparisons.', 'aggregates.csv: fig3a,fig4a,fig4d; sensitivity_summary.csv; no new causal/variance experiment')
    add('M03','indicates the number of output families counted toward scoring, not including','\\(N_{\\mathrm{req}}\\) and reduce recall.', 'Define prediction-side effective count; explain zero-required exclusion and support renormalization.',r'counts the predicted family set $F$ after removal of its neutralized subset $Z$: $N_{\mathrm{out}}^{\mathrm{eff}}=|F\setminus Z|$. Neutralization depends on correspondence to optional or non-recoverable reference oligos; it is not subtraction of reference counts. Equation~\eqref{eq:task2-sim} is renormalized over source-supported components. Primary Task~2 macro-F1 includes only protocols with $N_{\mathrm{req}}>0$; effective spurious-family counts for zero-required protocols are reported separately. The implementation retains its empty-set convention (one for no effective outputs, zero for any extras when $N_{\mathrm{req}}=0$).', 'supplement_sections.md Metric definitions; reference_consistency.csv; zero_required.csv')
    add('M04','partial one-to-one matching; candidate pairs with similarity below','partial one-to-one matching; candidate pairs with similarity below','The threshold acts on adjusted assignment scores.',r'partial one-to-one matching. The 0.25 eligibility floor applies to the adjusted assignment scores, including context/identity bonuses and compatibility penalties, not directly to the scientific similarities in the score numerator. All matching stages are recomputed in the sensitivity analysis.', 'supplement_sections.md Metric definitions; sensitivity/configurations.csv (or sensitivity/manifest.json configurations)')
    add('M05','Each connected workflow is scored once, entity counts are aggregated within','benchmark.', 'Add version/ref lock, denominator distinction and alignment dependence.',r'Each connected workflow is scored once; score masses and effective counts are pooled within protocol before computing F1. Protocol scores are then macro-averaged, with zero-required protocols excluded only from primary Task~2 F1. Camera-ready results use verifier 4.7.0 and the version-locked approved references, preserving historical trial selection and C30. State-alignment errors also affect transition endpoints and typed edges; these metrics and discrepancy categories are not independent.', 'results_identity.json; aggregates.csv; panel_membership.csv; reference_consistency.csv')
    native=a[(a.panel=='fig3a')&(a.policy=='primary')];ranges={m:(native[native.metric==m]['mean'].min(),native[native.metric==m]['mean'].max()) for m in METRICS}
    add('M06','ranged from 0.726 to 0.770. For Task~3, molecular-transition F1','typed-edge F1 from 0.721 to 0.754', 'Final approved reference changes and corrected informative T2 values.',f"ranged from {ranges[METRICS[0]][0]:.3f} to {ranges[METRICS[0]][1]:.3f} over 19 informative protocols. Task~3 retains all 20 protocols: transition F1 ranged from {ranges[METRICS[1]][0]:.3f} to {ranges[METRICS[1]][1]:.3f}, state F1 from {ranges[METRICS[2]][0]:.3f} to {ranges[METRICS[2]][1]:.3f}, and typed-edge F1 from {ranges[METRICS[3]][0]:.3f} to {ranges[METRICS[3]][1]:.3f} (Figure~\\ref{{fig:baseline-native}}).",source('fig3a'))
    add('M07','No single system dominated every metric: Kimi Code obtained the highest','connecting workflow nodes.', 'Leaders at default weights remain, but transition leader is weight-sensitive.',r'At the prespecified weights, Kimi Code had the highest informative Task~2 mean, Antigravity CLI the highest transition mean, Claude Code the highest state mean, and Codex the highest typed-edge mean. These are point-estimate rankings; sensitivity analysis changes the transition leader under some alternative weights and does not establish run-level superiority. Molecular-state F1 has the largest observed between-system range on this panel.',source('fig3a')+'; sensitivity_rank_first.csv: panel=fig3a')
    mn=a[(a.panel=='fig3b')&(a.policy=='primary')&(a.metric==METRICS[1])]['mean']
    add('M08','0.714 to 0.805 across the nine model--harness systems','the larger influence on protocol-level reconstruction behavior.', 'Correct numerical range and avoid causal variance attribution from clustering.',f"{mn.min():.3f} to {mn.max():.3f} across the nine systems (Figure~\\ref{{fig:baseline-transition}}). Task~2 complementary means use nine informative protocols, while Task~3 retains all ten. Historical estimated cost was not monotonically related to corrected performance. Hierarchical clustering is descriptive and does not establish that model effects exceed harness effects; the completed experiments do not include an independent two-way variance decomposition.",source('fig3b')+'; cost_performance.csv; heatmap_order.json')
    add('M09','typed-edge F1 for native CLI systems.','typed-edge F1 for native CLI systems.', 'Figure 3a denominator/interval caption.',r'typed-edge F1 for native CLI systems under verifier 4.7.0. Task~2 averages 19 informative protocols; Task~3 averages all 20. Intervals are pointwise 95\% protocol-bootstrap intervals, not repeated-model-run uncertainty.',source('fig3a'))
    er=b[b.panel=='fig3common10'];catcounts={c:int(er[c].sum()) for c in er if c.startswith('discrepancy_')}
    errdesc=', '.join(f"{catcounts['discrepancy_'+cat]}/100 {label}" for cat,label in [('molecular_state_or_assembly_error','state/assembly'),('workflow_or_topology_error','workflow/topology'),('missing_recoverable_information','missing-recoverable'),('unsupported_completion','unsupported-addition'),('operation_error','operation'),('strand_or_orientation_error','strand/orientation')])
    add('M10','(Table~\\ref{tab:baseline-error-terms}). Workflow-topology','(Figure~\\ref{fig:supp-baseline-errors-protocol}).','Recompute discrepancy counts from new alignments and qualify attribution/dependence.',r'(Table~\ref{tab:baseline-error-terms}). Under verifier 4.7.0, deterministic discrepancy counts were '+errdesc+r'. These are overlapping, unadjudicated output differences rather than independent confirmed agent errors. State-alignment failures can also cause transition and edge mismatches. The profiles do not identify causal model-versus-harness effects.', 'error_profiles.csv; results.csv panel=fig3common10, discrepancy_*; error_observations/')
    add('M11','\\paragraph{External knowledge did not improve performance in this pilot.}','\\paragraph{External knowledge did not improve performance in this pilot.}', 'Combined condition improves primary informative Task 2.',r'\paragraph{External knowledge improved oligo recovery in the combined condition, but not Task~3 point estimates.}',source('fig4a'))
    paired=contrasts[(contrasts.panel=='fig4a')&(contrasts.group_a=='baseline')&(contrasts.group_b=='general_methods_plus_memory_v1')&(contrasts.metric==METRICS[0])].iloc[0]
    add('M12','The baseline achieved mean oligo-family, molecular-transition,','ineffective.', 'All affected Figure 4a values and the no-condition-exceeds-baseline claim.',f"The external-knowledge panel contains sci-ATAC-seq, scRRBS, SMART-seq, SHARE-seq and ddSEQ. Baseline Task~2/transition/state/edge means were {fmt('fig4a','baseline')}; Task~2 uses four informative protocols and Task~3 all five. Memory-only means were {fmt('fig4a','cross_protocol_memory_v1')}, combined-knowledge means {fmt('fig4a','general_methods_plus_memory_v1')}, and general-only means {fmt('fig4a','general_methods_v1')}. Combined knowledge exceeded baseline on informative Task~2 by {paired.b_minus_a:.3f} (paired protocol-bootstrap 95\\% CI [{paired.ci_low:.3f}, {paired.ci_high:.3f}]); its Task~3 point estimates remained lower. This is a single-output pilot, not evidence that external knowledge is generally ineffective.",source('fig4a')+'; paired_contrasts.csv: fig4a combined-minus-baseline T2')
    add('M13','on the same five validation protocols without using validation outputs for','learning (Figure~\\ref{fig:autonomous-training}).', 'Explicitly distinguish learning validation from the knowledge pilot.',r'on a fixed learning-validation panel (CEL-seq, chATAC-seq, ddSEQ, HyDrop-RNA and scRRBS), without using its outputs for learning. This panel differs from the external-knowledge pilot; its baseline is not a repeat of Figure~\ref{fig:knowledge-pilot}.', 'panel_membership.csv: fig4a versus fig4c')
    add('M14','general-knowledge, cross-protocol memory, and combined conditions.','knowledge base (KB) supplement.%', 'Figure 4 caption denominators, historical selection and bootstrap interpretation.',r'general-knowledge, cross-protocol memory and combined conditions (Task~2: four informative protocols; Task~3: all five). \textbf{(b)}~Autonomous cumulative-improvement framework. \textbf{(c)}~Learning-validation means across checkpoints (Task~2: four informative protocols; Task~3: all five), shown in the original line-and-marker style. Pointwise protocol-bootstrap 95\% intervals remain available in the supplementary tables. \textbf{(d)}~Held-out means on the same ten protocols for all metrics, comparing baseline with the historically selected C30 rules, with and without the knowledge base. C30 was retained from its original 4.5.0 selection; it was not reselected using these 4.7.0 scores. Intervals quantify protocol variation conditional on the saved runs.',source('fig4a')+'; '+source('fig4c')+'; '+source('fig4d'))
    add('M15','Validation performance was non-monotonic across checkpoints. Using the aggregate','in held-out molecular-workflow reconstruction.', 'Separate fixed historical selection, primary metric change, and conditional single-run conclusions.',f"Validation performance was non-monotonic. We retain C30, historically selected under verifier 4.5.0 with the equal-four-metric criterion. Under 4.7.0, C0, C30, C40 and C58 Task~2/transition/state/edge means are {fmt('fig4c','C0')}; {fmt('fig4c','C30')}; {fmt('fig4c','C40')}; and {fmt('fig4c','C58')}, respectively. C58 is below C0 on all four default-weight means, but its informative Task~2 difference changes sign under uniform T2 weights. On the ten held-out protocols, baseline, C30 rules and C30+KB means are {fmt('fig4d','c0')}; {fmt('fig4d','c30_pact')}; and {fmt('fig4d','c30_kb')}. Both C30 conditions have lower Task~3 point estimates than baseline across the tested scoring settings. These completed outputs support a descriptive comparison; no matched repeated C0/C30 experiment or granularity-invariant evaluation establishes a general absence of molecular-understanding gains.",source('fig4c')+'; '+source('fig4d')+'; sensitivity/paired_contrasts.csv')
    add('M16','Our results suggest that the main limitation for agent to extract the','representing the physical molecules before and after each operation.', 'Narrow biological-bottleneck and stochastic-error interpretation.',r'The completed evaluations reveal persistent discrepancies in molecular-state representation and workflow connectivity. Reliable extraction of the tested text-layer nucleotide strings does not establish reliability for figure-only evidence. The name-only control often increases Task~1 mean similarity, but it does not establish memorization or explain the biological correctness of Task~3 outputs. Three saved native Codex attempts demonstrate observed execution variability; other system and learning comparisons remain single-output observations.', 'task1_paired_contrasts.csv; repeated_run_summary.csv; error_profiles.csv; unchanged parser control')
    add('M17','\\paragraph{Additional knowledge and accumulated experience did not yield reliable gains.}','including the oligo,', 'Avoid unsupported general no-gain claim and obsolete ddSEQ C40 mechanism; retain future-work text separately.',r'\paragraph{Intervention effects depend on task and representation.} Combined external knowledge improves informative-protocol Task~2 in the pilot, and C30 knowledge-base supplementation improves held-out Task~2 point estimates. The tested interventions do not exceed baseline Task~3 point estimates under the examined weights/thresholds. The historical ddSEQ C40 Task~2 collapse is removed by corrected family construction: both C30 and C40 have zero effective spurious families, and ddSEQ is excluded from primary Task~2 macro-F1. Its workflow mismatch remains. Finer chemically plausible graphs can be penalized by canonical one-to-one matching, so learning conclusions are conditional on that representation. Controlled repeats, independent biological adjudication and refinement-aware scoring remain future work.', 'zero_required.csv: fig4c C30/C40; aggregates.csv; sensitivity/paired_contrasts.csv; representation limitation in supplement_sections.md')
    add('M18','Both panels use the ten protocols common to all evaluated systems.','summarized as the mean of the four benchmark F1 metrics.%', 'New cost figure geometry and informative T2 display-composite definition.',r'Both panels use the same ten-system, ten-protocol historical cost panel. \textbf{(a)}~Bars show mean estimated API-equivalent cost per protocol; intervals are protocol-bootstrap 95\% intervals at the recorded historical pricing snapshot. \textbf{(b)}~Estimated cost versus the equal mean of informative Task~2 macro-F1 (nine protocols) and the three full-panel Task~3 macro-F1 values (ten protocols each). This display composite is neither execution reward nor the historical checkpoint-selection criterion.', 'cost_performance.csv; results.csv panel=fig3common10; figure_manifest.json')
    add('M19','  Heatmaps show performance across the ten protocols and nine','metrics. All color scales range from 0 to 1.%','Restore original heatmap design and independent metric clustering; omit the zero-required Task 2 column.',r'Heatmaps show the nine model--harness systems used in Figure~\ref{fig:baseline-transition}. Task~2 displays nine informative protocols, omitting ddSEQ because it has no required recoverable families; its effective spurious-family counts are reported separately. Task~3 state and typed-edge heatmaps retain all ten protocols. Each metric clusters systems independently using average linkage on one minus Pearson correlation of the displayed protocol-score vectors; protocol order is fixed to the original display order. These supplementary color scales range from 0 to 1.', 'results.csv panel=fig3b; zero_required.csv; heatmap_order.json; fig3b_plot_data.csv')
    for cid,cp,start,end in [('M20','C0','Task~2 F1: 0.807.','0.846, 0.625, and 0.822.'),('M21','C30','Highest Task~2 F1 (0.857),','Best overall balance among checkpoints.'),('M22','C40','Task~2 F1 decreased to 0.589;','0.743, 0.497, and 0.662.'),('M23','C58','State F1 partially recovered to 0.539,','remained 0.596, 0.720, and 0.638.')]:
        v=values('fig4c',cp);add(cid,start,end,'Table checkpoint values must use informative T2 and the fixed historical selection.',f"Informative Task~2 F1: {v[0]:.3f}; Task~3 transition, state and edge F1: {v[1]:.3f}, {v[2]:.3f}, {v[3]:.3f}."+(' C30 remains the historical selection, without reselection.' if cp=='C30' else '')+r' \\',source('fig4c',cp))
    add('M24','edge F1 from 1.000 to 0.364. Its Task~2 F1 also changed','Task~2 families.', 'Obsolete ddSEQ explanation under corrected verifier; T3 deterioration remains.',r'edge F1 decreased from 1.000 to 0.364. The historical 4.6.0 Task~2 collapse at C40 was caused by family grouping interacting with the zero-required convention; under 4.7.0 both C30 and C40 have zero effective spurious families. ddSEQ is therefore reported through spurious-family counts and is excluded from primary Task~2 macro-F1. The saved outputs and their historical scores remain unchanged.', 'zero_required.csv; results.csv: fig4c:C30/C40:ddseq_single_cell_3_rna_seq_kit; ../verifier_corrected_v4_7_0/revision_report.md')
    add('M25','These cases illustrate negative transfer: rules that were','scope.', 'Causal/biological interpretation is limited by single trajectories and canonical granularity.',r'from 0.609 to 0.526. These cases show where the saved predictions diverge from the canonical graph and support a conditional behavioral interpretation. They do not isolate the causal effect of a rule from execution variability, nor distinguish all biologically valid refinements from errors.', 'results.csv fig4c; repeated_run_summary.csv (different experiment); supplement_sections.md limitations')
    add('M26','compiled Task~2 and Task~3 from a linked work record. Graphs became more complete, averaging 10.0 states,','7.2 transitions, and 16.0 edges.','Distinguish raw graph size from effective scored edge count.',r'compiled Task~2 and Task~3 from a linked work record. The predicted graphs averaged 10.0 states, 7.2 transitions and 16.2 raw typed edges (16.0 effective scored edges after neutralization). &','results.csv: panel=fig4c, group=C30, raw_*_count and predicted_typed_edge_count')
    add('M27','    Each point represents one sequencing protocol completed','    and exact reconstruction counts for each interface.', 'Match the regenerated Task 1 plot and retain empty generated outputs in the denominator.',r'Each point represents one of 20 selected completed protocol attempts for both interfaces; empty generated outputs are retained with zero similarity. Plot annotations show the mean paired similarity difference (agent minus API) and its paired protocol-bootstrap 95\% interval. The inset table retains mean sequence similarity and exact reconstruction counts. The main text reports the paired Wilcoxon tests with Holm adjustment.', 'task1_paired_contrasts.csv; task1_summary.csv; completion_counts.csv panel=task1; figures/fig2_task1_api_vs_coding_agent.pdf')
    add('M28','Values range from 0 to 1, where 1 indicates this error appeared','or LLM-harness runs.%','Clarify dependent, unadjudicated discrepancy categories in the supplementary caption.',r'Values range from 0 to 1; one means a deterministic discrepancy occurred in every selected protocol or system for that row. Categories overlap and depend on entity alignment. These output differences were not independently adjudicated as biological errors.', 'error_profiles.csv; error_observations/; supplement_sections.md')
    add('M29','    \\textbf{(b)}~Task 3 molecular-transition F1 across ten common protocols','    and nine model--harness systems, clustered by protocol-level performance.', 'State the restored original heatmap clustering and displayed color limits.',r'\textbf{(b)}~Task~3 molecular-transition F1 across ten common protocols and nine model--harness systems. Systems are clustered by average linkage on one minus Pearson correlation of their protocol-score vectors; protocol order follows the original display. The original color scale spans 0.38--1.00.', 'results.csv panel=fig3b; heatmap_order.json; fig3b_fig4_original_style.R')
    # Keep target spans narrow: the discussion M17 removes only the obsolete
    # subsection, not unrelated future-work proposals.
    m17=next(r for r in edits if r['change_id']=='M17');m17['end_line']=next(i for i,l in enumerate(lines,1) if 'degrade earlier performance.' in l);m17['old_text']='\n'.join(lines[m17['start_line']-1:m17['end_line']])
    records('manuscript_changes.csv',edits)
    inventory=[]
    skip=re.compile(r'(^\s*%|\\(vspace|hspace|includegraphics|begin\{minipage|begin\{subfigure|setlength|renewcommand|small|author)|textwidth|linewidth|[0-9]+pt|[0-9]+em)')
    for ln,line in enumerate(lines,1):
        if ln<52 or not re.search(r'\d',line) or skip.search(line):continue
        related=[e['change_id'] for e in edits if e['start_line']<=ln<=e['end_line']]
        status='replace per change record' if related else 'unchanged by this integration; source/version statement may need nearby qualification'
        if 350<=ln<=450:status='Task 1 values independently reproduced; parser-control values unchanged (not rerun)'
        inventory.append(dict(line=ln,text=line,status=status,change_ids=';'.join(related)))
    records('manuscript_numeric_inventory.csv',inventory)
    report='# Manuscript consistency report\n\n**Proposals for review only. `main.tex` was not edited.**\n\nSnapshot: `'+str(paper)+'`; SHA-256 `'+lock['main_tex_sha256']+'`. Line numbers below apply only to that snapshot. `manuscript_changes.csv` contains exact old spans, replacements and source-table selectors; `manuscript_numeric_inventory.csv` inventories numeric source lines, including unchanged equations and controls.\n\nPrimary Task 2 means are informative-protocol means; Task 3 panel membership is unchanged. Default-weight point estimates and sensitivity statements are distinct. No model/harness decomposition, new learning replicate experiment, biological-equivalence adjudication, or chain-contraction experiment is implied.\n\n'
    for e in edits:report+=f"## {e['change_id']} · lines {e['start_line']}–{e['end_line']}\n\n{e['reason']}\n\nProposed replacement:\n\n```tex\n{e['proposed_replacement']}\n```\n\nSource: `{e['source_tables']}`.\n\n"
    report+='''## Additions and unchanged numerical statements

- Insert the prepared `supplement_sections.tex` (or the fuller Markdown sections) after author review. Add main-text references to the sensitivity, name-only and repeated-execution sections. Insert the provided sensitivity/repeat/control/zero-required figures using the source table manifest; no missing experiment is represented by a placeholder result.
- Task 1 API/agent means, exact counts, differences, win/tie counts and Holm-adjusted tests were independently reproduced and remain unchanged. Native Task 1 has actual empty outputs; the caption should say completed selected attempts, not valid sequences only. Task 1 uses its unchanged legacy scorer, not a newly invented 4.7.0 Task 1 metric.
- Equations 1–6 retain their scientific weights; accompanying definitions need the support/neutralization/threshold qualifications above. The Eq. 3 matched-pair numerator is over required targets. The typed-edge formula remains conditioned on both entity alignments.
- Native leaders at original weights remain Kimi/T2, Gemini/transition, Claude/state and GPT/edge. Their status is point-estimate ranking; the transition leader can change with alternative weights. The informative Task 2 Fig. 3b leader is Gemini/Antigravity; tiny gaps below first place are not evidence of robust superiority.
- The historical C30 selection, 58/5/10 split, 73 usable protocols, 12 post-training candidate checkpoints, and rule/exemplar counts are not reselected/redefined; graph-size terminology is clarified in M26. C30 raw state/transition/edge means can be verified in `results.csv` (`raw_*_count`); source evidence for learned-rule counts remains the historical checkpoint artifacts.
- HyDrop-RNA C30→C40 and scRRBS C40→C58 numerical Task 3 examples remain unchanged under these references; their causal/biological interpretation needs the stated granularity and single-trajectory caveats. ddSEQ T3 examples also remain, while its historical Task 2 collapse cannot be described as a current corrected-metric result.
- Audit finding counts, the 78.3% legacy-curation fraction, the parser experiment's 80/80 and 71/80 counts and .982 similarity, literature references, funding/author details and source-license claims are outside this rescoring. They were not re-audited or updated; do not infer additional supporting experiments. The current narrow text-layer PDF conclusion is retained.
- Standalone figures were regenerated as vector PDF/SVG plus PNG. Final manuscript typesetting must be reviewed after insertion; no TeX compiler is installed here, and the manuscript was not edited or compiled. The dense heatmaps should be placed at a readable width.
- Figure 4b is a schematic, and audit/parser figures have no corrected verifier measurements: retain them. All affected data figures, including cost/performance and discrepancy heatmaps, have new copies in `figures/`; historical figures are untouched. Error plots remain **unadjudicated deterministic discrepancies**, not confirmed biological causes.
'''
    (OUT/'manuscript_consistency_report.md').write_text(report)
    summary='# Camera-ready results integration · verifier 4.7.0\n\n**Final data generated and checked; manuscript edits remain proposals.**\n\nReference set `'+lock['reference_set_id']+'`. All historical data, predictions, ground truth, production 4.6.0 and C30 are preserved.\n\n## Reference reconciliation\n\nSix approved reference-file changes across two protocols affect default scores for 14 unique selected predictions (10 inDrop v1 and four 10x Feature Barcoding). Four further files differ from promotion bytes only in verified modality aliases. The scoped reference gate found no unresolved scientific difference; installation-authorization limits are documented in `reference_consistency.md`.\n\n## Authoritative table\n\n`results.csv` is the only authoritative score observation table: 625 original-setting membership rows and 182,115 alternative-setting rows. Alternative rows inherit provenance from `baseline_row_id`; they are scoring assumptions, not model attempts. `results_identity.json` locks its hash. All aggregates and main/supplement data figures derive from that table; `sensitivity/` contains derived sensitivity views.\n\n## Primary results\n\n'+md(primary)+'\n\n## Conclusions that change\n\n'
    summary+=f'''- Figure 3a informative T2 range becomes {ranges[METRICS[0]][0]:.3f}–{ranges[METRICS[0]][1]:.3f}; default metric leaders remain Kimi/Gemini/Claude/GPT. Figure 3b informative T2 is led by Gemini/Antigravity, with close secondary rankings.
- The combined-knowledge condition **does exceed baseline on informative Task 2**, {values('fig4a','general_methods_plus_memory_v1')[0]:.3f} versus {values('fig4a','baseline')[0]:.3f}; paired difference {paired.b_minus_a:+.3f} [{paired.ci_low:+.3f}, {paired.ci_high:+.3f}]. The blanket “none exceeded baseline on any metric” claim must change.
- The corrected C40 all-protocol T2 increase is a family-grouping/zero-required interaction; primary informative T2 excludes ddSEQ. Its historical Task 2 collapse is no longer a current scoring result. Learning remains non-monotonic; C58−C0 informative T2 can change sign under uniform weights.
- Task 3 intervention point estimates remain below baseline across the tested configurations, but graph-granularity penalties and missing matched C0/C30 repeats limit biological/no-gain claims.
- Name-only Task 1 mean similarity exceeds PDF in all three completed comparisons, with low exact counts and route/instruction confounding. This is not proof of contamination.
- Three native Codex attempts quantify observed execution variability; no completed repeats are available here for the learning comparison or other systems. Clustering does not establish that model variance exceeds harness variance.

## Completion counts

'''+md(counts[['panel','group','selected_completed_attempts','valid_outputs','invalid_or_empty_outputs','abstentions','recorded_exceptions']])+'\n\n## Sensitivity summary\n\n'+md(stability)+'\n\n## Files for review\n\n- `reference_consistency.md`: approval/hash gate and remaining representation-only differences.\n- `aggregates.csv`, `paired_contrasts.csv`, `score_changes.csv`, `ranking_changes.csv`, `zero_required.csv`: final numbers and reference-versus-verifier changes.\n- `supplement_sections.md` / `.tex`: metric definitions, corrections, sensitivity, name-only and repeated-execution sections.\n- `manuscript_consistency_report.md` / `manuscript_changes.csv`: proposed replacement spans with sources.\n- `figures/` and `sensitivity/figures/`: regenerated PDF/SVG/PNG figures.\n- `reproducibility_manifest.json` / `verification.json`: code/input/output identities and final checks.\n'
    (OUT/'integration_report.md').write_text(summary)
    (sens/'sensitivity_report.md').write_text('# Camera-ready sensitivity on final approved references\n\nThis supersedes the earlier frozen-reference sensitivity for final reporting, without overwriting it.\n\n'+sensitivity_intro+sensitive+'\n\n## Rank-first frequencies\n\n'+md(winners[['panel','metric','aggregation','family','group','rank_first_frequency']])+'\n\n## Largest protocol drivers\n\n'+md(drivers[(drivers.aggregation!='all_protocols')].sort_values('max_absolute_delta',ascending=False).head(30)[['panel','group','metric','protocol_id','family','max_absolute_delta','largest_config','direct_delta_at_largest','rematching_delta_at_largest']])+'\n\nAll original and alternative observations are in `../results.csv`. Aggregates include paired protocol CIs; ranking CSVs distinguish strict reversals and ties. The 4.7.0 family and numerical fixes are active. Residual GT-aware family scope, N/V compatibility and graph-granularity limitations remain; these findings are conditional on those unchanged rules.\n')
    print(f'Wrote supplement drafts and {len(edits)} manuscript replacement records; manuscript unchanged.',flush=True)

if __name__=='__main__':main()
