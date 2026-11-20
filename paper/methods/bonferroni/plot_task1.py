"""Presentation only: preserve the existing Figure 2 style and scores."""
import os
os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/libstruct-fig2-matplotlib')
os.environ.setdefault('XDG_CACHE_HOME', '/private/tmp/libstruct-fig2-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def draw(summary, paired, destination):
    # The published Figure 2 was Matplotlib 3.9.4 / Arial, not an R plot.
    # Recover its exact page and panel geometry from the existing vector PDF.
    # The old final source is absent; an earlier Python plotting bytecode cache
    # survives at analysis/library_structure/__pycache__/02_make_figure.*.pyc.
    from matplotlib.font_manager import FontProperties, findfont
    findfont(FontProperties(family='Arial'), fallback_to_default=False)
    ink = '#202124'
    axis_color = (0.650904119, 0.651010931, 0.650904119)
    plt.rcParams.update({'font.family': 'Arial', 'font.size': 12.5,
                         'axes.titlesize': 14.5, 'axes.labelsize': 14,
                         'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'text.color': ink, 'axes.labelcolor': ink,
                         'xtick.color': ink, 'ytick.color': ink,
                         'mathtext.fontset': 'custom', 'mathtext.rm': 'Arial',
                         'mathtext.it': 'Arial:italic', 'mathtext.bf': 'Arial:bold'})
    width_pt, height_pt = 644.1125, 235.7319
    panel_side = 166.428
    panel_bottom = height_pt - 188.229874
    fig = plt.figure(figsize=(width_pt / 72, height_pt / 72))
    for idx, (s, left_pt) in enumerate(zip(summary, [56.073086, 265.438812, 474.804504], strict=True)):
        ax = fig.add_axes([left_pt / width_pt, panel_bottom / height_pt,
                           panel_side / width_pt, panel_side / height_pt])
        ax.set_aspect('equal', adjustable='box')
        data = [r for r in paired if r['model_family'] == s['model_family']]
        ax.plot([0, 1], [0, 1], color=axis_color, lw=1.5, ls=(0, (4, 3)), zorder=1)
        ax.scatter([r['api_similarity'] for r in data], [r['agent_similarity'] for r in data],
                   s=70, c='#4682b4', edgecolors='white', linewidths=.9, alpha=.92, zorder=3)
        ax.set(xlim=(-.025, 1.035), ylim=(-.025, 1.035))
        ax.set_title(s['model_label'], pad=8)
        ticks = [0, .25, .5, .75, 1]
        ax.set_xticks(ticks, ['0', '0.25', '0.50', '0.75', '1'])
        ax.set_yticks(ticks, ['0', '0.25', '0.50', '0.75', '1'] if idx == 0 else [])
        ax.tick_params(labelsize=12.5, length=4, width=1)
        ax.set_axisbelow(True)
        ax.grid(color='#e1e4e8', lw=.9)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        for side in ('bottom', 'left'):
            ax.spines[side].set_color(axis_color)
            ax.spines[side].set_linewidth(.8)
        # New annotations occupy the otherwise empty lower-right corner.
        ax.text(.96, .08, f'$\\Delta$ = {s["mean_agent_minus_api"]:+.3f}\n'
                + f'$p_{{\\mathrm{{Bonf}}}}$ = {s["p_bonferroni"]:.3f}',
                transform=ax.transAxes, ha='right', va='bottom', fontsize=11.5,
                linespacing=1.5, bbox={'facecolor': 'white', 'alpha': .9, 'edgecolor': 'none', 'pad': 2})
        assert len(ax.collections[0].get_offsets()) == s['paired_n']
    fig.text(322.15681 / width_pt, (height_pt - 226.36748) / height_pt,
             'Direct API sequence similarity', ha='center', va='center', fontsize=14)
    fig.text(9.45031 / width_pt, (height_pt - 118.28187) / height_pt,
             'Coding-agent sequence similarity', rotation=90, ha='center', va='center', fontsize=14)
    fig.savefig(destination, metadata={'Title': 'Task 1: API versus agent; Bonferroni adjustment across three models', 'CreationDate': None})
    plt.close(fig)

