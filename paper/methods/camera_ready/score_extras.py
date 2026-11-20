"""Rescore completed Task 1 controls and all three observed Codex attempts only."""
from freeze import *
sys.path.insert(0,str(OUT/'sensitivity'))
sys.path.insert(0,str(OUT.parent))
from evaluator import documents, clone
from libstruct_bench.libgen.prediction_validation import validate_t2_prediction,validate_t3_prediction,validate_prediction_links
import prepare_task1_paper_data as task1
import prepare_task1_name_only as control
import concurrent.futures

def score_case(case):
    docs=documents(case)
    validate_t2_prediction(docs[0],protocol_id=case['protocol_id'])
    validate_t3_prediction(docs[1],protocol_id=case['protocol_id'])
    validate_prediction_links(*docs[:2])
    metrics,details=clone().grade_libgen(*docs)
    return dict(case_id=case['case_id'],metrics=metrics,details=details,valid=True)

def main():
    lock=load(OUT/'input_manifest.json');inputs={}
    def pin(p,h=None):
        p=Path(p).resolve();actual=sha(p)
        if h:assert actual==h,str(p)
        inputs[str(p)]=actual
    for name in ['task1_provenance.json','task1_reference_lock.json','task1_reference.py','prepare_task1_paper_data.py','prepare_task1_name_only.py','task1_grades_long.csv','task1_name_only_provenance.json','task1_name_only_protocols.csv','codex_replicate_sensitivity.json']:
        pin(OUT.parent/name)
    # Their collection functions recompute scores/validity from raw predictions,
    # verify frozen references/provenance, and have no artifact writes.
    t1rows,t1audits,t1provenance=task1.collect()
    config=load(ROOT/'benchmarks/library_structure/round2/name_only.json')
    control_root=ROOT/'runs/library_structure/round2/name_only'
    cnrows,cnprovenance=control.collect(control_root,config)
    assert cnprovenance['complete']
    for row in t1rows:
        pin(ROOT/row['prediction_path'],row['prediction_sha256'])
    original=load(OUT.parent/'task1_name_only_provenance.json')
    for r in original['runs']:
        for fn,h in r['name_only']['files_sha256'].items():pin(Path(r['name_only']['case_path'])/fn,h)
    csvout(OUT/'task1_recomputed.csv',t1rows);csvout(OUT/'task1_controls_recomputed.csv',cnrows)
    dump(OUT/'task1_recomputed_provenance.json',t1provenance)
    dump(OUT/'task1_controls_recomputed_provenance.json',cnprovenance)
    repeats=load(OUT.parent/'codex_replicate_sensitivity.json');cases=[];members=[]
    for protocol in repeats['protocols']:
        pr=protocol['protocol_id'];truth=next(c['truth'] for c in lock['cases'] if c['protocol_id']==pr)
        for ri,attempt in enumerate(protocol['attempts']):
            trial=ROOT/attempt['trial']
            for f in attempt['inputs']:pin(ROOT/f['path'],f['sha256'])
            p2=trial/'artifacts/logs/artifacts/t2_prediction.json';p3=p2.with_name('t3_prediction.json')
            pin(p2);pin(p3)
            cid=hashlib.sha256((str(p2)+str(p3)).encode()).hexdigest()[:24]
            case=dict(case_id=cid,protocol_id=pr,p2=str(p2),p3=str(p3),truth=truth)
            cases.append(case);members.append(dict(panel='codex_repeats',group=f'attempt_{ri+1}',protocol_id=pr,case_id=cid,trial=str(trial),task_prompt_sha256=attempt['task_prompt_sha256'],started_at=attempt['started_at']))
    assert len(cases)==60
    for pr in repeats['protocols']:assert len({x['task_prompt_sha256'] for x in pr['attempts']})==1
    dump(OUT/'extra_input_manifest.json',dict(inputs=inputs,cases=cases,members=members,source_replicate_notes=repeats['method_notes']))
    dest=OUT/'replicate_results';dest.mkdir(exist_ok=True)
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
        for result in pool.map(score_case,cases):dump(dest/(result['case_id']+'.json'),result)
    assert all(sha(p)==h for p,h in inputs.items())
    print('120 Task 1 API/agent, 60 name-only and 60 Codex attempts rescored; inputs unchanged.',flush=True)

if __name__=='__main__':main()
