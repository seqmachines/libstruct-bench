"""Camera-ready figure replacements, derived from the authoritative observation table."""
from tables import *
import os
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/libstruct-camera-ready-mpl')
os.environ.setdefault('XDG_CACHE_HOME','/private/tmp/libstruct-camera-ready-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import argparse
import subprocess

LABELS={'gpt_codex':'GPT · Codex','claude_code':'Claude · Claude Code','gemini_antigravity':'Gemini · Antigravity','kimi_code':'Kimi · Kimi Code',
 'gpt_pi':'GPT · Pi','gpt_mini_swe':'GPT · mini-SWE','claude_pi':'Claude · Pi','claude_mini_swe':'Claude · mini-SWE','gemini_pi':'Gemini · Pi','gemini_mini_swe':'Gemini · mini-SWE',
 'baseline':'Baseline','general_methods_v1':'General','cross_protocol_memory_v1':'Memory','general_methods_plus_memory_v1':'Combined','c0':'C0','c30_pact':'C30 rules','c30_kb':'C30 + KB'}
SHORT={METRICS[0]:'Oligo-family F1',METRICS[1]:'Transition F1',METRICS[2]:'State F1',METRICS[3]:'Typed-edge F1'}
COLORS=['#277da8','#cf6846','#5b9861','#8867a7']
files=[]
def native_original_style():
    """Render with the original paper's R design and locked current statistics."""
    dest=OUT/'figures';dest.mkdir(exist_ok=True)
    stem=dest/'fig3a_native_metrics'
    subprocess.run(['Rscript',str(OUT/'fig3a_original_style.R'),str(OUT/'aggregates.csv'),str(stem)],check=True)
    subprocess.run(['pdftocairo','-svg',str(stem.with_suffix('.pdf')),str(stem.with_suffix('.svg'))],check=True)
    subprocess.run(['pdftoppm','-r','200','-singlefile','-png',str(stem.with_suffix('.pdf')),str(stem)],check=True)
    for ext in ['pdf','svg','png']:
        p=stem.with_suffix('.'+ext)
        files.append(dict(path=str(p.relative_to(OUT)),sha256=sha(p),source='aggregates.csv panel=fig3a, policy=primary; original R design; Task2 n=19, Task3 n=20; paired protocol-bootstrap 95% intervals'))
def manifest():
    return dict(authoritative_table_sha256=sha(OUT/'results.csv'),script_sha256=sha(__file__),native_style_script_sha256=sha(OUT/'fig3a_original_style.R'),native_style_source=str(OUT.parent/'plot_fig3_baselines.R'),native_style_source_sha256=sha(OUT.parent/'plot_fig3_baselines.R'),heatmap_fig4_style_script_sha256=sha(OUT/'fig3b_fig4_original_style.R'),style_sources={name:sha(OUT.parent/name) for name in ['plot_fig3_baselines.R','plot_fig4_external_knowledge.R','plot_fig4cd_autonomous_improvement.R']},figures=files)
def save(fig,name,source):
    dest=OUT/'figures';dest.mkdir(exist_ok=True)
    for ext in ['pdf','svg','png']:
        p=dest/f'{name}.{ext}';fig.savefig(p,dpi=180,bbox_inches='tight',metadata={'Creator':'LibStructBench camera-ready verifier 4.7.0'} if ext=='pdf' else None);files.append(dict(path=str(p.relative_to(OUT)),sha256=sha(p),source=source))
    plt.close(fig)
def heatmaps_and_fig4_original_style(b):
    """Use original R plotting blocks with corrected scores and no T2 ddSEQ column."""
    metadata=pd.read_csv(OUT.parent/'fig3_trials_extracted.csv')
    metadata=metadata[['system_id','protocol_id','system_label','model','harness','protocol_label','protocol_order','display_order']].rename(columns={'system_id':'group','model':'model_annotation','harness':'harness_annotation'})
    rows=b[b.panel=='fig3b'][['group','protocol_id','required_families']+METRICS].merge(metadata,on=['group','protocol_id'],validate='one_to_one')
    rows['harness_annotation']=rows['group'].map({'gpt_codex':'Codex','claude_code':'Claude Code','gemini_antigravity':'Antigravity CLI'}).fillna(rows.harness_annotation)
    assert len(rows)==90
    rows.to_csv(OUT/'fig3b_plot_data.csv',index=False,float_format='%.17g')
    subprocess.run(['Rscript',str(OUT/'fig3b_fig4_original_style.R'),str(OUT)],check=True)
    sources={
        'fig3b_t2_heatmap':'results.csv panel=fig3b, config_id=original, metric=t2_required_family_f1, required_families>0; 9 protocols',
        'fig3b_t3_heatmap':'results.csv panel=fig3b, config_id=original, metric=t3_molecular_transition_f1; all 10 protocols',
        'fig3b_t3_state_heatmap':'results.csv panel=fig3b, config_id=original, metric=t3_state_f1; all 10 protocols',
        'fig3b_t3_typed_edge_heatmap':'results.csv panel=fig3b, config_id=original, metric=t3_typed_edge_f1; all 10 protocols',
        'fig4a_external_knowledge_pilot':'aggregates.csv panel=fig4a, policy=primary; original condition-grouped style; Task2 n=4, Task3 n=5; protocol-bootstrap intervals',
        'fig4c_agent_learning_curve':'aggregates.csv panel=fig4c, policy=primary; original line/marker style without shading; all intervals retained in table; historical C30 unchanged',
        'fig4d_test_comparison':'aggregates.csv panel=fig4d, policy=primary; original condition-grouped style; all metrics n=10; protocol-bootstrap intervals',
    }
    for name,source in sources.items():
        stem=OUT/'figures'/name
        subprocess.run(['pdftocairo','-svg',str(stem.with_suffix('.pdf')),str(stem.with_suffix('.svg'))],check=True)
        subprocess.run(['pdftoppm','-r','200','-singlefile','-png',str(stem.with_suffix('.pdf')),str(stem)],check=True)
        for ext in ['pdf','svg','png']:
            p=stem.with_suffix('.'+ext);files.append(dict(path=str(p.relative_to(OUT)),sha256=sha(p),source=source))

def main():
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group();mode.add_argument('--fig3a-only',action='store_true');mode.add_argument('--fig3b-fig4-only',action='store_true');args=parser.parse_args()
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'svg.fonttype':'none'})
    b=original();a=pd.read_csv(OUT/'aggregates.csv')
    if not args.fig3b_fig4_only:
        native_original_style()
    if args.fig3a_only:
        previous=load(OUT/'figure_manifest.json')
        files.extend(r for r in previous['figures'] if Path(r['path']).stem!='fig3a_native_metrics')
        dump(OUT/'figure_manifest.json',manifest())
        print('Updated Fig. 3a only; all other figure bytes unchanged.',flush=True)
        return
    heatmaps_and_fig4_original_style(b)
    if args.fig3b_fig4_only:
        previous=load(OUT/'figure_manifest.json');updated={r['path'] for r in files}
        files.extend(r for r in previous['figures'] if r['path'] not in updated)
        dump(OUT/'figure_manifest.json',manifest())
        print('Updated four Fig. 3 heatmaps and Fig. 4a/c/d only.',flush=True)
        return
    protlabel={r.protocol_id:r.protocol_label for r in b[b.panel=='fig3common10'].itertuples()}
    ep=pd.read_csv(OUT/'error_profiles.csv');cats=['missing_recoverable_information','unsupported_completion','strand_or_orientation_error','molecular_state_or_assembly_error','operation_error','workflow_or_topology_error'];catlabels=['Missing\nrecoverable','Unsupported\naddition','Strand /\norientation','State /\nassembly','Operation','Workflow /\ntopology']
    for scope,name in [('system','fig3c_error_profiles'),('protocol','fig3c_protocol_error_profiles')]:
        v=ep[ep.scope==scope].pivot(index='entity',columns='category',values='prevalence').reindex(columns=cats);fig,ax=plt.subplots(figsize=(8,5.5),layout='constrained');im=ax.imshow(v,vmin=0,vmax=1,cmap='Oranges',aspect='auto')
        for y in range(len(v)):
            for x in range(6):ax.text(x,y,f'{v.iloc[y,x]:.2f}',ha='center',va='center',color='white' if v.iloc[y,x]>.7 else '#222')
        ax.set_xticks(range(6),catlabels);ax.set_yticks(range(len(v)),[LABELS.get(s,protlabel.get(s,s)) for s in v.index]);ax.set_title('Deterministic output-discrepancy prevalence · verifier 4.7.0\nCategories overlap; attribution and biological validity are unadjudicated',fontsize=10);fig.colorbar(im,ax=ax,shrink=.7,label='Fraction of '+('10 protocols' if scope=='system' else '10 systems'));save(fig,name,f'error_profiles.csv scope={scope}')
    cost=pd.read_csv(OUT/'cost_performance.csv').sort_values('mean_cost_usd');fig,ax=plt.subplots(figsize=(8,4.3),layout='constrained');x=np.arange(len(cost));ax.barh(x,cost.mean_cost_usd,color='#277da8');ax.errorbar(cost.mean_cost_usd,x,xerr=[cost.mean_cost_usd-cost.cost_ci_low,cost.cost_ci_high-cost.mean_cost_usd],fmt='none',ecolor='#333',capsize=2,lw=.8);ax.set_yticks(x,[LABELS[g] for g in cost.group]);ax.set_xlabel('Estimated API cost per protocol (USD)');ax.set_title('Historical token/pricing snapshot · same selected ten-protocol panel\nProtocol-bootstrap 95% intervals; not billed provider charges',fontsize=10);save(fig,'fig3d_cost_comparison','cost_performance.csv, costs preserved from locked historical trials/prices')
    fig,ax=plt.subplots(figsize=(9,5.4),layout='constrained')
    for i,r in enumerate(cost.itertuples()):
        ax.errorbar(r.mean_cost_usd,r.mean_performance,yerr=[[r.mean_performance-r.performance_ci_low],[r.performance_ci_high-r.mean_performance]],fmt='o',color=COLORS[i%4],capsize=2,ms=5)
        ax.annotate(LABELS[r.group],(r.mean_cost_usd,r.mean_performance),xytext=(5,9 if i%2==0 else -14),textcoords='offset points',fontsize=7)
    ax.set_xscale('log');ax.set_xlim(cost.mean_cost_usd.min()*.75,cost.mean_cost_usd.max()*1.65);ax.set_xlabel('Estimated API cost per protocol (USD; log scale)');ax.set_ylabel('Equal mean of four reported macro-F1 metrics');ax.set_title('Cost versus corrected performance · Task 2 informative only\nExploratory display composite; not reward or checkpoint-selection score',fontsize=10);save(fig,'fig3e_cost_performance','cost_performance.csv; Task2 n=9, Task3 n=10; paired protocol resampling')
    t=b[b.panel=='task1'];pairs=pd.read_csv(OUT/'task1_paired_contrasts.csv')
    for other,name,xlab,ylab in [('Agent','fig2_task1_api_vs_coding_agent','Direct API (PDF)','Native agent (PDF)'),('NameOnly','figS_task1_name_only','Direct API (PDF)','Direct API (name only)')]:
        fig,axes=plt.subplots(1,3,figsize=(10.5,3.7),layout='constrained')
        for ax,fam,col in zip(axes,['Claude','GPT','Gemini'],COLORS):
            a1=t[t.group==fam+'_API'].set_index('protocol_id');v=t[t.group==fam+'_'+other].set_index('protocol_id').loc[a1.index];ax.scatter(a1.sequence_similarity,v.sequence_similarity,color=col,s=25,alpha=.7,edgecolors='white',linewidth=.4);ax.plot([0,1],[0,1],color='#888',ls='--',lw=.7);ax.set(xlim=(-.04,1.04),ylim=(-.04,1.04),xlabel=xlab,ylabel=ylab);ax.set_aspect('equal');r=pairs[(pairs.family==fam)&(pairs.contrast==('agent_minus_api' if other=='Agent' else 'name_only_minus_pdf'))].iloc[0];ax.set_title(f'{fam} · n=20\nΔ={r.difference:+.3f} [{r.ci_low:+.3f}, {r.ci_high:+.3f}]',fontsize=9)
        fig.suptitle('Task 1 · approved references; unchanged string scorer\n'+('Matched versions; includes empty generated outputs as zero' if other=='Agent' else 'One completed attempt/case; Gemini routes differ (PDF: OpenRouter; name only: Vertex)'),fontsize=10);save(fig,name,f'results.csv panel=task1; task1_paired_contrasts.csv other={other}')
    rep=pd.read_csv(OUT/'repeated_run_summary.csv').query("policy == 'primary'").set_index('metric').loc[METRICS];fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained');x=np.arange(4)
    for j,(col,label) in enumerate([('original_mean','Original'),('repeat_earlier_mean','Earlier repeat'),('repeat_later_mean','Later repeat'),('mean_three_attempts','Three-attempt mean')]):axes[0].plot(x,rep[col],'o-',color=COLORS[j],label=label,ms=4)
    axes[0].set_xticks(x,['Oligo','Transition','State','Edge']);axes[0].set_ylabel('Protocol macro-F1');axes[0].legend(frameon=False,fontsize=7);axes[0].set_title('Completed native Codex attempts')
    axes[1].errorbar(x,rep.n3_minus_original,yerr=[rep.n3_minus_original-rep.delta_ci_low,rep.delta_ci_high-rep.n3_minus_original],fmt='o',color='#277da8',capsize=3);axes[1].axhline(0,color='#888',lw=.7);axes[1].set_xticks(x,['Oligo','Transition','State','Edge']);axes[1].set_ylabel('Three-attempt mean − original');axes[1].set_title('Paired protocol-bootstrap 95% intervals');fig.suptitle('Observed repeated-execution variability · verifier 4.7.0\nThree attempts/protocol; Task 2 n=19, Task 3 n=20; not controlled random seeds',fontsize=10);save(fig,'figS_codex_repeated_runs','repeated_run_summary.csv policy=primary; all 60 saved attempts, no best-of-three')
    z=pd.read_csv(OUT/'zero_required.csv');fig,axes=plt.subplots(1,3,figsize=(11,3.5),layout='constrained')
    for ax,panel in zip(axes,['fig3a','fig4a','fig4c']):
        rs=z[z.panel==panel].copy()
        if panel=='fig4c':rs['order']=rs.group.str[1:].astype(int);rs=rs.sort_values('order')
        ax.bar(range(len(rs)),rs.effective_spurious_families,color='#8867a7');ax.set_xticks(range(len(rs)),[LABELS.get(g,g) for g in rs.group],rotation=60,ha='right',fontsize=7);ax.set_ylabel('Effective spurious families');ax.set_title(panel.replace('fig','Fig. ')+' · ddSEQ');ax.set_ylim(0,max(1.2,rs.effective_spurious_families.max()+.3))
    fig.suptitle('Zero-required Task 2 protocol · counts reported outside primary macro-F1',fontsize=10);save(fig,'figS_zero_required','zero_required.csv panels=fig3a,fig4a,fig4c')
    dump(OUT/'figure_manifest.json',manifest())
    print(f'{len(files)//3} main/supplement figure sets generated from locked table.',flush=True)

if __name__=='__main__':main()
