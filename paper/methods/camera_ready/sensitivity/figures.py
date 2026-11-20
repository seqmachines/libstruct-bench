"""Supplement figures. Shading/boxes describe scoring assumptions, not runs."""
import os
import argparse
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parent/'.mplconfig'))
os.environ.setdefault('XDG_CACHE_HOME',str(Path(__file__).resolve().parent/'.cache'))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from evaluator import *

LABELS={'gpt_codex':'GPT · Codex','claude_code':'Claude · Claude Code','gemini_antigravity':'Gemini · Antigravity','kimi_code':'Kimi · Kimi Code',
 'gpt_pi':'GPT · Pi','gpt_mini_swe':'GPT · mini-SWE','claude_pi':'Claude · Pi','claude_mini_swe':'Claude · mini-SWE','gemini_pi':'Gemini · Pi','gemini_mini_swe':'Gemini · mini-SWE',
 'baseline':'Baseline','general_methods_v1':'General','cross_protocol_memory_v1':'Memory','general_methods_plus_memory_v1':'Combined',
 'c0':'C0','c30_pact':'C30 rules','c30_kb':'C30 + KB'}
SHORT={METRICS[0]:'Oligo-family F1',METRICS[1]:'Transition F1',METRICS[2]:'State F1',METRICS[3]:'Typed-edge F1'}
COLORS=['#1976a3','#e07a35','#6a9d45','#9566a8','#d85164','#588b83','#878334','#79645f','#7389c4','#dca247','#38928c','#a96284','#566675']

def save(fig,name):
    folder=OUT/'figures';folder.mkdir(exist_ok=True)
    for suffix in ['pdf','svg','png']:fig.savefig(folder/f'{name}.{suffix}',dpi=200,bbox_inches='tight',metadata={'Creator':'LibStructBench frozen-verifier sensitivity'} if suffix=='pdf' else None)
    plt.close(fig)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--priority-only',action='store_true');args=parser.parse_args()
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none'})
    a=pd.read_csv(OUT/'aggregates.csv');freq=pd.read_csv(OUT/'rank_first_frequencies.csv')
    a=a[a.variant=='corrected_4_7_0'];freq=freq[freq.variant=='corrected_4_7_0']
    # Priority panels: component-vector perturbations. Boxes are the distribution
    # over exactly 200 assumptions, diamonds are uniform, and vertical lines
    # are the range of individual +/-20% perturbations.
    fig,axes=plt.subplots(2,4,figsize=(13.6,7.2),layout='constrained')
    choices=[(METRICS[0],'all_protocols','t2'),(METRICS[0],'informative_only','t2'),(METRICS[1],'unchanged_panel','transition'),(METRICS[2],'unchanged_panel','state')]
    for ri,panel in enumerate(['fig3a','fig4a']):
      for ci,(metric,aggregation,family) in enumerate(choices):
        ax=axes[ri,ci];s=a[(a.panel==panel)&(a.metric==metric)&(a.aggregation==aggregation)&(a.family==family)]
        groups=list(dict.fromkeys(s.group));box=[s[(s.group==g)&(s.kind=='dirichlet')].delta.to_numpy() for g in groups]
        bp=ax.boxplot(box,positions=np.arange(len(groups)),widths=.5,showfliers=False,patch_artist=True,medianprops={'color':'black'})
        for patch,col in zip(bp['boxes'],COLORS):patch.set(facecolor=col,alpha=.55)
        for j,g in enumerate(groups):
            v=s[(s.group==g)&(s.kind=='individual')].delta;u=s[(s.group==g)&(s.kind=='uniform')].delta.iloc[0]
            ax.vlines(j+.32,v.min(),v.max(),color='black',linewidth=1.5);ax.scatter(j,u,marker='D',s=24,color=COLORS[j],edgecolor='black',linewidth=.4,zorder=3)
        ax.axhline(0,color='#555',lw=.6);ax.set_xticks(np.arange(len(groups)),[LABELS[g] for g in groups],rotation=45,ha='right');ax.set_ylabel('Change in macro-F1');ax.set_title(f'{panel.replace("fig","Fig. ")} · {SHORT[metric]}\n'+('Nreq > 0 only' if aggregation=='informative_only' else f'{family.capitalize()} weights; original panel'))
    fig.suptitle('Verifier 4.7.0 · Weight sensitivity: alternative scoring assumptions\nBoxes: 200 Dirichlet draws (IQR/median, 1.5×IQR whiskers); diamonds: uniform; black ranges: individual ±20%',fontsize=11)
    save(fig,'S1_weight_sensitivity_priority_panels')
    if args.priority_only:return
    # Rank-first frequency separately for each vector: ties count as first for
    # every tied system; fractional allocation is also available in the CSV.
    fig,axes=plt.subplots(3,4,figsize=(13.2,10.2),layout='constrained')
    for ri,panel in enumerate(['fig3a','fig4a','fig3b']):
      for ci,(metric,aggregation,_) in enumerate(choices):
        ax=axes[ri,ci];s=freq[(freq.panel==panel)&(freq.metric==metric)&(freq.aggregation==aggregation)&(freq.kind=='dirichlet')]
        groups=list(dict.fromkeys(s.group));p=s.pivot(index='group',columns='family',values='rank_first_frequency').reindex(index=groups,columns=['t2','transition','state'])
        ax.imshow(p.to_numpy(),vmin=0,vmax=1,cmap='Blues',aspect='auto')
        for y in range(len(groups)):
          for x in range(3):
            value=p.iloc[y,x];ax.text(x,y,f'{value*100:g}',ha='center',va='center',color='white' if value>.6 else '#222',fontsize=7)
        ax.set_xticks(range(3),['T2','Transition','State']);ax.set_yticks(range(len(groups)),[LABELS[g] for g in groups]);ax.set_xlabel('Weight vector varied');ax.set_title(f'{panel.replace("fig","Fig. ")} · {SHORT[metric]}'+('\nNreq > 0' if aggregation=='informative_only' else ''))
    fig.suptitle('Verifier 4.7.0 · Frequency ranked first (%) across 200 Dirichlet assumptions per vector\nCorrected verifier 4.7.0; each vector varied independently; these are not probabilities of winning a new model run',fontsize=11)
    save(fig,'S2_rank_first_frequencies')
    # Threshold maps quantify magnitude and ranking sensitivity across every
    # system in a panel, avoiding an arbitrary choice of one system's response.
    configs={c['config_id']:c for c in configurations()};corr=pd.read_csv(OUT/'ranking_correlations.csv');corr=corr[corr.variant=='corrected_4_7_0']
    fig,axes=plt.subplots(5,3,figsize=(10.2,13),layout='constrained')
    threshold_scale=max(.04,a[(a.family=='threshold')&(a.metric.isin(METRICS[1:]))].delta.abs().max())
    for ri,panel in enumerate(['fig3a','fig4a','fig3b','fig4c','fig4d']):
      for ci,metric in enumerate(METRICS[1:]):
        s=a[(a.panel==panel)&(a.metric==metric)&(a.family.isin(['threshold','original']))];matrix=np.zeros((3,3));reversals=np.zeros((3,3),int)
        for config,rs in s.groupby('config_id'):
            cfg=configs[config];y=[.15,.25,.4].index(cfg['state_threshold']);x=[.15,.25,.4].index(cfg['transition_threshold'])
            matrix[y,x]=rs.delta.abs().max();r=corr[(corr.panel==panel)&(corr.metric==metric)&(corr.config_id==config)];reversals[y,x]=int(r.reversed_pairs.iloc[0])
        ax=axes[ri,ci];im=ax.imshow(matrix,cmap='YlOrRd',vmin=0,vmax=threshold_scale,aspect='equal')
        for y in range(3):
          for x in range(3):ax.text(x,y,f'{matrix[y,x]:.3f}\n({reversals[y,x]} rev.)',ha='center',va='center',fontsize=8,color='white' if matrix[y,x]>.65*threshold_scale else '#222')
        ax.set_xticks(range(3),['.15','.25','.40']);ax.set_yticks(range(3),['.15','.25','.40']);ax.set_xlabel('Transition assignment floor');ax.set_ylabel('State assignment floor');ax.set_title(f'{panel.replace("fig","Fig. ")} · {SHORT[metric]}')
    fig.suptitle('Verifier 4.7.0 · Assignment-threshold sensitivity at original weights\nEach cell: largest absolute system/condition mean change; parentheses: pairwise rank reversals versus (.25, .25)',fontsize=11)
    fig.colorbar(im,ax=axes.ravel().tolist(),fraction=.025,pad=.025,label='Largest absolute macro-F1 change (shared scale)')
    save(fig,'S3_threshold_grid')
    # Full trajectory and held-out intervention. Ranges are pointwise envelopes
    # over scoring alternatives, expressly not confidence intervals.
    choices5=choices[:2]+[(k,'unchanged_panel','') for k in METRICS[1:]]
    fig,axes=plt.subplots(2,5,figsize=(15,6.8),layout='constrained')
    for ci,(metric,aggregation,_) in enumerate(choices5):
        ax=axes[0,ci];s=a[(a.panel=='fig4c')&(a.metric==metric)&(a.aggregation==aggregation)];groups=sorted(s.group.unique(),key=lambda g:int(g[1:]));x=np.array([int(g[1:]) for g in groups]);o=s[s.config_id=='original'].set_index('group').loc[groups,'mean']
        d=s[s.kind=='dirichlet'].groupby('group')['mean'].agg(['min','max']).loc[groups];t=s[s.family.isin(['threshold','original'])].groupby('group')['mean'].agg(['min','max']).loc[groups]
        ax.fill_between(x,d['min'].to_numpy(),d['max'].to_numpy(),color='#1976a3',alpha=.25,label='200 draws/vector envelope');ax.fill_between(x,t['min'].to_numpy(),t['max'].to_numpy(),facecolor='none',edgecolor='#e07a35',hatch='///',linewidth=.6,label='Threshold-grid envelope');ax.plot(x,o.to_numpy(),'o-',color='#222',markersize=3,label='Original weights/floors');ax.axvline(30,color='#9566a8',linestyle=':',label='Historical C30');ax.set_xticks([0,15,30,45,58]);ax.set_xlabel('Training protocols');ax.set_ylabel('Macro-F1');ax.set_title('Fig. 4c · '+SHORT[metric]+('\nNreq > 0' if aggregation=='informative_only' else ''))
        ax=axes[1,ci];s=a[(a.panel=='fig4d')&(a.metric==metric)&(a.aggregation==aggregation)];groups=['c0','c30_pact','c30_kb'];o=s[s.config_id=='original'].set_index('group').loc[groups,'mean'];d=s[s.kind=='dirichlet'].groupby('group')['mean'].agg(['min','max']).loc[groups];t=s[s.family.isin(['threshold','original'])].groupby('group')['mean'].agg(['min','max']).loc[groups]
        for j,g in enumerate(groups):
            ax.vlines(j-.05,d.loc[g,'min'],d.loc[g,'max'],color='#1976a3',lw=4);ax.vlines(j+.07,t.loc[g,'min'],t.loc[g,'max'],color='#e07a35',lw=2);ax.scatter(j,o.loc[g],color='#222',s=22,zorder=3)
        ax.set_xticks(range(3),[LABELS[g] for g in groups],rotation=35,ha='right');ax.set_ylabel('Macro-F1');ax.set_title('Fig. 4d · '+SHORT[metric]+('\nNreq > 0 (same ten)' if aggregation=='informative_only' else ''))
    handles,labels=axes[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='outside lower center',ncol=4,frameon=False)
    fig.suptitle('Verifier 4.7.0 · Learning trajectory and fixed C30 evaluation\nRanges quantify scoring-assumption sensitivity, not model-run variance or protocol-bootstrap confidence intervals',fontsize=11)
    save(fig,'S4_learning_sensitivity_envelopes')
    # Paired protocol bootstrap illustration: uniform-weight changes, all five
    # panels, one vector at a time, holding the other two vectors fixed.
    fig,axes=plt.subplots(5,3,figsize=(12,15),layout='constrained')
    for ri,panel in enumerate(['fig3a','fig4a','fig3b','fig4c','fig4d']):
      for ci,(metric,aggregation,family) in enumerate([choices[1],choices[2],choices[3]]):
        s=a[(a.panel==panel)&(a.metric==metric)&(a.aggregation==aggregation)&(a.family==family)&(a.kind=='uniform')];ax=axes[ri,ci]
        if panel=='fig4c':s=s.assign(order=s.group.str[1:].astype(int)).sort_values('order')
        for j,row in enumerate(s.itertuples()):
            ax.hlines(j,row.delta_ci_low,row.delta_ci_high,color=COLORS[j%len(COLORS)],lw=1.4);ax.scatter(row.delta,j,color=COLORS[j%len(COLORS)],s=18)
        ax.set_yticks(range(len(s)),[LABELS.get(g,g) for g in s.group]);ax.invert_yaxis();ax.axvline(0,color='#777',lw=.7);ax.set_xlabel('Uniform − original macro-F1');ax.set_title(f'{panel.replace("fig","Fig. ")} · {SHORT[metric]}'+('\nNreq > 0' if aggregation=='informative_only' else ''))
    fig.suptitle('Verifier 4.7.0 · Paired protocol-bootstrap 95% intervals for uniform weights\n5,000 paired resamples; uncertainty is across protocols, not independent model runs',fontsize=11)
    save(fig,'S5_uniform_weight_paired_intervals')
    print('Wrote five supplement figure sets (PDF, SVG, PNG).',flush=True)

if __name__=='__main__':main()
