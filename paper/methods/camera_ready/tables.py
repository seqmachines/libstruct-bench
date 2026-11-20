"""All numerical manuscript/figure views derive solely from locked results.csv."""
from freeze import *
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from functools import lru_cache
from itertools import combinations,product

METRICS=['t2_required_family_f1','t3_molecular_transition_f1','t3_state_f1','t3_typed_edge_f1']
PANELS=['fig3a','fig3b','fig4a','fig4c','fig4d','fig3common10']
SEED=20261013
@lru_cache(None)
def samples(n,draws=5000,seed=SEED):
    return np.random.default_rng(np.random.SeedSequence([seed,n])).integers(n,size=(draws,n))
def ci(x,draws=5000,seed=SEED):
    x=np.asarray(x,float);assert len(x) and np.isfinite(x).all()
    return np.quantile(x[samples(len(x),draws,seed)].mean(axis=1),[.025,.975])
def records(path,rows):pd.DataFrame(rows).to_csv(OUT/path,index=False,float_format='%.17g')
def original():
    ident=load(OUT/'results_identity.json');assert sha(OUT/'results.csv')==ident['sha256']
    frame=pd.read_csv(OUT/'results.csv',low_memory=False);return frame[frame.config_id=='original'].copy()
def selected(frame,metric,policy='primary'):
    return frame[frame.required_families>0] if metric==METRICS[0] and policy=='primary' else frame
def main():
    b=original();summaries=[];contrasts=[];differences=[]
    for (panel,group),rs in b[b.panel.isin(PANELS)].groupby(['panel','group'],sort=False):
        for metric,policy in [(m,'primary') for m in METRICS]+[(METRICS[0],'all_protocols'),('reward','all_protocols'),('t2_exact_required_family_recall','all_protocols')]:
            v=selected(rs,metric,policy).sort_values('protocol_id');lo,hi=ci(v[metric]);mean=v[metric].mean()
            summaries.append(dict(summary_id=f'{panel}:{group}:{metric}:{policy}',panel=panel,group=group,metric=metric,policy=policy,n_protocols=len(v),mean=mean,ci_low=lo,ci_high=hi))
            for prefix,change in [('frozen_460_','total_correction'),('frozen_470_','approved_reference_update')]:
                d=v[metric]-v[prefix+metric];dl,dh=ci(d)
                differences.append(dict(panel=panel,group=group,metric=metric,policy=policy,comparison=change,n_protocols=len(v),original_mean=v[prefix+metric].mean(),corrected_mean=mean,delta=d.mean(),delta_ci_low=dl,delta_ci_high=dh))
    aggregate=pd.DataFrame(summaries)
    aggregate['rank']=aggregate.groupby(['panel','metric','policy'])['mean'].rank(method='average',ascending=False)
    aggregate.to_csv(OUT/'aggregates.csv',index=False,float_format='%.17g');records('score_changes.csv',differences)
    ranking=pd.DataFrame(differences)
    by=['panel','metric','policy','comparison']
    ranking['original_rank']=ranking.groupby(by).original_mean.rank(method='average',ascending=False)
    ranking['corrected_rank']=ranking.groupby(by).corrected_mean.rank(method='average',ascending=False)
    ranking['rank_improvement']=ranking.original_rank-ranking.corrected_rank
    ranking.to_csv(OUT/'ranking_changes.csv',index=False,float_format='%.17g')
    for panel,part in b[b.panel.isin(PANELS)].groupby('panel'):
        groups=list(dict.fromkeys(part.group))
        for metric in METRICS:
            for g1,g2 in combinations(groups,2):
                left=selected(part[part.group==g1],metric).set_index('protocol_id')[metric]
                right=selected(part[part.group==g2],metric).set_index('protocol_id')[metric]
                assert set(left.index)==set(right.index)
                d=(right-left).sort_index();lo,hi=ci(d)
                contrasts.append(dict(contrast_id=f'{panel}:{g2}-minus-{g1}:{metric}',panel=panel,group_a=g1,group_b=g2,metric=metric,n_protocols=len(d),b_minus_a=d.mean(),ci_low=lo,ci_high=hi))
    records('paired_contrasts.csv',contrasts)
    zero=b[(b.required_families==0)&b.panel.isin(PANELS+['codex_repeats'])]
    zero[['row_id','panel','group','protocol_id','predicted_families','neutralized_families','effective_spurious_families',METRICS[0]]].to_csv(OUT/'zero_required.csv',index=False)
    counts=[]
    for (p,g),rs in b.groupby(['panel','group'],sort=False):
        valid=rs.valid_completion.eq(True)
        counts.append(dict(panel=p,group=g,selected_completed_attempts=len(rs),valid_outputs=int(valid.sum()),invalid_or_empty_outputs=int((~valid).sum()),abstentions=int(rs.abstention.eq(True).sum()),recorded_exceptions=int(rs.historical_exception_type.notna().sum()),missing_selected_attempts=0))
    records('completion_counts.csv',counts)
    errorcols=[c for c in b if c.startswith('discrepancy_')]
    er=b[b.panel=='fig3common10'];errs=[]
    for scope,column in [('system','group'),('protocol','protocol_id')]:
        for entity,rs in er.groupby(column):
            for col in errorcols:errs.append(dict(scope=scope,entity=entity,category=col.removeprefix('discrepancy_'),with_discrepancy=int(rs[col].sum()),n=len(rs),prevalence=rs[col].mean(),adjudication='unreviewed deterministic output discrepancy'))
    records('error_profiles.csv',errs)
    # Cost is historical token-priced cost, same selected trials and price snapshot.
    costs=[]
    for group,rs in er.groupby('group'):
        v=rs.sort_values('protocol_id');boot=samples(len(v));elig=v.required_families.to_numpy()>0
        scores=v[METRICS].to_numpy();boot_scores=scores[boot]
        t2=np.where(elig[boot],boot_scores[:,:,0],np.nan)
        four=(np.nanmean(t2,axis=1)+boot_scores[:,:,1:].mean(axis=1).sum(axis=1))/4
        perf=(scores[elig,0].mean()+scores[:,1:].mean(axis=0).sum())/4
        lo,hi=np.quantile(four,[.025,.975]);cl,ch=ci(v.normalized_api_cost_usd)
        costs.append(dict(group=group,n_cost_protocols=len(v),n_t2_informative=int(elig.sum()),mean_cost_usd=v.normalized_api_cost_usd.mean(),cost_ci_low=cl,cost_ci_high=ch,mean_performance=perf,performance_ci_low=lo,performance_ci_high=hi,definition='equal mean of informative T2 macro and three unchanged-panel T3 macros; not reward or checkpoint criterion',pricing_snapshot_id=v.pricing_snapshot_id.iloc[0]))
    for c in costs:c['pareto_efficient']=not any(o['mean_cost_usd']<=c['mean_cost_usd'] and o['mean_performance']>=c['mean_performance'] and (o['mean_cost_usd']<c['mean_cost_usd'] or o['mean_performance']>c['mean_performance']) for o in costs)
    records('cost_performance.csv',costs)
    repeats=[];per_repeats=[]
    rep=b[b.panel=='codex_repeats']
    for metric,policy in [(m,'primary') for m in METRICS]+[(METRICS[0],'all_protocols')]:
        rs=selected(rep,metric,policy);v=rs.pivot(index='protocol_id',columns='group',values=metric).sort_index();a=v[['attempt_1','attempt_2','attempt_3']].to_numpy();n=len(a)
        mean=a.mean(axis=1);diff=mean-a[:,0];lo,hi=ci(mean,100000,20261011);dl,dh=ci(diff,100000,20261011)
        rng=np.random.default_rng(20261011);choices=rng.integers(0,3,size=(100000,n));observed=a[np.arange(n),choices].mean(axis=1);sd=a.std(axis=1,ddof=1)
        repeats.append(dict(metric=metric,policy=policy,n_protocols=n,attempts_per_protocol=3,original_mean=a[:,0].mean(),repeat_earlier_mean=a[:,1].mean(),repeat_later_mean=a[:,2].mean(),mean_three_attempts=mean.mean(),ci_low=lo,ci_high=hi,n3_minus_original=diff.mean(),delta_ci_low=dl,delta_ci_high=dh,rms_within_protocol_sd=np.sqrt(np.mean(sd**2)),mean_protocol_range=np.ptp(a,axis=1).mean(),max_protocol_range=np.ptp(a,axis=1).max(),observed_one_per_protocol_low=np.quantile(observed,.025),observed_one_per_protocol_high=np.quantile(observed,.975),observed_one_per_protocol_sd=observed.std(ddof=1),protocols_range_gt_point1=int((np.ptp(a,axis=1)>.1).sum())))
        for i,pr in enumerate(v.index):per_repeats.append(dict(protocol_id=pr,metric=metric,policy=policy,attempt_1=a[i,0],attempt_2=a[i,1],attempt_3=a[i,2],mean=mean[i],sample_sd=sd[i],range=np.ptp(a[i])))
    records('repeated_run_summary.csv',repeats);records('repeated_run_per_protocol.csv',per_repeats)
    # Task 1: end-to-end exact rates include actual empty generated outputs.
    t1=b[b.panel=='task1'];t1summary=[];t1pairs=[]
    for group,rs in t1.groupby('group'):
        lo,hi=ci(rs.sequence_similarity,100000,20261010)
        t1summary.append(dict(group=group,n=len(rs),valid=int(rs.valid_completion.eq(True).sum()),abstentions=int(rs.abstention.eq(True).sum()),mean=rs.sequence_similarity.mean(),ci_low=lo,ci_high=hi,exact_count=int(rs.exact_match.eq(True).sum()),exact_rate=rs.exact_match.eq(True).mean()))
    for family in ['Claude','GPT','Gemini']:
        for other,label in [('Agent','agent_minus_api'),('NameOnly','name_only_minus_pdf')]:
            a=t1[t1.group==family+'_API'].set_index('protocol_id');v=t1[t1.group==family+'_'+other].set_index('protocol_id');d=(v.sequence_similarity-a.sequence_similarity).sort_index();lo,hi=ci(d,100000,20261010);rounded=np.round(d.to_numpy(),12);z=rounded[rounded!=0]
            ranks=rankdata(abs(z));observed=abs(np.dot(np.sign(z),ranks));p=sum(abs(np.dot(signs,ranks))>=observed-1e-12 for signs in product([-1,1],repeat=len(z)))/(2**len(z))
            t1pairs.append(dict(family=family,contrast=label,n=len(d),difference=d.mean(),ci_low=lo,ci_high=hi,api_or_pdf_wins=int((rounded<0).sum()),other_wins=int((rounded>0).sum()),ties=int((rounded==0).sum()),wilcoxon_sign_permutation_p=p))
    for label in ['agent_minus_api','name_only_minus_pdf']:
        ordered=sorted([r for r in t1pairs if r['contrast']==label],key=lambda r:r['wilcoxon_sign_permutation_p']);adjusted=0
        for i,r in enumerate(ordered):adjusted=max(adjusted,min(1,(3-i)*r['wilcoxon_sign_permutation_p']));r['holm_p']=adjusted
    records('task1_summary.csv',t1summary);records('task1_paired_contrasts.csv',t1pairs)
    dump(OUT/'table_derivation_manifest.json',dict(source='results.csv',source_sha256=sha(OUT/'results.csv'),script_sha256=sha(__file__),protocol_bootstrap=5000,seed=SEED,task1_and_repeated_bootstrap=100000,task1_seed=20261010,repeat_seed=20261011,weight_samples_are_model_repeats=False))
    print('Derived primary aggregates, paired contrasts, historical comparisons, completion counts, cost/error views, Task 1 and repeat statistics.',flush=True)

if __name__=='__main__':main()
