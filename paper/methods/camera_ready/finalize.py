"""Independent integration invariants and immutable input/output inventory.

Run after scoring, derived tables, figures and reports. Does not rescore or
write any input. A later edit requires re-running this verifier and re-locking
the new artifacts; previously published bundles should remain immutable.
"""
from tables import *
import ast
import importlib.metadata
import platform
import re
import subprocess
import xml.etree.ElementTree as ET


def main():
    lock = load(OUT/'input_manifest.json')
    extra = load(OUT/'extra_input_manifest.json')
    identity = load(OUT/'results_identity.json')
    checks = []

    def check(name, condition, **details):
        assert condition, (name, details)
        checks.append(dict(check=name, passed=True, **details))

    inputs = {**lock['inputs'], **extra['inputs']}
    check('all protected input bytes unchanged', all(sha(p)==h for p,h in inputs.items()), files=len(inputs))
    check('production 4.6.0 unchanged', sha(ROOT/'src/libstruct_bench/libgen/scoring.py')=='c23eee3363e8bde80cc31529c66008267aaf61f7456c482a23502ab169f2b2f7')
    check('4.7.0 scorer identity', sha(ROOT/'src/libstruct_bench/libgen/v4_7_0/scoring.py')==lock['scorer_sha256']=='1618943605e33e488654c7b02af85b027d3009c82f1aa1572d0d782b7c1da11e')
    old=load(OLD/'manifest.json')
    check('historical panel/trial/C30 selection unchanged', lock['panels']==old['panels'] and lock['historical_selection']==old['historical_checkpoint'])
    refs=pd.read_csv(OUT/'reference_consistency.csv')
    check('reference gate complete', len(refs)==96 and refs.protocol_id.nunique()==32 and refs.changed_from_sensitivity.sum()==6)
    check('current references match approved lock', all(sha(r.current_path)==r.current_sha256 for r in refs.itertuples()))
    check('authoritative table identity', sha(OUT/'results.csv')==identity['sha256'])
    data=pd.read_csv(OUT/'results.csv',low_memory=False)
    b=data[data.config_id=='original'].copy()
    main_panels=['fig3a','fig3b','fig4a','fig4c','fig4d']
    main=b[b.panel.isin(main_panels)]
    check('unique observation rows and expected membership counts', data.row_id.is_unique and len(data)==182740 and len(b)==625 and len(main)==285)
    check('main panel size', main.groupby('panel').size().to_dict()==dict(fig3a=80,fig3b=90,fig4a=20,fig4c=65,fig4d=30))
    check('selected run identities', {(r.panel,r.group,r.protocol_id,r.case_id,r.trial) for r in main.itertuples()}=={(r['panel'],r['group'],r['protocol_id'],r['case_id'],r['trial']) for r in lock['panels']})
    sweep=data[data.panel.isin(main_panels)]
    sm=load(OUT/'sensitivity/manifest.json')
    check('640 complete settings per membership', set(sweep.config_id)=={r['config_id'] for r in sm['configurations']} and sweep.groupby('baseline_row_id').size().eq(640).all())
    check('required support invariant across settings', sweep.groupby('baseline_row_id').required_families.nunique().eq(1).all())
    linked=data[data.panel!='task1']
    check('finite bounded linked scores', np.isfinite(linked[METRICS].to_numpy()).all() and ((linked[METRICS]>=-1e-12)&(linked[METRICS]<=1+1e-12)).all().all())
    check('reward constant unchanged', np.allclose(linked.reward,.3*linked[METRICS[0]]+.7*linked[METRICS[1]],rtol=0,atol=1e-12))
    check('informative eligibility and linked validity', linked.primary_t2_eligible.eq(linked.required_families>0).all() and linked.valid_completion.eq(True).all())
    check('all and only ddSEQ zero-required', set(main.loc[main.required_families==0,'protocol_id'])=={'ddseq_single_cell_3_rna_seq_kit'})
    validate=pd.read_csv(OUT/'baseline_validation.csv')
    check('all independent uncached baselines reproduce full details', len(validate)==247 and validate.case_id.nunique()==247 and validate.maximum_metric_error.eq(0).all() and validate.details_identical.all() and validate.linked_valid.all())
    refdelta=main.drop_duplicates('case_id')[[f'reference_delta_{m}' for m in METRICS]].abs().max(axis=1)>1e-12
    check('approved-reference effects counted', int(refdelta.sum())==14, affected_predictions=int(refdelta.sum()))
    v=sweep.merge(main[['row_id']+METRICS].rename(columns={'row_id':'baseline_row_id',**{m:m+'_baseline' for m in METRICS}}),on='baseline_row_id',validate='many_to_one')
    check('direct plus rematching equals full change', all(np.allclose(v[m+'_direct_delta']+v[m+'_rematching_delta'],v[m]-v[m+'_baseline'],rtol=0,atol=2e-12) for m in METRICS))
    ag=pd.read_csv(OUT/'aggregates.csv')
    for r in ag.itertuples():
        selected_rows=selected(b[(b.panel==r.panel)&(b.group==r.group)],r.metric,r.policy).sort_values('protocol_id')
        assert len(selected_rows)==r.n_protocols and abs(selected_rows[r.metric].mean()-r.mean)<1e-12
        assert np.allclose(ci(selected_rows[r.metric]),[r.ci_low,r.ci_high],rtol=0,atol=1e-12)
    check('all reported aggregates and bootstrap intervals derive from master', True, aggregate_rows=len(ag))
    sa=pd.read_csv(OUT/'sensitivity/aggregates.csv')
    for r in ag[ag.panel.isin(main_panels)&ag.policy.eq('primary')].itertuples():
        pol='informative_only' if r.metric==METRICS[0] else 'unchanged_panel'
        match=sa[(sa.panel==r.panel)&(sa.group==r.group)&(sa.metric==r.metric)&(sa.aggregation==pol)&(sa.config_id=='original')]
        assert len(match)==1 and np.allclose(match[['mean','ci_low','ci_high']].iloc[0],[r.mean,r.ci_low,r.ci_high],rtol=0,atol=1e-12)
    check('main and sensitivity baseline views agree',True)
    rep=b[b.panel=='codex_repeats']
    check('all three completed attempts retained',len(rep)==60 and rep.groupby('protocol_id').size().eq(3).all() and rep.valid_completion.all() and rep.groupby('protocol_id').task_prompt_sha256.nunique().eq(1).all())
    original_rep=rep[rep.group=='attempt_1'].set_index('protocol_id').sort_index()
    original_native=main[(main.panel=='fig3a')&(main.group=='gpt_codex')].set_index('protocol_id').sort_index()
    check('original Codex repeat is same selected prediction',original_rep.index.equals(original_native.index) and np.allclose(original_rep[METRICS],original_native[METRICS],rtol=0,atol=1e-12) and original_rep.prediction_t2_sha256.equals(original_native.prediction_t2_sha256) and original_rep.prediction_t3_sha256.equals(original_native.prediction_t3_sha256))
    task=b[b.panel=='task1'];pdf=task[task.condition=='pdf'];names=task[task.condition=='name_only']
    check('completed Task 1 denominators', len(task)==180 and len(pdf)==120 and len(names)==60 and pdf.valid_completion.sum()==111 and names.valid_completion.sum()==60 and pdf.abstention.sum()==9)
    for r in task.itertuples():
        assert r.reference_t1_sha256==sha(PRIVATE/'ground_truth'/r.protocol_id/'groundtruth_final_lib_struct.json')
        assert sha(r.prediction_t1_path)==r.prediction_t1_sha256
    check('Task 1 raw predictions and approved reference projections locked',True)
    t1old=pd.read_csv(OUT.parent/'task1_grades_long.csv');t1new=pd.read_csv(OUT/'task1_recomputed.csv')
    key=['model_family','interface','protocol_id']
    cmp=t1new.merge(t1old,on=key,validate='one_to_one',suffixes=('_new','_old'))
    check('Task 1 selected values reproduced',len(cmp)==120 and np.allclose(cmp.sequence_similarity_new,cmp.sequence_similarity_old,rtol=0,atol=1e-12) and cmp.exact_match_new.eq(cmp.exact_match_old).all())
    controlold=pd.read_csv(OUT.parent/'task1_name_only_protocols.csv');controlnew=pd.read_csv(OUT/'task1_controls_recomputed.csv')
    cmp=controlnew.merge(controlold,on=['model_family','protocol_id'],validate='one_to_one',suffixes=('_new','_old'))
    check('completed name-only values reproduced',len(cmp)==60 and np.allclose(cmp.name_only_similarity_new,cmp.name_only_similarity_old,rtol=0,atol=1e-12) and cmp.name_only_exact_new.eq(cmp.name_only_exact_old).all())
    c30=main[(main.panel=='fig4c')&(main.group=='C30')]
    check('C30 graph counts distinguish raw/effective edges',np.allclose(c30[['raw_state_count','raw_transition_count','raw_typed_edge_count','predicted_typed_edge_count']].mean(),[10,7.2,16.2,16],rtol=0,atol=1e-12))
    e=b[b.panel=='fig3common10']
    check('recomputed discrepancy prevalences', len(e)==100 and e.discrepancy_unsupported_completion.sum()==79 and e.discrepancy_molecular_state_or_assembly_error.sum()==100 and e.discrepancy_workflow_or_topology_error.sum()==100)
    def difference(panel,group_a,group_b,metric):
        pol='informative_only' if metric==METRICS[0] else 'unchanged_panel'
        view=sa[(sa.panel==panel)&(sa.metric==metric)&(sa.aggregation==pol)].pivot(index='config_id',columns='group',values='mean')
        return view[group_a]-view[group_b]
    check('combined knowledge informative T2 exceeds baseline throughout sweep',difference('fig4a','general_methods_plus_memory_v1','baseline',METRICS[0]).gt(0).all())
    check('held-out C30 Task 3 below baseline throughout sweep',all(difference('fig4d',g,'c0',m).lt(0).all() for g in ['c30_kb','c30_pact'] for m in METRICS[1:]))
    trajectory=difference('fig4c','C58','C0',METRICS[0])
    check('late trajectory T2 sign depends on weight assumption',trajectory.loc['original']<0 and trajectory.loc['t2_uniform']>0)
    # Convenient full-provenance original-setting view, derived from master only.
    main.to_csv(OUT/'selected_protocol_scores.csv',index=False,float_format='%.17g')
    paper=Path('external/manuscript/main.tex')
    lines=paper.read_text().splitlines();edits=pd.read_csv(OUT/'manuscript_changes.csv');occupied=set()
    for r in edits.itertuples():
        assert '\n'.join(lines[r.start_line-1:r.end_line])==r.old_text,r.change_id
        span=set(range(r.start_line,r.end_line+1));assert not span&occupied,r.change_id;occupied|=span
    check('proposed manuscript spans match pinned text and do not overlap',sha(paper)==lock['main_tex_sha256'], proposed_edits=len(edits))
    for p in OUT.rglob('*.py'):ast.parse(p.read_text(),filename=str(p))
    figures=list((OUT/'figures').glob('*.pdf'))+list((OUT/'sensitivity/figures').glob('*.pdf'))
    check('21 vector figure sets present',len(figures)==21)
    for p in figures:
        assert p.read_bytes().startswith(b'%PDF-') and p.stat().st_size>3000
        ET.parse(p.with_suffix('.svg'))
        assert p.with_suffix('.png').stat().st_size>10000
    fm=load(OUT/'figure_manifest.json')
    check('main figure identities and source table locked',fm['script_sha256']==sha(OUT/'figures.py') and fm['authoritative_table_sha256']==identity['sha256'] and all(sha(OUT/r['path'])==r['sha256'] for r in fm['figures']))
    ev=load(OUT/'sensitivity/evaluation_integrity.json')
    check('scoring code unchanged after full sweep',all(sha(p)==h for p,h in ev['code_hashes'].items()) and ev['total_full_pipeline_scores']==158080)
    # Capture remaining consumed reporting sources too (they contain historical
    # costs/provenance, not newly scored scientific observations).
    reporting_sources={str(OUT.parent/p):sha(OUT.parent/p) for p in ['fig3_trials_extracted.csv','prepare_task1_paper_data.py','prepare_task1_name_only.py','task1_reference.py','plot_fig3_baselines.R','plot_fig4_external_knowledge.R','plot_fig4cd_autonomous_improvement.R']}
    versions={}
    for name in ['numpy','pandas','scipy','matplotlib','jsonschema','pydantic']:
        try: versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name]=None
    versions['R_figure_stack']=subprocess.check_output(['Rscript','-e','cat("R=", as.character(getRversion()), "; ggplot2=", as.character(packageVersion("ggplot2")), "; scales=", as.character(packageVersion("scales")), "; pheatmap=", as.character(packageVersion("pheatmap")), "; dplyr=", as.character(packageVersion("dplyr")), "; jsonlite=", as.character(packageVersion("jsonlite")), sep="")'],text=True)
    result=dict(status='passed',checks=checks,manuscript_edited=False,agents_rerun=False,ground_truth_edited=False,historical_results_overwritten=False,tex_compiled=False,figure_qa='Standalone PNG review and PDF/SVG structure checks; final manuscript placement/typesetting requires author review.')
    dump(OUT/'verification.json',result)
    ignored={'reproducibility_manifest.json'}
    artifacts={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name not in ignored and not any(x.startswith('.') or x=='__pycache__' for x in p.relative_to(OUT).parts)}
    manifest=dict(format_version=1,reference_set_id=lock['reference_set_id'],verifier='4.7.0',scorer_sha256=lock['scorer_sha256'],authoritative_table=identity,python=platform.python_version(),platform=platform.platform(),packages=versions,random_seeds=dict(weights=20261012,protocol_bootstrap=20261013,task1_bootstrap=20261010,repeated_execution=20261011),bootstrap_draws=dict(main_and_sensitivity=5000,task1_and_repeated_execution=100000),seed_construction='Weights: SeedSequence([seed,vector_index]); bootstrap: SeedSequence([seed,n_protocols]); descriptive repeat-choice envelope: default_rng(20261011)',historical_selection=lock['historical_selection'],protected_inputs=inputs,additional_reporting_inputs=reporting_sources,artifacts=artifacts,reproduction='python reproduce.py --output ../camera_ready_v4_7_0_reproduction',limitations=['No completed matched C0/C30 learning repeats','No model/harness variance decomposition','No refinement-invariant graph scoring or new biological adjudication','Final manuscript was neither edited nor compiled','Four reference files match approved scientific content through checked modality aliases, rather than identical promotion bytes'])
    dump(OUT/'reproducibility_manifest.json',manifest)
    print(f'{len(checks)} integration checks passed; {len(inputs)} protected inputs unchanged; {len(artifacts)} artifacts locked.',flush=True)


if __name__=='__main__':main()
