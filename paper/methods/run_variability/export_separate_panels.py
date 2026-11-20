#!/usr/bin/env python
"""Export compact standalone panels from verified CSVs; no rescoring."""
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/libstruct-run-variability-mpl")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
import fitz

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "results_v1"
OUT = DATA / "separate_panels"
METRICS = ["t2_required_family_f1", "t3_molecular_transition_f1",
           "t3_state_f1", "t3_typed_edge_f1"]
LABELS = ["Oligo", "Transition", "State", "Typed edge"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    lock = json.loads((DATA / "reproducibility_manifest.json").read_text())
    inputs = ["per_protocol_ranges.csv", "summary.csv",
              "figS_codex_execution_variability.pdf"]
    for name in inputs:
        assert sha(DATA / name) == lock["outputs"][name], name
    ranges = pd.read_csv(DATA / inputs[0])
    summary = pd.read_csv(DATA / inputs[1]).set_index("metric").loc[METRICS]
    OUT.mkdir(exist_ok=True)
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.4,
        "ytick.labelsize": 7.4, "axes.linewidth": .6,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })
    outputs = {}
    for panel in ("within_protocol_variability", "aggregate_performance"):
        fig, ax = plt.subplots(figsize=(3.0, 2.8))
        fig.subplots_adjust(left=.18, right=.98, bottom=.25, top=.78)
        if panel == "within_protocol_variability":
            for x, metric in enumerate(METRICS):
                s = ranges[ranges.metric == metric].sort_values("protocol_id")
                jitter = np.linspace(-.19, .19, len(s))
                ax.scatter(x+jitter, s["range"], s=15, color="#438999",
                           alpha=.72, linewidths=.35, edgecolors="white", zorder=3)
                r = summary.loc[metric]
                ax.plot([x, x], [r.range_q25, r.range_q75], color="#222222", lw=2.1, zorder=4)
                for q in (r.range_q25, r.range_q75):
                    ax.plot([x-.11, x+.11], [q]*2, color="#222222", lw=1, zorder=4)
                ax.scatter(x, r.range_median, marker="D", s=24, facecolor="white",
                           edgecolor="#222222", linewidth=.9, zorder=5)
                ax.text(x, .505, f"{int(r.protocols_range_gt_010)}/{int(r.n_protocols)}",
                        ha="center", va="bottom", fontsize=7.4)
            ax.axhline(.1, color="#999999", ls=(0, (3, 3)), lw=.7, zorder=1)
            ax.set_ylim(-.012, .55)
            ax.set_yticks([0, .1, .2, .3, .4, .5])
            ax.set_ylabel("Within-protocol F1 range")
            ax.set_title("Within-protocol variability", fontsize=9, pad=27)
            ax.text(.5, 1.055, "Counts above: range > 0.10", transform=ax.transAxes,
                    ha="center", fontsize=7.4, color="#555555")
            ax.legend(handles=[
                Line2D([], [], marker="o", ls="None", color="#438999", markersize=3.5, label="Protocol"),
                Line2D([], [], marker="D", color="#222222", markerfacecolor="white",
                       linewidth=1.7, markersize=3.5, label="Median and IQR"),
            ], loc="upper center", bbox_to_anchor=(.5, -.23), ncol=2,
                fontsize=7, frameon=False, handlelength=1.2, columnspacing=1.1)
        else:
            styles = [
                ("original_macro_f1", "Original", "#277da8", "o", -.18),
                ("repeat_earlier_macro_f1", "Repeat 1", "#cf6846", "s", -.06),
                ("repeat_later_macro_f1", "Repeat 2", "#5b9861", "^", .06),
                ("three_attempt_macro_f1", "Three-execution mean", "#222222", "D", .18),
            ]
            for column, label, color, marker, offset in styles:
                ax.scatter(np.arange(4)+offset, summary[column], label=label,
                           marker=marker, s=28 if marker == "D" else 23,
                           facecolor="white" if marker == "D" else color,
                           edgecolor=color, linewidth=.9, zorder=4)
            ax.set_ylim(.59, .85)
            ax.set_yticks([.60, .65, .70, .75, .80, .85])
            ax.set_ylabel("Protocol-macro F1")
            ax.set_title("Aggregate performance", fontsize=9, pad=27)
            ax.text(.5, 1.055, "Three saved executions per protocol", transform=ax.transAxes,
                    ha="center", fontsize=7.4, color="#555555")
            ax.legend(loc="upper center", bbox_to_anchor=(.5, -.23), ncol=2,
                      fontsize=7, frameon=False, handlelength=1, columnspacing=1.2,
                      handletextpad=.4, labelspacing=.4)
        ax.set_xticks(range(4), LABELS)
        ax.set_xlim(-.45, 3.45)
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["bottom", "left"]].set_color("#777777")
        ax.grid(axis="y", color="#e5e5e5", linewidth=.5, zorder=0)
        ax.set_axisbelow(True)
        path = OUT / f"figS_codex_{panel}.pdf"
        fig.savefig(path, format="pdf", facecolor="white", metadata={
            "Title": f"LibStructBench: Codex {panel.replace('_', ' ')}",
            "Creator": "Export from verified execution-variability CSVs",
            "CreationDate": None, "ModDate": None,
        })
        plt.close(fig)
        with fitz.open(path) as doc:
            assert len(doc) == 1
            page = doc[0]
            assert not page.get_images()
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        assert page.rect.contains(fitz.Rect(span["bbox"])), span
            page.get_pixmap(matrix=fitz.Matrix(3, 3)).save(f"/private/tmp/{path.stem}.png")
            outputs[path.name] = {"sha256": sha(path), "width_inches": page.rect.width/72,
                                  "height_inches": page.rect.height/72, "raster_images": 0}
    for name in inputs:
        assert sha(DATA / name) == lock["outputs"][name], name
    manifest = {"source_data_unchanged": True, "combined_pdf_unchanged": True,
                "rescoring_performed": False, "script_sha256": sha(Path(__file__)),
                "inputs_sha256": {n: sha(DATA/n) for n in inputs}, "outputs": outputs}
    (OUT / "export_manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
