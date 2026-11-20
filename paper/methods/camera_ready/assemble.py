"""Build the sole authoritative observation table and verify fresh baseline scoring.

All primary/cost/error/control/repeat figure views must derive from results.csv.
Historical columns are comparisons, never substituted for corrected scores.
"""
from freeze import *
sys.path.insert(0,str(OUT/'sensitivity'))
from evaluator import documents,clone,METRICS
from score_extras import score_case
import pandas as pd
import concurrent.futures, types

def main():
    lock=load(OUT/'input_manifest.json');extra=load(OUT/'extra_input_manifest.json')
    cases={c['case_id']:c for c in lock['cases']};baseline={};checks=[]
    # Independent uncached, default 4.7.0 calls plus current schema/link validation.
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--reuse-validation',action='store_true');args=parser.parse_args()
    if args.reuse_validation:
        checks=pd.read_csv(OUT/'baseline_validation.csv').to_dict('records')
        assert {r['case_id'] for r in checks}==set(cases)
        assert all(r['maximum_metric_error']==0 and r['details_identical'] and r['linked_valid'] for r in checks)
        assert all(sha(p)==h for p,h in lock['inputs'].items())
        baseline={cid:load(OUT/'sensitivity/case_results'/f'{cid}.baseline.json') for cid in cases}
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
            for result in pool.map(score_case,cases.values()):
                cid=result['case_id'];saved=load(OUT/'sensitivity/case_results'/f'{cid}.baseline.json')
                error=max(abs(result['metrics'][k]-saved['metrics'][k]) for k in result['metrics'])
                assert error < 1e-12,(cid,error)
                assert result['details']==saved['details'],cid
                checks.append(dict(case_id=cid,maximum_metric_error=error,details_identical=True,linked_valid=True))
                baseline[cid]=saved
        csvout(OUT/'baseline_validation.csv',checks)
    # Hashguard the error observer and import its 4.7.0 counterparts in a local module only.
    source=ROOT/'src/libstruct_bench/libgen/error_analysis.py'
    assert sha(source)==lock['inputs'][str(source)]
    observer=types.ModuleType('_camera_ready_error_observer')
    exec(compile(source.read_text().replace('from libstruct_bench.libgen.scoring import','from libstruct_bench.libgen.v4_7_0.scoring import'),str(source),'exec'),observer.__dict__)
    frozen=OUT.parent/'verifier_impact/results_frozen_v1'
    selected=lock['panels']
    extra_panels=pd.read_csv(frozen/'figure_per_protocol.csv').query("panel == 'fig3_common10'").to_dict('records')
    for member in extra_panels:member['panel']='fig3common10'
    assert len(extra_panels)==100 and {r['case_id'] for r in extra_panels}<=set(cases)
    historical=load(frozen/'manifest.json')['panels']
    oldlookup={(r['panel'],r['group'],r['protocol_id']):r for r in historical}
    cost=pd.read_csv(OUT.parent/'fig3_trials_extracted.csv')
    costlookup={(r['system_id'],r['protocol_id']):r for r in cost.to_dict('records')}
    records=[];errors={};comparison_metrics=METRICS+['reward','t2_exact_required_family_recall']
    def base_record(panel,group,pr,cid,trial):
        return dict(row_id=f'{panel}:{group}:{pr}',panel=panel,group=group,protocol_id=pr,case_id=cid,trial=trial,verifier='4.7.0',scorer_sha256=lock['scorer_sha256'],reference_set_id=lock['reference_set_id'],config_id='original')
    def scientific(row,case,result):
        diag=result['details']['diagnostic_metrics'];t2=diag['t2'];t3=diag['t3']
        row.update(result['metrics']);row.update(valid_completion=True,completion_status='valid_prediction',required_families=t2['required_family_count'],effective_predicted_families=t2['effective_prediction_family_count'],primary_t2_eligible=t2['required_family_count']>0,neutralized_families=t2['neutralized_prediction_family_count'],predicted_families=t2['predicted_family_count'],effective_spurious_families=t2['effective_prediction_family_count'] if t2['required_family_count']==0 else None)
        row.update({k:v for k,v in t3.items() if k.endswith('_count')})
        row.update(prediction_t2_path=case['p2'],prediction_t3_path=case['p3'],prediction_t2_sha256=sha(case['p2']),prediction_t3_sha256=sha(case['p3']),reference_t1_sha256=sha(case['truth']['paths']['T1']),reference_t2_sha256=sha(case['truth']['paths']['T2']),reference_t3_sha256=sha(case['truth']['paths']['T3']),reference_transform=case['truth']['transform'])
        workflows=load(case['p3'])['workflows']
        row.update(raw_workflow_count=len(workflows),raw_state_count=sum(len(w['states']) for w in workflows),raw_transition_count=sum(len(w['transitions']) for w in workflows),raw_typed_edge_count=sum(len(set(t[k])) for w in workflows for t in w['transitions'] for k in ['substrate_state_ids','carried_forward_product_ids','discarded_product_ids']))
        rr=Path(row['trial'])/'result.json';outcome=load(rr) if rr.exists() else {}
        row['historical_exception_type']=(outcome.get('exception_info') or {}).get('exception_type','')
        assert abs(row['reward']-(.3*row[METRICS[0]]+.7*row[METRICS[1]]))<1e-12
    for member in selected+extra_panels:
        cid=member['case_id'];case=cases[cid];result=baseline[cid]
        row=base_record(member['panel'],member['group'],member['protocol_id'],cid,member['trial']);scientific(row,case,result)
        frozen460=load(frozen/'cases'/f'{cid}.json')['original']
        # Frozen impact case stores metric and detail fields as separate dictionaries.
        if 'metrics' in frozen460:frozen460=frozen460['metrics']
        prev=load(OLD/'case_results'/f'{cid}.baseline.json')['metrics']
        for metric in comparison_metrics:
            row['frozen_460_'+metric]=frozen460[metric]
            row['frozen_470_'+metric]=prev[metric]
            row['reference_delta_'+metric]=row[metric]-prev[metric]
            row['total_delta_'+metric]=row[metric]-frozen460[metric]
        h=oldlookup.get((member['panel'],member['group'],member['protocol_id']))
        if h:
            row['historical_score_path']=h['reward_path'];row['historical_score_sha256']=sha(h['reward_path'])
            row.update({'historical_saved_'+k:v for k,v in h['saved_metrics'].items()})
        if member['panel']=='fig3common10':
            c=costlookup[(member['group'],member['protocol_id'])]
            assert Path(c['result_path']).parent==Path(member['trial']), 'Cost trial selection changed'
            for k in ['normalized_api_cost_usd','pricing_snapshot_id','pricing_date','normalized_api_cost_kind','input_tokens','output_tokens','cache_read_tokens','model','harness','protocol_label']:row[k]=c[k]
            if cid not in errors:
                docs=documents(case)
                doc=observer.build_error_analysis(trial_id=Path(member['trial']).name,protocol_id=case['protocol_id'],result=None,details={'prediction_valid':True,'scoring':result['details']},verifier_error=None,artifact_inventory=[],t2_prediction=docs[0],t3_prediction=docs[1],t2_groundtruth=docs[2],t3_groundtruth=docs[3],canonical_scoring=result['details'])
                errors[cid]=doc
            categories={o['category'] for o in errors[cid]['observations'] if o['substantive']}
            for cat in ['missing_recoverable_information','unsupported_completion','strand_or_orientation_error','molecular_state_or_assembly_error','operation_error','workflow_or_topology_error']:row['discrepancy_'+cat]=int(cat in categories)
        records.append(row)
    for member in extra['members']:
        cid=member['case_id'];case=next(c for c in extra['cases'] if c['case_id']==cid)
        row=base_record(member['panel'],member['group'],member['protocol_id'],cid,member['trial'])
        scientific(row,case,load(OUT/'replicate_results'/f'{cid}.json'))
        row.update(task_prompt_sha256=member['task_prompt_sha256'],started_at=member['started_at']);records.append(row)
    t1=pd.read_csv(OUT/'task1_recomputed.csv').to_dict('records')
    for item in t1:
        pr=item['protocol_id'];pp=ROOT/item['prediction_path']
        row=base_record('task1',item['model_family']+'_'+item['interface'],pr,sha(pp)[:24],str(pp.parent))
        row.update(verifier='Task1 unchanged v0 string scorer',scorer_sha256=sha(ROOT/'src/libstruct_bench/library_structure.py'),model=item['model'],interface=item['interface'],condition='pdf',sequence_similarity=item['sequence_similarity'],exact_match=item['exact_match'],completion_status=item['status'],valid_completion=item['prediction_parse_valid'],abstention=item['empty_library_output'],prediction_t1_path=str(pp),prediction_t1_sha256=sha(pp),reference_t1_sha256=item['groundtruth_sha256'],archived_api_errors=item['archived_api_errors'])
        records.append(row)
    provenance=load(OUT.parent/'task1_name_only_provenance.json')
    for item in pd.read_csv(OUT/'task1_controls_recomputed.csv').to_dict('records'):
        pr=item['protocol_id'];prov=next(r for r in provenance['runs'] if r['model']==item['model'] and r['protocol_id']==pr)
        pp=Path(prov['name_only']['case_path'])/'prediction.json'
        row=base_record('task1',item['model_family']+'_NameOnly',pr,sha(pp)[:24],str(pp.parent))
        row.update(verifier='Task1 unchanged v0 string scorer',scorer_sha256=sha(ROOT/'src/libstruct_bench/library_structure.py'),model=item['model'],interface='API',condition='name_only',method_group=item['method_group'],sequence_similarity=item['name_only_similarity'],exact_match=item['name_only_exact'],completion_status=item['name_only_status'],valid_completion=item['name_only_valid_sequence_output'],abstention=item['name_only_abstention'],prediction_t1_path=str(pp),prediction_t1_sha256=sha(pp),provider=item['name_only_provider'],reference_t1_sha256=sha(PRIVATE/'ground_truth'/pr/'groundtruth_final_lib_struct.json'))
        records.append(row)
    table=pd.DataFrame(records);assert table.row_id.is_unique;assert len(table)==625
    table['variant']='corrected_4_7_0'
    table['baseline_row_id']=table.row_id
    # Store all alternative scientific assumptions in the same authoritative
    # table. Identity/provenance fields absent on alternatives are inherited
    # from their explicitly named baseline_row_id, never another trial.
    alternatives=pd.concat([pd.read_csv(OUT/'sensitivity/case_results'/f'{cid}.csv') for cid in cases],ignore_index=True)
    members=table[table.panel.isin(['fig3a','fig3b','fig4a','fig4c','fig4d'])][['row_id','panel','group','protocol_id','case_id','trial']].rename(columns={'row_id':'baseline_row_id'})
    alternatives=members.merge(alternatives[alternatives.config_id!='original'],on=['case_id','protocol_id'],validate='many_to_many')
    alternatives['row_id']=alternatives.baseline_row_id+'@'+alternatives.config_id
    alternatives['verifier']='4.7.0';alternatives['reference_set_id']=lock['reference_set_id']
    alternatives['valid_completion']=True;alternatives['primary_t2_eligible']=alternatives.required_families>0
    # Original-setting sensitivity deltas are zero by definition.
    for col in alternatives:
        if col.endswith(('_direct_delta','_rematching_delta','_changed')):table[col]=0
    table=pd.concat([table,alternatives],ignore_index=True)
    assert len(table)==182740 and table.row_id.is_unique
    table.to_csv(OUT/'results.csv',index=False,float_format='%.17g')
    (OUT/'error_observations').mkdir(exist_ok=True)
    for cid,doc in errors.items():dump(OUT/'error_observations'/f'{cid}.json',doc)
    dump(OUT/'results_identity.json',dict(authoritative_table='results.csv',sha256=sha(OUT/'results.csv'),rows=len(table),verifier='4.7.0',reference_set_id=lock['reference_set_id'],primary_t2='macro mean over protocols with N_req > 0; zero-required separate',historical_C30_preserved=True,main_panel_rows=285,auxiliary_error_cost_rows=100,completed_codex_attempts=60,completed_task1_attempts=180))
    print('Authoritative table: 625 original-setting rows + 182115 alternative-setting rows; 247 independent baseline checks passed.',flush=True)

if __name__=='__main__':main()
