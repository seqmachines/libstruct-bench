#!/usr/bin/env python3
"""Plot the parser-control per-sequence similarities for Supplementary Figure 2."""

from __future__ import annotations

import csv
import random
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
DATA = HERE / "parser_control_per_sequence.csv"
OUT = HERE / "figures" / "supp2_parser_control.pdf"

INPUTS = (
    ("human", "Human\ntext"),
    ("pymupdf", "PyMuPDF\ntext"),
    ("pypdf", "pypdf\ntext"),
    ("docling", "Docling\ntext"),
    ("mineru", "MinerU\nOCR"),
)

PROTOCOLS = (
    ("10x_chromium_3_gene_expression_v4", "10x 3' GEX v4", "#1F77B4"),
    ("10x_chromium_single_cell_atac_v2", "10x scATAC v2", "#FF7F0E"),
    ("cel_seq", "CEL-Seq", "#2CA02C"),
    ("drop_seq", "Drop-seq", "#D62728"),
    ("scrrbs", "scRRBS", "#9467BD"),
)

GRID_COLOR = "#E1E4E8"
SPINE_COLOR = "#A6A6A6"
TEXT_COLOR = "#202124"


def load_scores() -> dict[str, dict[str, list[float]]]:
    scores = {
        input_id: {protocol_id: [] for protocol_id, _, _ in PROTOCOLS}
        for input_id, _ in INPUTS
    }
    with DATA.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            input_id = row["input_id"]
            protocol_id = row["protocol_id"]
            if input_id in scores and protocol_id in scores[input_id]:
                scores[input_id][protocol_id].append(
                    float(row["max_similarity_to_input"])
                )
    missing = [
        f"{input_id}/{protocol_id}"
        for input_id, protocol_scores in scores.items()
        for protocol_id, values in protocol_scores.items()
        if not values
    ]
    if missing:
        raise ValueError(f"missing parser-control scores for: {', '.join(missing)}")
    return scores


def main() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "text.color": TEXT_COLOR,
            "axes.labelcolor": TEXT_COLOR,
            "xtick.color": TEXT_COLOR,
            "ytick.color": TEXT_COLOR,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    scores = load_scores()
    rng = random.Random(20260808)
    fig, ax = plt.subplots(figsize=(4.7, 3.25))

    for position, (input_id, _) in enumerate(INPUTS):
        for protocol_id, protocol_label, color in PROTOCOLS:
            values = scores[input_id][protocol_id]
            x_values = [position + rng.uniform(-0.20, 0.20) for _ in values]
            ax.scatter(
                x_values,
                values,
                s=20,
                color=color,
                edgecolor="white",
                linewidth=0.25,
                alpha=0.68,
                label=protocol_label if position == 0 else None,
                zorder=3,
            )

    ax.set_xlim(-0.45, len(INPUTS) - 0.55)
    ax.set_ylim(0.00, 1.025)
    ax.set_xticks(range(len(INPUTS)), [label for _, label in INPUTS])
    ax.set_yticks(
        (0.00, 0.25, 0.50, 0.75, 1.00),
        ("0", "0.25", "0.50", "0.75", "1"),
    )
    ax.set_ylabel("Best-match sequence similarity", fontsize=10.5, labelpad=6)
    ax.tick_params(axis="x", labelsize=10.2, length=0, pad=5)
    ax.tick_params(axis="y", labelsize=10.5, length=4, width=1)
    ax.grid(True, axis="y", color=GRID_COLOR, linewidth=0.9)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(SPINE_COLOR)
    ax.spines["left"].set_color(SPINE_COLOR)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.23),
        ncol=3,
        frameon=False,
        fontsize=9.2,
        handletextpad=0.35,
        columnspacing=0.9,
        markerscale=0.9,
    )
    fig.subplots_adjust(left=0.17, right=0.99, bottom=0.34, top=0.98)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor="white", bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)


if __name__ == "__main__":
    main()
