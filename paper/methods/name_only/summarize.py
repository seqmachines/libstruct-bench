"""Read-only name-only comparison with camera-ready references and scorer.

No inference or source-result writes. Output must be a new directory.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
CAMERA = ROOT / 'analysis/paper_results/camera_ready_v4_7_0'
EXPERIMENT = ROOT / 'benchmarks/libgen/name_only/experiment.json'
sys.path.insert(0, str(ROOT / 'src'))
METRICS = ['t2_required_family_f1', 't3_molecular_transition_f1', 't3_state_f1', 't3_typed_edge_f1']
LABELS = ['Oligo', 'Transition', 'State', 'Typed edge']
SYSTEMS = {'codex': ('GPT/Codex', 'gpt_codex'),
           'claude-code': ('Claude Code', 'claude_code'),
           'antigravity-cli': ('Gemini/Antigravity', 'gemini_antigravity')}


def load(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p, x): Path(p).write_text(json.dumps(x, indent=2, sort_keys=True) + '\n')
def csvout(p, rows):
    with Path(p).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def prediction(trial, name):
    ps = sorted((trial / 'artifacts').rglob(name))
    assert len({sha(p) for p in ps}) <= 1, f'Conflicting predictions: {trial}'
    return str(ps[0]) if ps else None


def score(case):
    from libstruct_bench.libgen.v4_7_0.scoring import grade_libgen
    from libstruct_bench.libgen.prediction_validation import (
        validate_t2_prediction, validate_t3_prediction, validate_prediction_links)
    row = dict(case_id=case['case_id'], trial=case['trial'],
               protocol_id=case['protocol_id'], exception=case['exception'],
               status='missing_output', prediction_valid=False, scores=None)
    if not case['p2'] or not case['p3']:
        return row
    try:
        p2, p3 = load(case['p2']), load(case['p3'])
        validate_t2_prediction(p2, protocol_id=case['protocol_id'])
        validate_t3_prediction(p3, protocol_id=case['protocol_id'])
        validate_prediction_links(p2, p3)
    except ValueError as exc:
        row.update(status='invalid_output', validation_error=str(exc))
        return row
    assert case['truth']['transform'] == 'none'
    t2, t3 = [load(case['truth']['paths'][k]) for k in ('T2', 'T3')]
    scores, details = grade_libgen(p2, p3, t2, t3)
    row.update(scores=scores, details=details, prediction_valid=True,
               empty_oligos=not p2['oligos'], empty_workflows=not p3['workflows'],
               status='runtime_failure_with_output' if case['exception'] else 'valid_completion')
    return row


def main():
    import numpy as np
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=Path(__file__).parent/'results_v1')
    args = ap.parse_args(); out = args.out.resolve()
    assert not out.exists(), f'Use a fresh output directory: {out}'
    experiment, camera = load(EXPERIMENT), load(CAMERA/'input_manifest.json')
    inputs = {}
    def pin(p, expected=None):
        p = Path(p).resolve(); digest = sha(p)
        if expected: assert digest == expected, f'Changed input: {p}'
        inputs[str(p)] = digest
        return digest
    pin(EXPERIMENT); pin(CAMERA/'input_manifest.json')
    scorer = ROOT/'src/libstruct_bench/libgen/v4_7_0/scoring.py'
    pin(scorer, camera['scorer_sha256'])
    for p in (ROOT/'src/libstruct_bench').rglob('*.py'):
        pin(p)
    for p in (ROOT/'schemas').rglob('*.json'): pin(p)
    # Verify the name-only environment and inference-controller lock as frozen.
    for p, h in experiment['generated_files'].items(): pin(EXPERIMENT.parent/p, h)
    for p, h in experiment['controller_files'].items(): pin(p, h)
    refs = {c['protocol_id']: c['truth'] for c in camera['cases']}
    reference_checks = []
    for pid, g in experiment['groundtruth'].items():
        for t, p in refs[pid]['paths'].items():
            current = pin(p, camera['inputs'][str(Path(p).resolve())])
            frozen = pin(EXPERIMENT.parent/'tasks'/pid/'tests/groundtruth'/Path(p).name,
                         g['sha256'][Path(p).name])
            assert current == frozen and refs[pid]['transform'] == 'none'
            reference_checks.append(dict(protocol_id=pid, task=t, sha256=current, matches_camera_ready=True))
    pin(CAMERA/'selected_protocol_scores.csv')
    selected = pd.read_csv(CAMERA/'selected_protocol_scores.csv')
    selected = selected[(selected.panel == 'fig3a') & selected.group.isin([v[1] for v in SYSTEMS.values()])]
    assert len(selected) == 60
    cases, memberships, isolation = {}, [], []

    def register(trial, system, condition, attempt, pid, record=None):
        trial = Path(trial).resolve(); pin(trial/'result.json', record['result_sha256'] if record else None)
        result = load(trial/'result.json'); assert result.get('finished_at'), trial
        cid = hashlib.sha256(str(trial).encode()).hexdigest()[:24]
        p2, p3 = [prediction(trial, f'{t}_prediction.json') for t in ('t2','t3')]
        for t, p in [('t2',p2), ('t3',p3)]:
            if p:
                pin(p, record['predictions'][t]['sha256'] if record else None)
        a = result['config']['agent']; spec = experiment['systems'][system]
        settings = {k: a.get(k) for k in ['model_name','import_path']}
        settings.update({k: a.get('kwargs',{}).get(k) for k in ['version','reasoning_effort']})
        if condition != 'fig3a_selected':
            assert settings['model_name'] == spec['model']
            assert settings['version'] == spec['version']
            assert settings['reasoning_effort'] == spec['effort']
        if condition == 'name_only':
            rp, tp = trial/'agent/name-only-isolation.json', trial/'agent/trajectory.json'
            pin(rp); pin(tp); receipt, trajectory = load(rp), load(tp)
            assert receipt['ready'] and receipt['direct_tcp_blocked']
            assert all(' 403 ' in v for v in receipt['blocked'].values())
            assert receipt['experiment_lock_sha256'] in {sha(EXPERIMENT), *spec.get('compatible_receipt_lock_sha256',[])}
            inv = load(EXPERIMENT.parent/'tasks'/pid/'environment/name_only_inventory.json')
            assert receipt['workspace_hashes'] == inv
            tool_names = Counter()
            for step in trajectory.get('steps',[]):
                for tool in step.get('tool_calls',[]) or []:
                    name = str(tool.get('function_name') or tool.get('tool_name') or '')
                    tool_names[name] += 1
                    assert not any(w in name.lower() for w in
                        ('web_search','search_web','read_url','browser','mcp','invoke_subagent','spawn_agent')), (trial,name)
            isolation.append(dict(system=system, protocol_id=pid, trial=str(trial),
                                  receipt_sha256=sha(rp), trajectory_sha256=sha(tp),
                                  tool_names=dict(tool_names), isolation_checks_passed=True))
        cases[cid] = dict(case_id=cid, trial=str(trial), protocol_id=pid, p2=p2, p3=p3,
                          truth=refs[pid], exception=(result.get('exception_info') or {}).get('exception_type'))
        memberships.append(dict(system=system, condition=condition, attempt=attempt, protocol_id=pid,
                                case_id=cid, trial=str(trial), started_at=result.get('started_at'), **settings))
        return cid

    for system, spec in experiment['systems'].items():
        for pid in experiment['groundtruth']:
            recs = [r for r in experiment['baseline'][system] if r['protocol_id'] == pid]
            assert len(recs) == 3
            for i, rec in enumerate(recs): register(rec['trial'],system,'bundle_n3',i+1,pid,rec)
        by_pid = {}
        job = ROOT/'runs/libgen'/spec['family']/spec['job']
        for p in sorted(job.glob('*/result.json')):
            pid = load(p)['task_name'].removeprefix('sequencing/libgen-name-only-')
            assert pid not in by_pid
            by_pid[pid] = p.parent
        assert set(by_pid) == set(experiment['groundtruth'])
        for pid, trial in by_pid.items(): register(trial,system,'name_only',1,pid)
        group = SYSTEMS[system][1]
        for _, row in selected[selected.group == group].iterrows():
            for t in ('t2','t3'): pin(row[f'prediction_{t}_path'], row[f'prediction_{t}_sha256'])
            register(row.trial,system,'fig3a_selected',1,row.protocol_id)

    out.mkdir(parents=True); (out/'case_results').mkdir()
    dump(out/'input_manifest.json', dict(inputs=inputs, cases=list(cases.values()), memberships=memberships,
        reference_set_id=camera['reference_set_id'], scorer_sha256=camera['scorer_sha256'],
        scorer_version='4.7.0', experiment_sha256=sha(EXPERIMENT),
        primary_policy=experiment['output_failure_policy'], baseline_policy=experiment['baseline_policy']))
    csvout(out/'reference_checks.csv',reference_checks); dump(out/'isolation_checks.json',isolation)
    scored = {}
    with ProcessPoolExecutor(max_workers=3) as pool:
        for i,r in enumerate(pool.map(score,cases.values())):
            scored[r['case_id']] = r; dump(out/'case_results'/f"{r['case_id']}.json",r)
            if (i+1)%20==0: print(f'Scored {i+1}/{len(cases)} saved trials',flush=True)
    # Fresh main-selection scores must reproduce the existing paper table.
    for member in memberships:
        if member['condition'] != 'fig3a_selected': continue
        row = selected[(selected.group == SYSTEMS[member['system']][1]) & (selected.protocol_id == member['protocol_id'])].iloc[0]
        actual = scored[member['case_id']]
        assert actual['prediction_valid']
        for metric in METRICS:
            assert abs(actual['scores'][metric]-row[metric]) < 1e-12, (member,metric)
    rows, completions = [], []
    for system in SYSTEMS:
        for condition in ['bundle_n3','name_only','fig3a_selected']:
            members = [m for m in memberships if m['system']==system and m['condition']==condition]
            counts = Counter(scored[m['case_id']]['status'] for m in members)
            completions.append(dict(system=system,condition=condition,n_executions=len(members),
                valid_completions=counts['valid_completion'], invalid_or_failed=len(members)-counts['valid_completion'],
                statuses=json.dumps(counts,sort_keys=True)))
        for pr in experiment['protocols']:
            pid=pr['protocol_id']; rr={}
            for condition in ['bundle_n3','name_only','fig3a_selected']:
                rr[condition]=[scored[m['case_id']] for m in memberships if
                    m['system']==system and m['protocol_id']==pid and m['condition']==condition]
            b,n,f=rr['bundle_n3'],rr['name_only'][0],rr['fig3a_selected'][0]
            nreq=experiment['groundtruth'][pid]['n_required']
            valid_pair=n['status']=='valid_completion' and all(x['status']=='valid_completion' for x in b)
            for metric,label in zip(METRICS,LABELS):
                eligible=metric!=METRICS[0] or nreq>0
                bval=float(np.mean([x['scores'][metric] if x['status']=='valid_completion' else 0. for x in b]))
                nval=n['scores'][metric] if n['status']=='valid_completion' else 0.
                fval=f['scores'][metric] if f['status']=='valid_completion' else 0.
                rows.append(dict(system=system,protocol_id=pid,protocol_name=pr['canonical_name'],metric=metric,
                    label=label,n_required=nreq,eligible=eligible,valid_pair=valid_pair,
                    name_only_status=n['status'],name_only_exception=n['exception'],
                    bundle_n3=bval,name_only=nval,name_minus_bundle_n3=nval-bval,
                    bundle_fig3a=fval,name_minus_bundle_fig3a=nval-fval))
    csvout(out/'completion_counts.csv',completions);csvout(out/'per_protocol.csv',rows)
    df=pd.DataFrame(rows); summary=[]
    for si,system in enumerate(SYSTEMS):
        for mi,metric in enumerate(METRICS):
            for policy in ['valid_complete_pairs','all_scheduled_failure_zero']:
                d=df[(df.system==system)&(df.metric==metric)&df.eligible]
                if policy=='valid_complete_pairs':d=d[d.valid_pair]
                for baseline in ['bundle_n3','bundle_fig3a']:
                    delta=(d.name_only-d[baseline]).to_numpy()
                    rng=np.random.default_rng(np.random.SeedSequence([20261022,si,mi,len(d)]))
                    idx=rng.integers(0,len(d),(100_000,len(d)))
                    lo,hi=np.quantile(delta[idx].mean(axis=1),[.025,.975])
                    summary.append(dict(system=system,metric=metric,label=LABELS[mi],policy=policy,baseline=baseline,
                        n_protocols=len(d),bundle_mean=float(d[baseline].mean()),name_only_mean=float(d.name_only.mean()),
                        name_minus_bundle=float(delta.mean()),ci_low=float(lo),ci_high=float(hi),
                        bundle_wins=int((delta < -1e-12).sum()),name_only_wins=int((delta>1e-12).sum()),
                        ties=int((abs(delta)<=1e-12).sum())))
    csvout(out/'summary.csv',summary)
    # Task 1 is a separate API experiment and stays separate in reporting.
    p=CAMERA/'task1_controls_recomputed.csv';pin(p);t1=pd.read_csv(p)
    assert len(t1)==60
    csvout(out/'task1_per_protocol.csv',t1.to_dict('records'))
    dump(out/'settings.json',dict(systems=experiment['systems'],memberships=memberships))
    assert all(sha(p)==h for p,h in inputs.items())
    dump(out/'verification.json',dict(inputs_unchanged=True,reference_files_verified=len(reference_checks),
        name_only_isolation_checks=len(isolation),unique_trials_scored=len(cases),
        main_selected_scores_reproduced=60,weights_changed=False,agent_inference=False,
        bootstrap_draws=100000,bootstrap_seed=20261022,
        bootstrap_unit='Paired protocol; all bundle replicates remain together',
        primary_comparison='Three bundle attempts mean versus one name-only attempt; valid complete pairs',
        protected_input_count=len(inputs),script_sha256=sha(__file__)))
    print(pd.DataFrame(summary).query("policy=='valid_complete_pairs' and baseline=='bundle_n3'").to_string(index=False),flush=True)


if __name__=='__main__':main()
