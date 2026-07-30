#!/usr/bin/env python3
"""Render the API-vs-coding-agent library-structure comparison figure.

Top row : 3 square scatter panels (one per AI) — x = direct-API sequence
          similarity, y = coding-agent sequence similarity, one point per
          protocol, y=x parity line. Points colored by which method scored
          higher. Only protocols where the API succeeded are shown (API
          failures removed, per spec).
Bottom  : grouped box plot (API vs Agent per AI, paired set) with the group
          mean marked and labeled, plus a slim completion-rate panel
          (robustness: API completed N/20, agent 20/20).
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

REPO = Path("/Users/seqmachines/playground/libstruct-bench")
OUT = REPO / "analysis" / "library_structure"
rows = json.loads((OUT / "grades_long.json").read_text())

AIS = ["Claude", "GPT", "Gemini"]
AI_SUB = {"Claude": "Opus 4.8  ·  claude-code (xhigh)",
          "GPT": "GPT-5.5  ·  codex (xhigh)",
          "Gemini": "Gemini 3.1 Pro  ·  gemini-cli (high)"}

# ---- palette (dataviz reference, light mode) ------------------------------
C_API   = "#2a78d6"   # blue   — direct API
C_AGENT = "#1baf7a"   # aqua   — coding agent
C_TIE   = "#7c7a75"   # muted gray — tie
SURFACE = "#ffffff"
PAGE    = "#ffffff"
INK     = "#0b0b0b"
INK2    = "#41403d"
MUTED   = "#7c7a75"
GRID    = "#e4e3dc"
AXIS    = "#b9b8b1"

mpl.rcParams.update({
    "figure.facecolor": PAGE, "axes.facecolor": SURFACE,
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "text.color": INK, "axes.edgecolor": AXIS, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2,
    "xtick.labelsize": 12, "ytick.labelsize": 12,
    "axes.linewidth": 1.1, "svg.fonttype": "none",
})

# ---- reshape --------------------------------------------------------------
def cell(ai, method, pid):
    for r in rows:
        if r["ai"] == ai and r["method"] == method and r["protocol"] == pid:
            return r
    return None

protocols = []
for r in rows:
    if r["protocol"] not in protocols:
        protocols.append(r["protocol"])

SHORT = {  # compact protocol labels for annotations
    "10x_chromium_single_cell_atac_v2": "10x scATAC v2",
    "10x_chromium_3_gene_expression_v4": "10x 3' GEX v4",
    "10x_chromium_3_feature_barcoding": "10x 3' FB",
    "drop_seq": "Drop-seq", "seq_well_s3": "Seq-Well S3", "indrop_v1": "inDrop v1",
    "s3_atac": "s3-ATAC", "cel_seq": "CEL-Seq", "split_seq": "SPLiT-seq",
    "sci_rna_seq": "sci-RNA-seq", "sci_atac_seq": "sci-ATAC-seq",
    "microwell_seq": "Microwell-seq", "scrrbs": "scRRBS", "pip_seq_v4": "PIP-seq v4",
    "scdamid": "scDam&T", "ddseq_single_cell_3_rna_seq_kit": "ddSEQ 3'RNA",
    "smart_seq": "Smart-seq", "share_seq": "SHARE-seq", "dr_seq": "DR-seq",
    "petri_seq": "PETRI-seq",
}

paired = {}   # ai -> list of (pid, api_sim, agent_sim)
api_fail = {}  # ai -> count of API failures
for ai in AIS:
    pts, nf = [], 0
    for pid in protocols:
        a = cell(ai, "API", pid)
        g = cell(ai, "Agent", pid)
        if a is None or a["status"] != "ok":
            nf += 1
            continue
        pts.append((pid, a["sequence_similarity"], g["sequence_similarity"]))
    paired[ai] = pts
    api_fail[ai] = nf

# ---- figure ---------------------------------------------------------------
fig = plt.figure(figsize=(15.0, 12.4), dpi=200)
gs = GridSpec(2, 3, figure=fig, height_ratios=[1.0, 0.88],
              hspace=0.58, wspace=0.24,
              left=0.062, right=0.982, top=0.795, bottom=0.070)

TIE_EPS = 1e-9

def outcome_color(a, g):
    if abs(g - a) <= TIE_EPS:
        return C_TIE
    return C_AGENT if g > a else C_API

# ----- top row: scatter panels --------------------------------------------
for i, ai in enumerate(AIS):
    ax = fig.add_subplot(gs[0, i])
    pts = paired[ai]
    n = len(pts)
    wins = sum(1 for _, a, g in pts if g - a > TIE_EPS)
    losses = sum(1 for _, a, g in pts if a - g > TIE_EPS)
    ties = n - wins - losses

    # parity zones (very light wash) + y=x line
    ax.fill_between([0, 1.04], [0, 1.04], 1.04, color=C_AGENT, alpha=0.05, lw=0, zorder=0)
    ax.fill_between([0, 1.04], 0, [0, 1.04], color=C_API, alpha=0.05, lw=0, zorder=0)
    ax.plot([0, 1.04], [0, 1.04], ls=(0, (4, 3)), lw=1.3, color=AXIS, zorder=1)

    ax.grid(True, color=GRID, lw=1.0, zorder=0)
    ax.set_axisbelow(True)

    for pid, a, g in pts:
        ax.scatter(a, g, s=118, color=outcome_color(a, g), alpha=0.9,
                   edgecolor="white", linewidth=1.5, zorder=4)
        if abs(g - a) > 0.13:  # selective direct labels: notable disagreements
            lx = min(max(a, 0.02), 0.98)
            ha = "left" if a < 0.14 else ("right" if a > 0.86 else "center")
            if g < 0.12:                       # near floor → label above
                ly, va = g + 0.05, "bottom"
            elif g >= a:
                ly, va = g + 0.035, "bottom"
            else:
                ly, va = g - 0.06, "top"
            ax.annotate(SHORT.get(pid, pid), (lx, ly), fontsize=9.6, color=INK2,
                        ha=ha, va=va, zorder=5)

    # corner cues (mathtext arrows render regardless of the UI font)
    ax.text(0.035, 0.975, r"agent better $\uparrow$", transform=ax.transAxes,
            fontsize=10.5, color=C_AGENT, ha="left", va="top", fontweight="bold")
    ax.text(0.975, 0.03, r"$\downarrow$ API better", transform=ax.transAxes,
            fontsize=10.5, color=C_API, ha="right", va="bottom", fontweight="bold")

    ax.set_xlim(-0.02, 1.05); ax.set_ylim(-0.02, 1.05)
    ax.set_aspect("equal")
    ax.set_xticks([0, .25, .5, .75, 1]); ax.set_yticks([0, .25, .5, .75, 1])
    ax.tick_params(labelsize=12.5)
    ax.set_xlabel("Direct-API similarity", fontsize=15, color=INK, labelpad=9)
    if i == 0:
        ax.set_ylabel("Coding-agent similarity", fontsize=15, color=INK, labelpad=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    # title + subtitle above the panel (own coords, clear of the header)
    ax.text(0, 1.155, ai, transform=ax.transAxes, fontsize=17, color=INK,
            fontweight="bold", va="bottom", ha="left")
    ax.text(0, 1.055, AI_SUB[ai], transform=ax.transAxes, fontsize=10.4,
            color=MUTED, va="bottom", ha="left")
    # per-panel tally as a caption BELOW the plot (never covers data)
    ax.text(0.5, -0.245,
            f"n = {n} paired   ·   agent wins {wins} · tie {ties} · API wins {losses}\n"
            f"API failed on {api_fail[ai]}/20 protocols (removed)",
            transform=ax.transAxes, fontsize=10.4, color=INK2,
            ha="center", va="top", linespacing=1.6)

# ----- bottom-left: grouped box plot --------------------------------------
axb = fig.add_subplot(gs[1, 0:2])
box_data, positions, colors = [], [], []
xt, xtl = [], []
for gi, ai in enumerate(AIS):
    pts = paired[ai]
    api_v = [a for _, a, _ in pts]
    ag_v = [g for _, _, g in pts]
    base = gi * 1.0
    for off, vals, col in ((-0.19, api_v, C_API), (0.19, ag_v, C_AGENT)):
        box_data.append(vals); positions.append(base + off); colors.append(col)
    xt.append(base); xtl.append(ai)

bp = axb.boxplot(box_data, positions=positions, widths=0.30, patch_artist=True,
                 showfliers=False, medianprops=dict(color=INK, lw=1.9),
                 whiskerprops=dict(color=AXIS, lw=1.3),
                 capprops=dict(color=AXIS, lw=1.3),
                 boxprops=dict(lw=0.0), zorder=2)
for patch, col in zip(bp["boxes"], colors):
    patch.set_facecolor(col); patch.set_alpha(0.26)
    patch.set_edgecolor(col); patch.set_linewidth(1.5)

rng = np.random.default_rng(7)
for vals, pos, col in zip(box_data, positions, colors):
    jit = pos + (rng.random(len(vals)) - 0.5) * 0.14
    axb.scatter(jit, vals, s=34, color=col, alpha=0.85, edgecolor="white",
                linewidth=0.8, zorder=3)

# mean of each distribution: white diamond + value label above the box
for vals, pos, col in zip(box_data, positions, colors):
    m = float(np.mean(vals))
    axb.scatter([pos], [m], marker="D", s=82, facecolor="white", edgecolor=INK,
                linewidth=1.6, zorder=6)
    axb.text(pos, 1.105, f"{m:.3f}", ha="center", va="bottom", fontsize=12.5,
             fontweight="bold", color=col)
axb.text(-0.55, 1.105, "mean", ha="left", va="bottom", fontsize=11.5, color=MUTED)

axb.grid(True, axis="y", color=GRID, lw=1.0); axb.set_axisbelow(True)
axb.set_xticks(xt); axb.set_xticklabels(xtl, fontsize=16, color=INK, fontweight="bold")
axb.set_xlim(-0.6, 2.6); axb.set_ylim(-0.03, 1.19)
axb.set_yticks([0, .25, .5, .75, 1])
axb.tick_params(axis="y", labelsize=12.5)
axb.set_ylabel("Sequence similarity", fontsize=15, color=INK, labelpad=9)
for s in ("top", "right"):
    axb.spines[s].set_visible(False)
axb.set_title("Per-AI distribution on protocols the API completed (paired)",
              fontsize=14.5, color=INK, fontweight="bold", loc="left", pad=10)
axb.legend(handles=[Patch(fc=C_API, ec=C_API, alpha=0.5, label="Direct API"),
                    Patch(fc=C_AGENT, ec=C_AGENT, alpha=0.5, label="Coding agent"),
                    Line2D([0], [0], marker="D", ls="", mfc="white", mec=INK,
                           mew=1.6, ms=9, label="mean")],
           loc="upper center", fontsize=12.5, frameon=False, ncol=3,
           handletextpad=0.6, columnspacing=2.0, bbox_to_anchor=(0.5, -0.10))

# ----- bottom-right: completion-rate panel --------------------------------
axc = fig.add_subplot(gs[1, 2])
ypos = []
labels = []
y = 0
for ai in reversed(AIS):
    done_api = 20 - api_fail[ai]
    for method, val, col in (("Agent", 20, C_AGENT), ("API", done_api, C_API)):
        axc.barh(y, val, height=0.62, color=col, alpha=0.9,
                 edgecolor="white", linewidth=0.9, zorder=3)
        axc.text(val - 0.45, y, f"{val}", va="center", ha="right",
                 fontsize=11.5, color="white", fontweight="bold", zorder=4)
        ypos.append(y); labels.append(f"{ai} · {method}")
        y += 1
    y += 0.6
axc.set_xlim(0, 20.6); axc.set_ylim(-0.6, y - 0.4)
axc.axvline(20, color=AXIS, lw=1.2, ls=(0, (4, 3)), zorder=1)
axc.set_yticks(ypos); axc.set_yticklabels(labels, fontsize=10.8, color=INK2)
axc.set_xticks([0, 10, 20])
axc.tick_params(axis="x", labelsize=12.5)
axc.set_xlabel("Protocols completed (of 20)", fontsize=14, color=INK, labelpad=9)
axc.grid(True, axis="x", color=GRID, lw=1.0); axc.set_axisbelow(True)
for s in ("top", "right", "left"):
    axc.spines[s].set_visible(False)
axc.tick_params(axis="y", length=0)
axc.set_title("Robustness", fontsize=14.5, color=INK, fontweight="bold", loc="left", pad=10)

# ---- figure header --------------------------------------------------------
fig.text(0.062, 0.980, "Coding agents vs direct LLM APIs on library-structure extraction",
         fontsize=22, color=INK, fontweight="bold", ha="left", va="top")
fig.text(0.062, 0.941,
         "Final-library sequence similarity (1 − normalized Levenshtein distance) vs curated ground truth, "
         "20 single-cell protocols · PDF/DOCX/XLSX inputs.\n"
         "Direct-API runs that errored (HTTP 4xx/5xx / truncation) are removed; each scatter shows only "
         "protocols where that AI's API returned a scorable answer.",
         fontsize=11.6, color=INK2, ha="left", va="top", linespacing=1.55)

for ext in ("png", "pdf", "svg"):
    fig.savefig(OUT / f"api_vs_agent_library_structure.{ext}",
                facecolor=PAGE, bbox_inches="tight", pad_inches=0.2)
    print("wrote", OUT / f"api_vs_agent_library_structure.{ext}")
