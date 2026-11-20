"""Paired protocol bootstrap, rankings, rematching decomposition and drivers.

All bootstrap resampling is across the same protocols on both sides of a
contrast. Scoring configurations are never treated as replicated model runs.
"""
import csv
import argparse
import functools
import itertools
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import numpy as np
import pandas as pd
from scipy.stats import kendalltau
from evaluator import *
from run import rows,csvout,verify

PANELS=['fig3a','fig4a','fig3b','fig4c','fig4d']
TOL=1e-12
REPLICATES=5000

@functools.lru_cache(None)
def bootstrap_counts(n):
    rng=np.random.default_rng(np.random.SeedSequence([BOOTSTRAP_SEED,n]))
    indices=rng.integers(n,size=(REPLICATES,n))
    return np.array([np.bincount(row,minlength=n) for row in indices],dtype=float)/n

@functools.lru_cache(maxsize=250000)
def interval(values):
    x=np.array(values)
    if np.ptp(x)<1e-15:return float(x.mean()),float(x.mean())
    result=np.quantile(bootstrap_counts(len(x))@x,[.025,.975])
    return tuple(map(float,result))

def ranks(scores):
    scores=np.asarray(scores);order=np.argsort(-scores,kind='stable');rank=np.empty(len(scores));i=0
    while i<len(order):
        j=i+1
        while j<len(order) and abs(scores[order[j]]-scores[order[i]])<=TOL:j+=1
        rank[order[i:j]]=(i+1+j)/2;i=j
    return rank

def sign(x):return int(x>TOL)-int(x<-TOL)

def metrics():
    return [(METRICS[0],'all_protocols'),(METRICS[0],'informative_only')]+[(k,'unchanged_panel') for k in METRICS[1:]]

def stream_writer(path,fieldnames):
    handle=path.open('w',newline='');writer=csv.DictWriter(handle,fieldnames=fieldnames);writer.writeheader();return handle,writer

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase1',action='store_true',help='Analyze completed Figures 3a/4a first; final invocation without this flag replaces preliminary summaries.');args=parser.parse_args()
    manifest=load(OUT/'manifest.json');verify(manifest['inputs'])
    if args.phase1:
        manifest['panels']=[r for r in manifest['panels'] if r['panel'] in ['fig3a','fig4a']]
        selected_ids={r['case_id'] for r in manifest['panels']}
        manifest['cases']=[c for c in manifest['cases'] if c['case_id'] in selected_ids]
    configs=manifest['configurations'];config_ids=[c['config_id'] for c in configs]
    meta=pd.DataFrame(configs).drop(columns='weights')
    case_data=pd.read_csv(OUT.parent/'results.csv',low_memory=False)
    panels=pd.DataFrame(manifest['panels'])[['panel','group','protocol_id','trial','case_id','linked_valid']]
    data=case_data[case_data.panel.isin(PANELS)].copy().merge(meta,on='config_id',validate='many_to_one')
    # Includes every panel membership, with trial paths/IDs, actual fresh scores,
    # direct and residual deltas, entity counts, and assignment-change flags.
    data.to_csv(OUT/'per_protocol.csv',index=False,float_format='%.15g')
    checks=[load(OUT/'case_results'/f"{c['case_id']}.json") for c in manifest['cases']]
    validity=[]
    for (panel,group),rs in panels.groupby(['panel','group'],sort=False):
        valid=rs['linked_valid'].astype(str).str.lower().eq('true')
        validity.append(dict(panel=panel,group=group,selected_completions=len(rs),valid_completions=int(valid.sum()),invalid_completions=int((~valid).sum()),unique_predictions=rs.case_id.nunique()))
    csvout(OUT/'completion_counts.csv',validity)
    aggregates=[];correlations=[];first=[];reversal_summaries=[];drivers=[];rematching=[];contrasts=[]
    reversal_fields=['panel','variant','metric','aggregation','family','kind','config_id','group_a','group_b','baseline_difference','new_difference','status']
    reversal_file,reversal_writer=stream_writer(OUT/'pairwise_ranking_changes.csv',reversal_fields)
    for panel in PANELS:
      if panel not in set(panels.panel):continue
      for variant in manifest['variants']:
        part=data[(data.panel==panel)&(data.variant==variant)]
        groups=list(dict.fromkeys(part.group))
        if panel=='fig4c':groups=sorted(groups,key=lambda x:int(x[1:]))
        for metric,aggregation in metrics():
            selected=part[part.required_families>0] if aggregation=='informative_only' else part
            protocols=sorted(selected.protocol_id.unique());n=len(protocols)
            base=dict(panel=panel,variant=variant,metric=metric,aggregation=aggregation)
            values=np.stack([selected[selected.group==g].pivot(index='config_id',columns='protocol_id',values=metric).reindex(index=config_ids,columns=protocols).to_numpy() for g in groups])
            assert np.isfinite(values).all(),base
            means=values.mean(axis=2)
            rank_values=np.stack([ranks(means[:,j]) for j in range(len(configs))],axis=1)
            for gi,g in enumerate(groups):
                subset=selected[selected.group==g]
                direct=subset.pivot(index='config_id',columns='protocol_id',values=metric+'_direct_delta').reindex(index=config_ids,columns=protocols).to_numpy()
                residual=subset.pivot(index='config_id',columns='protocol_id',values=metric+'_rematching_delta').reindex(index=config_ids,columns=protocols).to_numpy()
                assert np.allclose(direct+residual,values[gi]-values[gi,0],rtol=0,atol=2e-12)
                for ci,config in enumerate(configs):
                    x=values[gi,ci];delta=x-values[gi,0];lo,hi=interval(tuple(x));dl,dh=interval(tuple(delta))
                    rl,rh=interval(tuple(residual[ci]))
                    aggregates.append(dict(**base,group=g,config_id=config['config_id'],family=config['family'],kind=config['kind'],n_protocols=n,
                        mean=means[gi,ci],ci_low=lo,ci_high=hi,baseline_mean=means[gi,0],delta=delta.mean(),delta_ci_low=dl,delta_ci_high=dh,
                        direct_delta=direct[ci].mean(),rematching_delta=residual[ci].mean(),rematching_ci_low=rl,rematching_ci_high=rh,
                        rank=rank_values[gi,ci],baseline_rank=rank_values[gi,0]))
                for family in [*WEIGHTS,'threshold']:
                    for kind in (['uniform','individual','dirichlet','all_weight_variants'] if family!='threshold' else ['threshold_grid']):
                        indices=[j for j,c in enumerate(configs) if (c['family']==family and (c['kind']==kind or kind in ['all_weight_variants','threshold_grid'])) or (kind=='threshold_grid' and j==0)]
                        if not indices:continue
                        diffs=values[gi,indices]-values[gi,0]
                        for pi,pid in enumerate(protocols):
                            j=max(indices,key=lambda k:abs(values[gi,k,pi]-values[gi,0,pi]))
                            drivers.append(dict(**base,group=g,family=family,kind=kind,protocol_id=pid,n_configs=len(indices),
                                baseline_score=values[gi,0,pi],min_delta=diffs[:,pi].min(),max_delta=diffs[:,pi].max(),
                                mean_absolute_delta=np.abs(diffs[:,pi]).mean(),max_absolute_delta=np.abs(diffs[:,pi]).max(),
                                largest_config=configs[j]['config_id'],largest_delta=values[gi,j,pi]-values[gi,0,pi],
                                contribution_to_macro_delta=(values[gi,j,pi]-values[gi,0,pi])/n,
                                direct_delta_at_largest=direct[j,pi],rematching_delta_at_largest=residual[j,pi]))
            for ci,config in enumerate(configs):
                oldrank=rank_values[:,0];newrank=rank_values[:,ci]
                tau=kendalltau(oldrank,newrank,variant='b').statistic
                if not np.isfinite(tau):tau=1. if np.array_equal(oldrank,newrank) else float('nan')
                changed_pairs=0;reversed_pairs=0
                for a,b in itertools.combinations(range(len(groups)),2):
                    old=means[a,0]-means[b,0];new=means[a,ci]-means[b,ci]
                    if sign(old)!=sign(new):
                        changed_pairs+=1;is_reversal=sign(old)*sign(new)==-1;reversed_pairs+=is_reversal
                        reversal_writer.writerow(dict(**base,family=config['family'],kind=config['kind'],config_id=config['config_id'],group_a=groups[a],group_b=groups[b],baseline_difference=old,new_difference=new,status='reversal' if is_reversal else ('tie_created' if sign(new)==0 else 'tie_resolved')))
                correlations.append(dict(**base,config_id=config['config_id'],family=config['family'],kind=config['kind'],tau_b=tau,reversed_pairs=reversed_pairs,changed_pairs=changed_pairs,possible_pairs=len(groups)*(len(groups)-1)//2))
            # CIs for principal paired system/condition contrasts, including all
            # system pairs in Figure 3 and each intervention versus its C0.
            pairs=list(itertools.combinations(range(len(groups)),2)) if panel.startswith('fig3') else [(i,groups.index('baseline' if panel=='fig4a' else ('C0' if panel=='fig4c' else 'c0'))) for i,g in enumerate(groups) if g!=('baseline' if panel=='fig4a' else ('C0' if panel=='fig4c' else 'c0'))]
            for a,b in pairs:
                original_difference=values[a,0]-values[b,0]
                for ci,config in enumerate(configs):
                    difference=values[a,ci]-values[b,ci];change=difference-original_difference
                    lo,hi=interval(tuple(difference));cl,ch=interval(tuple(change))
                    contrasts.append(dict(**base,group_a=groups[a],group_b=groups[b],config_id=config['config_id'],family=config['family'],kind=config['kind'],n_protocols=n,
                        mean_difference=difference.mean(),ci_low=lo,ci_high=hi,original_mean_difference=original_difference.mean(),
                        change_in_difference=change.mean(),change_ci_low=cl,change_ci_high=ch))
            for family in [*WEIGHTS,'threshold']:
              for kind in (['uniform','individual','dirichlet','all_weight_variants'] if family!='threshold' else ['threshold_grid']):
                indices=[j for j,c in enumerate(configs) if (c['family']==family and (c['kind']==kind or kind in ['all_weight_variants','threshold_grid'])) or (kind=='threshold_grid' and j==0)]
                if not indices:continue
                best=means[:,indices].max(axis=0);winner=np.abs(means[:,indices]-best)<=TOL
                for gi,g in enumerate(groups):
                    first.append(dict(**base,group=g,family=family,kind=kind,n_configs=len(indices),rank_first_count=int(winner[gi].sum()),rank_first_frequency=winner[gi].mean(),fractional_first_frequency=(winner[gi]/winner.sum(axis=0)).mean(),min_rank=rank_values[gi,indices].min(),max_rank=rank_values[gi,indices].max(),min_mean=means[gi,indices].min(),max_mean=means[gi,indices].max()))
                for a,b in itertools.combinations(range(len(groups)),2):
                    old=sign(means[a,0]-means[b,0]);signs=np.array([sign(means[a,j]-means[b,j]) for j in indices]);rev=signs*old==-1
                    reversal_summaries.append(dict(**base,family=family,kind=kind,group_a=groups[a],group_b=groups[b],n_configs=len(indices),original_difference=means[a,0]-means[b,0],reversal_count=int(rev.sum()),reversal_frequency=rev.mean(),tie_count=int((signs==0).sum()),min_difference=(means[a,indices]-means[b,indices]).min(),max_difference=(means[a,indices]-means[b,indices]).max()))
            print(panel,variant,metric,aggregation,'done',flush=True)
    reversal_file.close()
    for name,records in [('aggregates',aggregates),('ranking_correlations',correlations),('rank_first_frequencies',first),('pairwise_reversal_summary',reversal_summaries),('protocol_drivers',drivers),('paired_contrasts',contrasts)]:
        pd.DataFrame(records).to_csv(OUT/(name+'.csv'),index=False,float_format='%.15g')
    stability=pd.DataFrame(correlations).groupby(['panel','variant','metric','aggregation','family','kind']).agg(n_configs=('config_id','size'),minimum_tau_b=('tau_b','min'),median_tau_b=('tau_b','median'),configs_with_reversals=('reversed_pairs',lambda x:int((x>0).sum())),max_reversed_pairs=('reversed_pairs','max')).reset_index()
    stability.to_csv(OUT/'ranking_stability_summary.csv',index=False,float_format='%.15g')
    trajectory=[]
    curve=pd.DataFrame(aggregates);curve=curve[curve.panel=='fig4c']
    for (variant,metric,aggregation,config_id),part in curve.groupby(['variant','metric','aggregation','config_id']):
        part=part.assign(checkpoint=part['group'].str[1:].astype(int)).sort_values('checkpoint');y=part['mean'].to_numpy();delta=np.diff(y)
        increasing=bool((delta>=-TOL).all());decreasing=bool((delta<=TOL).all())
        trajectory.append(dict(variant=variant,metric=metric,aggregation=aggregation,config_id=config_id,nondecreasing=increasing,nonincreasing=decreasing,nonmonotonic=not increasing and not decreasing,endpoint_delta=y[-1]-y[0],up_steps=int((delta>TOL).sum()),down_steps=int((delta<-TOL).sum())))
    if trajectory:pd.DataFrame(trajectory).to_csv(OUT/'trajectory_monotonicity.csv',index=False,float_format='%.15g')
    # Count unique predictions (not reused figure memberships) with each layer
    # changed, separately by configuration/family rule.
    layers=[c for c in case_data.columns if c.endswith('_changed')]
    rematch=case_data.groupby(['variant','config_id'])[layers].sum().reset_index().merge(meta,on='config_id')
    rematch.to_csv(OUT/'rematching_counts.csv',index=False)
    zero=data[(data.required_families==0)][['panel','group','protocol_id','case_id','variant','config_id','family','kind','effective_predicted_families',METRICS[0]]]
    zero.to_csv(OUT/'zero_required_spurious_families.csv',index=False)
    # Effects of excluding zero-required protocols are distinct from weight
    # effects: this is a difference in estimand and denominator, not a paired
    # protocol contrast, so no misleading paired CI is assigned to that gap.
    a=pd.DataFrame(aggregates);a=a[(a.metric==METRICS[0])&(a.config_id=='original')]
    z=a.pivot(index=['panel','variant','group'],columns='aggregation',values='mean').reset_index();z['exclusion_delta']=z.informative_only-z.all_protocols
    z.to_csv(OUT/'zero_required_exclusion_baseline.csv',index=False)
    dump(OUT/'analysis_integrity.json',dict(phase='priority_panels_only' if args.phase1 else 'all_requested_panels',verified_inputs=verify(manifest['inputs']),unique_cases=len(checks),full_pipeline_scores=len(checks)*640,panel_config_rows=len(data),
        baseline_policy='4.7.0 with approved references; every baseline independently reproduced by an uncached call in ../baseline_validation.csv. Alternative rows are read from ../results.csv.',
        fixed_pair_identity_checks_passed=True,
        bootstrap_replicates=REPLICATES,bootstrap_seed=BOOTSTRAP_SEED,bootstrap_method='paired protocol percentile; common multinomial counts within n; no model-run resampling',
        numpy=np.__version__,pandas=pd.__version__,script_sha256=sha(__file__)))

if __name__=='__main__':main()
