"""Protocol-paired factorial analysis of locked camera-ready scores; no inference/scoring.

Run with --output pointing to a NEW directory. Only that directory is written.
The script files live separately from generated results to preserve previous runs.
"""
from pathlib import Path
from itertools import combinations, product
from collections import Counter
import argparse, hashlib, json, os, platform, shutil, subprocess
import numpy as np
import pandas as pd
import scipy
from scipy import stats

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'camera_ready_v4_7_0'
MODELS = ['GPT', 'Claude', 'Gemini']
HARNESSES = ['Pi', 'mini-SWE']
METRICS = ['t2_required_family_f1', 't3_molecular_transition_f1', 't3_state_f1', 't3_typed_edge_f1']
LABELS = ['Task 2 oligo-family F1', 'Task 3 transition F1', 'Task 3 state F1', 'Task 3 typed-edge F1']
GROUPS = {f'{m.lower()}_{h}': (m, label) for m in MODELS for h, label in [('pi', 'Pi'), ('mini_swe', 'mini-SWE')]}
GROUPS.update({'gpt_codex': ('GPT', 'Codex'), 'claude_code': ('Claude', 'Claude Code'), 'gemini_antigravity': ('Gemini', 'Antigravity CLI')})
NATIVE = {'GPT': 'Codex', 'Claude': 'Claude Code', 'Gemini': 'Antigravity CLI'}
EXPECTED = {'GPT': 'gpt-5.6-sol', 'Claude': 'claude-opus-5', 'Gemini': 'gemini-3.7-flash'}
SEED, DRAWS = 20261014, 10000
os.environ.setdefault('XDG_CACHE_HOME','/private/tmp/libstruct-model-harness-cache')

def load(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p, obj): Path(p).write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False)+'\n')
def csv(p, rows): pd.DataFrame(rows).to_csv(p, index=False, float_format='%.17g')
def text_hash(t): return hashlib.sha256(t.encode()).hexdigest()
def bootstrap(n): return np.random.default_rng(np.random.SeedSequence([SEED, n])).integers(n, size=(DRAWS, n))

def summarize(v, test=True):
    v=np.asarray(v, float); n=len(v); boots=v[bootstrap(n)].mean(axis=1)
    lo,hi=np.quantile(boots,[.025,.975]); sd=v.std(ddof=1); se=sd/np.sqrt(n)
    out=dict(n_protocols=n, estimate=float(v.mean()), ci_low=float(lo), ci_high=float(hi), protocol_sd=float(sd), protocol_se=float(se))
    if test:
        signs=np.asarray(list(product([-1,1], repeat=n)))
        p=float(np.mean(np.abs(signs@v/n)>=abs(v.mean())-1e-14))
        t=float(v.mean()/se) if se else 0.0
        out.update(t_stat=t, t_df=n-1, t_p=float(2*stats.t.sf(abs(t),n-1)), signflip_p=p,
                   t_ci_low=float(v.mean()-stats.t.ppf(.975,n-1)*se),t_ci_high=float(v.mean()+stats.t.ppf(.975,n-1)*se))
    return out

def holm(rows, family, pcol, outcol):
    df=pd.DataFrame(rows)
    if outcol not in df: df[outcol]=np.nan
    for _, part in df[df[pcol].notna()].groupby(family):
        order=part.sort_values(pcol).index; k=len(order)
        adjusted=np.maximum.accumulate([min(1,float(df.loc[j,pcol])*(k-i)) for i,j in enumerate(order)])
        df.loc[order,outcol]=adjusted
    return df

def contrasts():
    """Weights in GPT/Pi,GPT/mini,Claude/Pi,... order; equal factor weighting."""
    cells=np.eye(6).reshape(3,2,6); rows=[]
    def add(name,kind,w): rows.append(dict(contrast=name,kind=kind,**{f'w{i}':float(x) for i,x in enumerate(w)}))
    for m,model in enumerate(MODELS):
        for h,harness in enumerate(HARNESSES): add(f'{model} | {harness}','cell_mean',cells[m,h])
        add(model,'model_marginal_mean',cells[m].mean(axis=0))
    for h,harness in enumerate(HARNESSES): add(harness,'harness_marginal_mean',cells[:,h].mean(axis=0))
    add('Grand mean','grand_mean',cells.mean(axis=(0,1)))
    for i,j in combinations(range(3),2):
        add(f'{MODELS[j]} - {MODELS[i]}','model_main_contrast',cells[j].mean(axis=0)-cells[i].mean(axis=0))
    add('mini-SWE - Pi (averaged models)','harness_main_contrast',cells[:,1].mean(axis=0)-cells[:,0].mean(axis=0))
    for i,m in enumerate(MODELS): add(f'{m}: mini-SWE - Pi','model_specific_harness',cells[i,1]-cells[i,0])
    for i,j in combinations(range(3),2):
        add(f'({MODELS[j]} - {MODELS[i]}) x (mini-SWE - Pi)','interaction',(cells[j,1]-cells[j,0])-(cells[i,1]-cells[i,0]))
    return rows

def freeze(out):
    original=load(SOURCE/'reproducibility_manifest.json'); inputs={}
    def pin(path, expected=None):
        path=Path(path).resolve(); h=sha(path)
        if expected is not None: assert h==expected, str(path)
        inputs[str(path)]=h; return h
    for name in ['results.csv','results_identity.json','input_manifest.json','extra_input_manifest.json','aggregates.csv','fig3b_plot_data.csv','reference_consistency.csv']:
        pin(SOURCE/name, original['artifacts'][name])
    pin(SOURCE/'reproducibility_manifest.json')
    pin(HERE.parents[2]/'src/libstruct_bench/libgen/scoring.py', original['protected_inputs'][str(HERE.parents[2]/'src/libstruct_bench/libgen/scoring.py')])
    locked=load(SOURCE/'input_manifest.json'); cases={r['case_id']:r for r in locked['cases']}
    pin(HERE.parents[2]/'src/libstruct_bench/libgen/v4_7_0/scoring.py',locked['scorer_sha256'])
    d=pd.read_csv(SOURCE/'results.csv',low_memory=False).query("panel=='fig3b' and config_id=='original'").copy()
    assert len(d)==90 and d.trial.nunique()==90 and d.case_id.nunique()==90
    assert set(d.group)==set(GROUPS) and d.groupby(['group','protocol_id']).size().eq(1).all()
    assert d.protocol_id.nunique()==10 and d.groupby('group').size().eq(10).all()
    identity_columns=['group','protocol_id','case_id','trial']
    assert set(d[identity_columns].itertuples(index=False,name=None))=={tuple(r[k] for k in identity_columns) for r in locked['panels'] if r['panel']=='fig3b'}
    assert d.valid_completion.eq(True).all() and d.verifier.eq('4.7.0').all()
    assert d.groupby('protocol_id')[['reference_t2_sha256','reference_t3_sha256']].nunique().eq(1).all().all()
    d['model']=d.group.map(lambda g:GROUPS[g][0]);d['harness']=d.group.map(lambda g:GROUPS[g][1])
    d['shared']=d.harness.isin(HARNESSES)
    settings=[]; runtime=[]; execution_ids=[]
    for r in d.itertuples():
        t=Path(r.trial);rp=t/'result.json';lp=t/'lock.json'
        for f in [rp,lp,t/'config.json']:
            pin(f,original['protected_inputs'].get(str(f)))
        rr=load(rp); lk=load(lp);agent=rr['config']['agent'];info=rr['agent_info'];config=rr['config'];execution_ids.append(rr['id'])
        recorded_model=info['model_info']['name'];base_model=recorded_model.removesuffix('-high') if r.group=='gemini_antigravity' else recorded_model
        assert base_model==EXPECTED[r.model],(r.group,recorded_model)
        kw=agent.get('kwargs',{});reasoning=kw.get('thinking',kw.get('reasoning_effort','high (model alias)' if r.group=='gemini_antigravity' else 'not recorded'))
        assert reasoning.startswith('high') if r.model=='Gemini' else reasoning=='max'
        st=dict(group=r.group,model=r.model,harness=r.harness,shared=r.shared,protocol_id=r.protocol_id,trial=r.trial,execution_id=rr['id'],
                configured_model=agent['model_name'],recorded_model=recorded_model,provider=info['model_info'].get('provider'),
                harness_version=info['version'],reasoning=reasoning,configured_max_tokens=kw.get('max_tokens'),
                started_at=rr['started_at'],finished_at=rr.get('finished_at'),task_digest=lk['task']['digest'],task_checksum=rr.get('task_checksum'),
                timeout_multiplier=config.get('timeout_multiplier'),override_timeout_sec=agent.get('override_timeout_sec'),
                max_timeout_sec=agent.get('max_timeout_sec'),environment_type=config['environment'].get('type'),
                agent_resume=agent.get('resume_trajectory'),historical_exception=(rr.get('exception_info') or {}).get('exception_type'),
                result_sha256=inputs[str(rp.resolve())],lock_sha256=inputs[str(lp.resolve())])
        if r.harness=='Pi':
            models=set();levels=set();providers=set();user_hashes=[];response_models=set()
            for f in sorted((t/'agent/pi/sessions').glob('*.jsonl')):
                pin(f)
                for line in f.open():
                    e=json.loads(line)
                    if e.get('type')=='model_change': models.add(e['modelId']);providers.add(e['provider'])
                    if e.get('type')=='thinking_level_change': levels.add(e['thinkingLevel'])
                    msg=e.get('message',{})
                    if msg.get('role')=='user':user_hashes.append(text_hash(json.dumps(msg.get('content'),sort_keys=True)))
                    if msg.get('role')=='assistant' and msg.get('model'):response_models.add(msg['model'])
            assert models=={EXPECTED[r.model]} and levels=={reasoning},(r.group,models,levels)
            runtime.append(dict(group=r.group,protocol_id=r.protocol_id,models=sorted(models),response_models=sorted(response_models),reasoning=sorted(levels),providers=sorted(providers),user_message_hashes=user_hashes,max_tokens='not explicit in saved Pi configuration'))
        elif r.harness=='mini-SWE':
            f=t/'agent/mini-swe-agent.trajectory.json';pin(f);raw=load(f);c=raw['info']['config'];m=c['model'];params={k:v for k,v in m['model_kwargs'].items() if k!='extra_headers'}
            assert m['model_name']==agent['model_name'] and (params.get('max_tokens',params.get('max_output_tokens'))==64000)
            observed=params.get('reasoning_effort',params.get('reasoning',{}).get('effort'))
            assert observed==reasoning
            runtime.append(dict(group=r.group,protocol_id=r.protocol_id,models=[m['model_name']],model_type=c.get('model_type'),parameters=params,
                                agent_type=c.get('agent_type'),step_limit=c['agent'].get('step_limit'),cost_limit=c['agent'].get('cost_limit'),
                                system_template_sha256=text_hash(c['agent']['system_template']),instance_template_sha256=text_hash(c['agent']['instance_template']),
                                response_models=sorted({str(m['model']) for m in raw['messages'] if m.get('model')})))
        settings.append(st)
        case=cases[r.case_id]
        for k in ['t2','t3']: pin(getattr(r,f'prediction_{k}_path'),getattr(r,f'prediction_{k}_sha256'))
        for task,path in case['truth']['paths'].items():pin(path,locked['inputs'][path])
    assert len(set(execution_ids))==90
    st=pd.DataFrame(settings);shared=st[st.shared]
    assert len(shared)==60 and shared.groupby('protocol_id').task_digest.nunique().eq(1).all()
    assert shared.groupby('protocol_id').task_checksum.nunique().eq(1).all()
    for field in ['environment_type','timeout_multiplier','override_timeout_sec','max_timeout_sec','agent_resume']:
        assert shared[field].nunique(dropna=False)==1,field
    assert shared.groupby(['model','harness']).harness_version.nunique().eq(1).all()
    assert set(st[st.harness.eq('Pi')].harness_version)=={'0.84.2'} and set(st[st.harness.eq('mini-SWE')].harness_version)=={'2.4.6'}
    csv(out/'settings_by_trial.csv',settings);dump(out/'runtime_settings.json',runtime)
    comparable=[]
    for pr,rs in st.groupby('protocol_id'):
        baseline=rs[rs.shared].task_digest.iloc[0]
        for row in rs.itertuples():comparable.append(dict(protocol_id=pr,group=row.group,task_digest=row.task_digest,shared_task_digest=baseline,same_task_digest=row.task_digest==baseline))
    csv(out/'task_compatibility.csv',comparable)
    keep=['row_id','group','model','harness','shared','protocol_id','case_id','trial','verifier','scorer_sha256','reference_set_id','required_families','valid_completion','historical_exception_type']+METRICS+['prediction_t2_path','prediction_t2_sha256','prediction_t3_path','prediction_t3_sha256','reference_t2_sha256','reference_t3_sha256','reference_transform']
    csv(out/'selected_scores.csv',d[keep])
    lock=dict(source_bundle=str(SOURCE),source_table_sha256=sha(SOURCE/'results.csv'),verifier='4.7.0',reference_set_id=locked['reference_set_id'],scorer_sha256=locked['scorer_sha256'],inputs=inputs,
              historical_selection=locked['historical_selection'],selection=[{k:getattr(r,k) for k in ['row_id','group','protocol_id','case_id','trial']} for r in d.itertuples()],
              shared_observations=60,native_additional_observations=30,attempts_per_model_harness_protocol=1,
              seed=SEED,bootstrap_resamples=DRAWS,bootstrap_seed_construction='SeedSequence([20261014, n_protocols]); identical indices within eligible panel',
              note='Source scores consumed exactly; no new scoring or inference. Temperatures/seeds/budgets absent from saved configurations are not assumed equal.')
    dump(out/'input_manifest.json',lock)
    return d,st,lock

def analyze(out,d,settings):
    primary=[];per=[];effects=[];per_effect=[];native=[];native_per=[];loo=[];long=[];weights=contrasts()
    for metric,label in zip(METRICS,LABELS):
        eligible=d[d.required_families.gt(0)] if metric==METRICS[0] else d
        pivot=eligible.pivot(index='protocol_id',columns=['model','harness'],values=metric).sort_index()
        protocols=pivot.index.tolist();a=np.stack([pivot[[(m,h) for h in HARNESSES]].to_numpy() for m in MODELS],axis=1);n=len(a)
        assert n==(9 if metric==METRICS[0] else 10) and np.isfinite(a).all()
        model_switch=np.mean(np.stack([abs(a[:,i,:]-a[:,j,:]) for i,j in combinations(range(3),2)],axis=1),axis=(1,2))
        harness_switch=abs(a[:,:,1]-a[:,:,0]).mean(axis=1);delta=model_switch-harness_switch
        for kind,v in [('model_switch',model_switch),('harness_switch',harness_switch),('model_minus_harness',delta)]:
            primary.append(dict(metric=metric,metric_label=label,effect=kind,test_family='primary_four_metrics',**summarize(v,kind=='model_minus_harness')))
        for i,pr in enumerate(protocols):
            per.append(dict(metric=metric,protocol_id=pr,model_switch=model_switch[i],harness_switch=harness_switch[i],difference=delta[i]))
            loo.append(dict(metric=metric,excluded_protocol=pr,full_difference=delta.mean(),leave_one_out_difference=np.delete(delta,i).mean()))
            for mi,m in enumerate(MODELS):
                for hi,h in enumerate(HARNESSES):long.append(dict(metric=metric,protocol_id=pr,model=m,harness=h,score=a[i,mi,hi]))
        for w in weights:
            v=a.reshape(n,6)@np.array([w[f'w{i}'] for i in range(6)])
            is_contrast='mean' not in w['kind']
            effects.append(dict(metric=metric,contrast=w['contrast'],kind=w['kind'],test_family=w['kind'],**summarize(v,is_contrast)))
            per_effect.extend(dict(metric=metric,protocol_id=pr,contrast=w['contrast'],kind=w['kind'],value=float(value)) for pr,value in zip(protocols,v))
        for m in MODELS:
            for h in HARNESSES:
                v=(pivot[(m,NATIVE[m])]-pivot[(m,h)]).to_numpy()
                contrast=f'{m}: {NATIVE[m]} - {h}'
                native.append(dict(metric=metric,model=m,native=NATIVE[m],shared_harness=h,contrast=contrast,panel='all_selected',test_family='native_24_contrasts',**summarize(v)))
                native_per.extend(dict(metric=metric,protocol_id=pr,model=m,contrast=contrast,value=float(value)) for pr,value in zip(protocols,v))
                compatible=[pr for pr in protocols if settings.query('protocol_id==@pr and model==@m').task_digest.nunique()==1]
                if len(compatible)<len(protocols):
                    cv=(pivot.loc[compatible,(m,NATIVE[m])]-pivot.loc[compatible,(m,h)]).to_numpy()
                    native.append(dict(metric=metric,model=m,native=NATIVE[m],shared_harness=h,contrast=contrast,panel='same_task_digest_only',test_family='native_task_compatibility',**summarize(cv)))
    csv(out/'primary_effects.csv',holm(primary,'test_family','signflip_p','signflip_p_holm'))
    csv(out/'paired_effects.csv',holm(effects,'test_family','signflip_p','signflip_p_holm'))
    csv(out/'native_contrasts.csv',holm(native,'test_family','signflip_p','signflip_p_holm'))
    for name,rows in [('protocol_switch_magnitudes',per),('protocol_paired_effects',per_effect),('protocol_native_contrasts',native_per),('leave_one_protocol_out',loo),('shared_long',long),('contrast_weights',weights)]:csv(out/f'{name}.csv',rows)

def verify(out,lock):
    p=pd.read_csv(out/'primary_effects.csv');d=pd.read_csv(out/'selected_scores.csv');paired=pd.read_csv(out/'paired_effects.csv');mixed=pd.read_csv(out/'mixed_effects.csv')
    joined=paired.merge(mixed,on=['metric','contrast','kind'],validate='one_to_one',suffixes=('_paired','_mixed'))
    assert len(joined)==len(paired)
    err=float(abs(joined.estimate_paired-joined.estimate_mixed).max());assert err<1e-10
    combined=joined.rename(columns={'estimate_paired':'estimate','ci_low':'protocol_bootstrap_ci_low','ci_high':'protocol_bootstrap_ci_high'})
    csv(out/'statistical_summary.csv',combined)
    pr=pd.read_csv(out/'protocol_switch_magnitudes.csv')
    for metric in METRICS:
        v=pr[pr.metric.eq(metric)]
        rr=p[p.metric.eq(metric)].set_index('effect')
        assert abs(rr.loc['model_minus_harness','estimate']-(rr.loc['model_switch','estimate']-rr.loc['harness_switch','estimate']))<1e-14
        assert np.allclose(v.difference,v.model_switch-v.harness_switch,rtol=0,atol=1e-14)
        for r in v.itertuples():
            block=d[d.protocol_id.eq(r.protocol_id)&d.shared.eq(True)].set_index(['model','harness'])[metric]
            expected=np.mean([abs(block[m1,h]-block[m2,h]) for m1,m2 in combinations(MODELS,2) for h in HARNESSES])-np.mean([abs(block[m,'Pi']-block[m,'mini-SWE']) for m in MODELS])
            assert abs(expected-r.difference)<1e-14
    # Independent balanced-design GLS covariance check of the nlme fit.
    long=pd.read_csv(out/'shared_long.csv');weights=pd.read_csv(out/'contrast_weights.csv');se_error=0.0
    for metric,rs in long.groupby('metric'):
        a=rs.pivot(index='protocol_id',columns=['model','harness'],values='score')[[(m,h) for m in MODELS for h in HARNESSES]].to_numpy();n=len(a)
        residual=a-a.mean(axis=0)-a.mean(axis=1)[:,None]+a.mean()
        sigma2=float((residual**2).sum()/((n-1)*5))
        tau2=float((6*((a.mean(axis=1)-a.mean())**2).sum()/(n-1)-sigma2)/6)
        assert tau2>0, 'Boundary fit requires a separate covariance check.'
        for r in weights.itertuples():
            w=np.asarray([getattr(r,f'w{i}') for i in range(6)])
            expected=np.sqrt((sigma2*np.dot(w,w)+tau2*w.sum()**2)/n)
            actual=mixed[mixed.metric.eq(metric)&mixed.contrast.eq(r.contrast)].mixed_se.iloc[0]
            se_error=max(se_error,abs(float(expected-actual)))
    assert se_error<1e-7
    assert all(sha(path)==h for path,h in lock['inputs'].items())
    source=load(SOURCE/'reproducibility_manifest.json')
    assert all(sha(SOURCE/name)==h for name,h in source['artifacts'].items())
    result=dict(status='passed',shared_observations=60,native_observations=30,protocols=10,informative_t2_protocols=9,
                protocol_blocks_preserved=True,shared_exact_model_ids_checked=True,shared_task_digests_match=True,
                primary_formula_independently_reconstructed=True,maximum_mixed_vs_paired_effect_error=err,
                mixed_standard_errors_independently_checked=88,maximum_mixed_standard_error_difference=se_error,
                all_locked_inputs_unchanged=True,all_source_bundle_artifacts_unchanged=True,
                source_bundle_artifacts_checked=len(source['artifacts']),agents_rerun=False,scorer_rerun=False,
                historical_selection_unchanged=True,ground_truth_edited=False)
    dump(out/'verification.json',result)
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();out=args.output.resolve()
    assert not out.exists(),'Use a new output directory; never overwrite prior results.'
    out.mkdir(parents=True)
    for f in HERE.iterdir():
        if f.suffix in ['.py','.R'] or f.name=='README.md': shutil.copy2(f,out/f.name)
    d,settings,lock=freeze(out);analyze(out,d,settings)
    subprocess.run(['Rscript',str(HERE/'fit_models.R'),str(out)],check=True)
    verify(out,lock)
    subprocess.run(['Rscript',str(HERE/'plot.R'),str(out)],check=True)
    stem=out/'figures/model_vs_harness_magnitudes'
    subprocess.run(['pdftocairo','-svg',str(stem.with_suffix('.pdf')),str(stem.with_suffix('.svg'))],check=True)
    subprocess.run(['pdftoppm','-r','180','-singlefile','-png',str(stem.with_suffix('.pdf')),str(stem)],check=True)
    subprocess.run([os.sys.executable,str(HERE/'report.py'),str(out)],check=True)
    artifacts={str(f.relative_to(out)):sha(f) for f in sorted(out.rglob('*')) if f.is_file() and f.name!='manifest.json'}
    dump(out/'manifest.json',dict(verifier='4.7.0',reference_set_id=lock['reference_set_id'],source_table_sha256=lock['source_table_sha256'],input_manifest_sha256=sha(out/'input_manifest.json'),
         artifacts=artifacts,code_hashes={f.name:sha(f) for f in HERE.iterdir() if f.suffix in ['.py','.R']},python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__,scipy=scipy.__version__,
         R=subprocess.check_output(['Rscript','-e','cat(R.version.string,"; nlme ",as.character(packageVersion("nlme")),"; ggplot2 ",as.character(packageVersion("ggplot2")),sep="")'],text=True),
         seed=SEED,bootstrap_resamples=DRAWS,reproduction='PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 python ../analyze.py --output ../results_reproduction',
         primary='Protocol means of within-protocol absolute model versus harness differences; paired percentile bootstrap',
         mixed_model='nlme::lme(score ~ model * harness, random=~1|protocol_id, method=REML); model-based Wald uncertainty secondary',
         blocked='Repeated-measures aov with Error(protocol_id/(model*harness)); Greenhouse-Geisser correction for 2-df factors',
         multiplicity='Exact sign-flip p-values Holm-adjusted within predeclared contrast types across four metrics; primary family=4; native family=24. Bootstrap CIs pointwise.',
         limitations=['9/10 protocol blocks, not 54/60 independent units','One selected output per cell; no run variability estimate','Factor levels are fixed tested configurations, not random samples of models or harnesses','Unmatched default token limits/implementation settings; model aliases lack immutable provider-weight revisions','Native Claude scRRBS has a different historical task digest; separate exclusion robustness retained']))
    print('Analysis complete:',out,flush=True)

if __name__=='__main__':main()
