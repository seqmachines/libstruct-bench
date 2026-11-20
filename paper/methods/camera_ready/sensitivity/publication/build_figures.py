"""Publish saved sensitivity results; never import or call the scorer.

Run: python analysis/paper_results/camera_ready_v4_7_0/sensitivity/publication_20261017/build_figures.py
Only this new output directory is written. Source tables and main.tex are hashed.
"""
from pathlib import Path
import hashlib
import json
import os

OUT = Path(__file__).resolve().parent
SRC = OUT.parent
PAPER = Path('external/manuscript')
os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/libstruct-sensitivity-publication-mpl')
os.environ.setdefault('XDG_CACHE_HOME', '/private/tmp/libstruct-sensitivity-publication-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import MultipleLocator, FormatStrFormatter
import numpy as np
import pandas as pd

METRICS = ['t2_required_family_f1', 't3_molecular_transition_f1',
           't3_state_f1', 't3_typed_edge_f1']
SHORT = ['Oligo', 'Transition', 'State', 'Typed edge']
GROUPS = ['gpt_codex', 'claude_code', 'gemini_antigravity', 'kimi_code']
LABELS = ['GPT/Codex', 'Claude Code', 'Gemini/Antigravity', 'Kimi Code']
COLORS = ['#277da8', '#cf6846', '#5b9861', '#8867a7']
FILENAMES = ['aggregates.csv', 'rank_first_frequencies.csv',
             'paired_contrasts.csv', 'trajectory_monotonicity.csv',
             'zero_required_exclusion_baseline.csv', 'zero_required_spurious_families.csv',
             'ranking_correlations.csv', 'manifest.json', 'sensitivity_report.md']

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def primary(frame):
    frame = frame[frame.variant == 'corrected_4_7_0']
    return frame[frame.aggregation != 'all_protocols'] if 'aggregation' in frame else frame

def one(frame):
    assert len(frame) == 1, f'Expected one row, got {len(frame)}'
    return frame.iloc[0]

def export(frame, filename):
    frame.to_csv(OUT / filename, index=False, float_format='%.15g')

def save(fig, filename):
    fig.savefig(OUT / filename, format='pdf', facecolor='white', metadata={
        'Title': filename.removesuffix('.pdf').replace('_', ' '),
        'Creator': 'LibStructBench: plotting of frozen sensitivity tables',
        'CreationDate': None, 'ModDate': None})
    plt.close(fig)

def clean_axes(ax):
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.spines['bottom'].set_color('#777777')
    ax.tick_params(axis='y', length=0)
    ax.grid(axis='x', color='#e5e5e5', linewidth=.5)
    ax.set_axisbelow(True)

def figure_a(a, freq):
    native = a[a.panel == 'fig3a']
    families = ['t2', 'transition', 'state']
    records = []
    fig = plt.figure(figsize=(5.5, 2.20))
    grid = fig.add_gridspec(1, 3, left=.215, right=.985, bottom=.165, top=.744, wspace=.25)
    for j, (metric, family) in enumerate(zip(METRICS[:3], families)):
        ax = fig.add_subplot(grid[0, j])
        s = native[native.metric == metric]
        for y, (group, color) in enumerate(zip(GROUPS, COLORS)):
            default = one(s[(s.group == group) & (s.config_id == 'original')])
            uniform = one(s[(s.group == group) & (s.family == family) & (s.kind == 'uniform')])
            draws = s[(s.group == group) & (s.family == family) & (s.kind == 'dirichlet')]
            assert len(draws) == 200 and draws.config_id.nunique() == 200
            f = one(freq[(freq.panel == 'fig3a') & (freq.metric == metric) &
                         (freq.family == family) & (freq.kind == 'dirichlet') & (freq.group == group)])
            lo, hi = draws['mean'].min(), draws['mean'].max()
            records.append(dict(group=group, metric=metric, varied_vector=family,
                                n_protocols=int(default.n_protocols), default=default['mean'],
                                uniform=uniform['mean'], sample_min=lo, sample_max=hi,
                                first_count=int(f.rank_first_count), samples=200,
                                first_frequency=f.rank_first_frequency))
            ax.plot([lo, hi], [y, y], color=color, lw=2.2, alpha=.72, solid_capstyle='butt')
            ax.plot([lo, hi], [y, y], '|', color=color, ms=5, mew=.9)
            ax.scatter(default['mean'], y, s=28, color=color, edgecolors='white', linewidths=.5, zorder=4)
            ax.scatter(uniform['mean'], y, s=26, marker='D', facecolors='white', edgecolors=color, linewidths=1, zorder=5)
        ax.set_ylim(3.5, -.5)
        ax.set_yticks(range(4), LABELS if j == 0 else [])
        ax.tick_params(axis='y', labelsize=8)
        ax.set_xlabel('Macro-F1', labelpad=3)
        ax.set_title(['Oligo F1', 'Transition F1', 'State F1'][j], fontsize=9, pad=17)
        ax.text(.5, 1.045, f'{family.capitalize()} weights; n = {19 if j == 0 else 20}',
                ha='center', transform=ax.transAxes, fontsize=7.2, color='#555555')
        ax.set_xlim([(0.63, .82), (.74, .84), (.44, .78)][j])
        ax.xaxis.set_major_locator(MultipleLocator([.05, .05, .10][j]))
        ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
        clean_axes(ax)
    handles = [Line2D([], [], marker='o', color='#444444', ls='None', ms=4, label='Default'),
               Line2D([], [], marker='D', color='#444444', markerfacecolor='white', ls='None', ms=4, label='Uniform'),
               Line2D([], [], color='#777777', lw=2, label='200-sample scoring-sensitivity range (not CI)')]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.52, .997), ncol=3,
               frameon=False, fontsize=7, handlelength=1.5, columnspacing=1.2)
    rows = pd.DataFrame(records)
    export(rows, 'figure_A_data.csv')
    save(fig, 'figS_sensitivity_weights.pdf')
    return rows

def figure_b(a, manifest):
    config = {x['config_id']: x for x in manifest['configurations']}
    thresholds = [.15, .25, .40]
    t = a[a.family.isin(['threshold', 'original']) & a.metric.isin(METRICS[1:])].copy()
    assert t.config_id.nunique() == 9
    t['abs_delta'] = t.delta.abs()
    heat_rows, max_rows = [], []
    for panel in ['fig3a', 'fig3b', 'fig4a', 'fig4c', 'fig4d']:
        for metric in METRICS[1:]:
            s = t[(t.panel == panel) & (t.metric == metric)]
            assert s.config_id.nunique() == 9
            largest = s.loc[s.abs_delta.idxmax()]
            max_rows.append(dict(panel=panel, metric=metric, max_abs_change=largest.abs_delta,
                                 group_at_max=largest.group, config_at_max=largest.config_id,
                                 signed_change_at_max=largest.delta))
            if panel == 'fig4c':
                for cid, rs in s.groupby('config_id'):
                    largest = rs.loc[rs.abs_delta.idxmax()]
                    assert len(rs) == 13
                    heat_rows.append(dict(metric=metric, config_id=cid,
                        state_threshold=config[cid]['state_threshold'],
                        transition_threshold=config[cid]['transition_threshold'],
                        max_abs_change=largest.abs_delta, checkpoint_at_max=largest.group,
                        signed_change_at_max=largest.delta))
    heat, maxima = pd.DataFrame(heat_rows), pd.DataFrame(max_rows)
    fig = plt.figure(figsize=(5.5, 2.20))
    grid = fig.add_gridspec(1, 3, left=.09, right=.86, bottom=.16, top=.79, wspace=.39)
    for j, metric in enumerate(METRICS[1:]):
        ax = fig.add_subplot(grid[0, j])
        matrix = heat[heat.metric == metric].pivot(index='state_threshold', columns='transition_threshold', values='max_abs_change').reindex(index=thresholds, columns=thresholds).to_numpy()
        assert abs(matrix[1, 1]) < 1e-12
        im = ax.pcolormesh(np.arange(4)-.5, np.arange(4)-.5, matrix,
                           cmap='Blues', vmin=0, vmax=.14, shading='flat', rasterized=False)
        ax.set_aspect('equal')
        ax.set_xlim(-.5,2.5);ax.set_ylim(-.5,2.5)
        for y in range(3):
            for x in range(3):
                ax.text(x, y, f'{matrix[y,x]:.3f}', ha='center', va='center', fontsize=8,
                        color='white' if matrix[y,x] > .085 else '#222222')
        ax.add_patch(Rectangle((.5,.5),1,1,fill=False,edgecolor='#222222',linewidth=1.3))
        ax.set_xticks(range(3), ['0.15','0.25','0.40'])
        ax.set_yticks(range(3), ['0.15','0.25','0.40'])
        ax.set_title(['Transition F1','State F1','Typed-edge F1'][j],fontsize=8.5,pad=8)
        ax.set_xlabel('Transition threshold',fontsize=7.2,labelpad=3)
        if j == 0: ax.set_ylabel('State threshold',fontsize=7.2,labelpad=3)
        ax.tick_params(length=0,labelsize=7)
        for spine in ax.spines.values(): spine.set_visible(False)
    cax = fig.add_axes([.89,.22,.018,.51])
    cb=fig.colorbar(im,cax=cax,ticks=[0,.07,.14])
    cb.solids.set_rasterized(False)
    cb.solids.set_edgecolor('face')
    cb.ax.tick_params(labelsize=7,length=2)
    fig.suptitle('Maximum absolute change in macro-F1',fontsize=9,y=.97)
    export(heat,'figure_B_heatmaps.csv');export(maxima,'figure_B_maxima.csv')
    save(fig,'figS_sensitivity_thresholds.pdf')
    return maxima

def figure_c(contrasts):
    specs=[('fig4a','general_methods_plus_memory_v1','baseline','Combined - baseline','External knowledge'),
           ('fig4c','C58','C0','C58 - C0','Learning validation'),
           ('fig4d','c30_kb','c0','C30 + KB - C0','Held-out test')]
    records=[]
    for panel,g,b,_,_ in specs:
        for metric in METRICS:
            s=contrasts[(contrasts.panel==panel)&(contrasts.group_a==g)&(contrasts.group_b==b)&(contrasts.metric==metric)]
            assert len(s)==640 and s.config_id.nunique()==640
            d=one(s[s.config_id=='original'])
            records.append(dict(panel=panel,metric=metric,intervention=g,baseline=b,
                n_protocols=int(d.n_protocols),default_difference=d.mean_difference,
                scoring_min=s.mean_difference.min(),scoring_max=s.mean_difference.max(),
                default_ci_low=d.ci_low,default_ci_high=d.ci_high,
                positive_configurations=int((s.mean_difference>1e-12).sum()),
                negative_configurations=int((s.mean_difference < -1e-12).sum()),configurations=640))
    rows=pd.DataFrame(records)
    fig,axes=plt.subplots(1,3,figsize=(5.5,2.8),sharey=True,gridspec_kw={'left':.15,'right':.985,'top':.76,'bottom':.25,'wspace':.21})
    for j,(ax,(panel,_,_,title,subtitle)) in enumerate(zip(axes,specs)):
        for y,m in enumerate(METRICS):
            r=one(rows[(rows.panel==panel)&(rows.metric==m)])
            ax.plot([r.scoring_min,r.scoring_max],[y-.11,y-.11],color='#377b8c',lw=4,solid_capstyle='butt',zorder=2)
            ax.scatter(r.default_difference,y-.11,s=23,marker='D',color='#222222',edgecolors='white',linewidths=.45,zorder=4)
            ax.plot([r.default_ci_low,r.default_ci_high],[y+.14,y+.14],color='#777777',lw=.85,zorder=2)
            ax.plot([r.default_ci_low,r.default_ci_high],[y+.14,y+.14],'|',color='#777777',ms=4,mew=.8)
        ax.axvline(0,color='#333333',ls='--',lw=.8,zorder=1)
        ax.set_yticks(range(4),SHORT)
        ax.set_ylim(3.5,-.5)
        ax.set_xlim(-.36,.20)
        ax.set_xticks([-.3,0,.2],['-0.3','0','0.2'])
        ax.set_title(title,fontsize=8.1,pad=24)
        ax.text(.5,1.08,subtitle,transform=ax.transAxes,ha='center',fontsize=7.3,color='#555555')
        ax.text(.5,1.015,'n = 4 oligo; 5 Task 3' if j<2 else 'n = 10, all metrics',transform=ax.transAxes,ha='center',fontsize=7.1,color='#555555')
        clean_axes(ax)
        ax.set_xlabel('F1 difference',fontsize=8,labelpad=4)
    handles=[Line2D([],[],color='#377b8c',lw=4,label='Scoring range (640 settings)'),
             Line2D([],[],marker='D',color='#222222',ls='None',ms=4,label='Default difference'),
             Line2D([],[],color='#777777',lw=.85,marker='|',ms=4,label='95% protocol-bootstrap CI (default)')]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.015),ncol=3,frameon=False,fontsize=7,handlelength=1.3,columnspacing=.9,handletextpad=.45)
    export(rows,'figure_C_data.csv')
    save(fig,'figS_sensitivity_interventions.pdf')
    return rows

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    before={name:sha(SRC/name) for name in FILENAMES}
    paper_before={str(p.relative_to(PAPER)):sha(p) for p in [PAPER/'main.tex',*sorted((PAPER/'figures').rglob('*.pdf'))]}
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Helvetica','Arial','DejaVu Sans'],
        'font.size':8,'axes.labelsize':8,'xtick.labelsize':7.3,'ytick.labelsize':8,
        'axes.linewidth':.6,'pdf.fonttype':42,'ps.fonttype':42})
    a=primary(pd.read_csv(SRC/'aggregates.csv'))
    freq=primary(pd.read_csv(SRC/'rank_first_frequencies.csv'))
    contrasts=primary(pd.read_csv(SRC/'paired_contrasts.csv'))
    trajectory=primary(pd.read_csv(SRC/'trajectory_monotonicity.csv'))
    manifest=json.loads((SRC/'manifest.json').read_text())
    assert len(manifest['configurations'])==640
    assert len(a[a.panel=='fig3a'].config_id.unique())==640
    expected={'fig3a':(19,20),'fig3b':(9,10),'fig4a':(4,5),'fig4c':(4,5),'fig4d':(10,10)}
    for panel,(n2,n3) in expected.items():
        assert set(a[(a.panel==panel)&(a.metric==METRICS[0])].n_protocols)=={n2}
        assert set(a[(a.panel==panel)&(a.metric!=METRICS[0])].n_protocols)=={n3}
    # Validate requested claims directly against saved results, not prose.
    for metric,leader in [(METRICS[0],'kimi_code'),(METRICS[2],'claude_code'),(METRICS[3],'gpt_codex')]:
        s=a[(a.panel=='fig3a')&(a.metric==metric)&(a.group==leader)]
        assert len(s)==640 and (s['rank']==1).all()
    for metric in METRICS:
        s=trajectory[trajectory.metric==metric]
        assert len(s)==640 and s.nonmonotonic.all()
    for group in ['c30_pact','c30_kb']:
        s=contrasts[(contrasts.panel=='fig4d')&(contrasts.group_a==group)&(contrasts.group_b=='c0')&contrasts.metric.isin(METRICS[1:])]
        assert len(s)==1920 and (s.mean_difference<0).all()
    A=figure_a(a,freq);B=figure_b(a,manifest);C=figure_c(contrasts)
    assert one(C[(C.panel=='fig4a')&(C.metric==METRICS[0])]).positive_configurations==640
    r=one(C[(C.panel=='fig4c')&(C.metric==METRICS[0])]);assert r.scoring_min<0<r.scoring_max
    z=pd.read_csv(SRC/'zero_required_exclusion_baseline.csv')
    selected=z[(z.panel=='fig4a')|((z.panel=='fig4c')&z.group.isin(['C0','C30','C40','C58']))].copy()
    sp=pd.read_csv(SRC/'zero_required_spurious_families.csv')
    sp=sp[(sp.config_id=='original')&(sp.variant=='corrected_4_7_0')]
    selected=selected.merge(sp[['panel','group','protocol_id','effective_predicted_families']],on=['panel','group'],validate='one_to_one')
    assert set(selected.protocol_id)=={'ddseq_single_cell_3_rna_seq_kit'}
    export(selected,'zero_required_summary.csv')
    config_counts={f'{fam}/{kind}':sum(c['family']==fam and c['kind']==kind for c in manifest['configurations']) for fam,kind in sorted({(c['family'],c['kind']) for c in manifest['configurations']})}
    after={name:sha(SRC/name) for name in FILENAMES}
    assert before==after
    assert paper_before=={name:sha(PAPER/name) for name in paper_before}
    receipt={'source_directory':str(SRC),'paper_directory':str(PAPER),'source_sha256':before,
        'paper_original_sha256':paper_before,'script_sha256':sha(__file__),
        'configuration_counts':config_counts,'source_tables_unchanged':True,
        'main_tex_and_existing_pdf_figures_unchanged':True,'assertions':'passed',
        'reference_policy':manifest['reference_policy'],'historical_checkpoint':manifest['historical_checkpoint'],
        'no_scoring_or_bootstrap_recomputed':True,
        'figure_C_interval_method':'Saved 5,000 paired protocol-bootstrap percentile intervals at default scoring; no new bootstrap',
        'outputs':{p.name:sha(p) for p in sorted(OUT.glob('figS_*.pdf'))}}
    (OUT/'publication_manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('Created 3 vector PDFs and 5 compact data tables; all frozen-input and numerical assertions passed.')

if __name__=='__main__':main()
