#!/usr/bin/env python3
"""Plot confirmed causes of sequence-relevant audit findings for Supp. Fig. 1."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
DATA = HERE / "audit_sequence_error_causes.csv"
OUT = HERE / "figures" / "supp1_sequence_error_causes"

COLORS = ("#E69F00", "#56B4E9", "#009E73", "#CC79A7")
DISPLAY_LABELS = (
    "Human curation error",
    "Protocol ambiguity",
    "Format",
    "Inconsistent names",
)
TEXT_COLOR = "#202124"


def load_data() -> tuple[list[str], list[int]]:
    with DATA.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no audit-error counts found in {DATA}")
    labels = [row["confirmed_cause"] for row in rows]
    counts = [int(row["count"]) for row in rows]
    if any(count <= 0 for count in counts):
        raise ValueError("all audit-error counts must be positive")
    return labels, counts


def main() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "text.color": TEXT_COLOR,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )

    _, counts = load_data()
    total = sum(counts)
    fig, ax = plt.subplots(figsize=(6.5, 4.4))

    wedges, _ = ax.pie(
        counts,
        colors=COLORS,
        startangle=90,
        counterclock=False,
        radius=1.0,
        wedgeprops={"width": 0.38, "edgecolor": "white", "linewidth": 1.5},
    )

    # Keep even the two smallest slices readable with external labels and leaders.
    label_radius = 1.23
    for wedge, count in zip(wedges, counts):
        angle = math.radians((wedge.theta1 + wedge.theta2) / 2)
        x, y = math.cos(angle), math.sin(angle)
        ax.annotate(
            f"{100 * count / total:.1f}%\n(n={count})",
            xy=(0.92 * x, 0.92 * y),
            xytext=(label_radius * x, label_radius * y),
            ha="left" if x >= 0 else "right",
            va="center",
            fontsize=15,
            fontweight="normal",
            linespacing=1.15,
            arrowprops={
                "arrowstyle": "-",
                "color": "#7A7A7A",
                "linewidth": 0.8,
                "shrinkA": 0,
                "shrinkB": 0,
                "connectionstyle": "arc3,rad=0.08",
            },
        )

    ax.legend(
        wedges,
        DISPLAY_LABELS,
        loc="upper right",
        bbox_to_anchor=(1.12, 1.02),
        frameon=False,
        fontsize=14,
        labelspacing=0.75,
        handlelength=1.0,
        handleheight=1.0,
        handletextpad=0.55,
        borderaxespad=0,
    )
    ax.set(aspect="equal")
    ax.set_xlim(-1.42, 2.05)
    ax.axis("off")
    fig.subplots_adjust(left=0.03, right=0.98, bottom=0.04, top=0.96)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    for suffix, options in (
        (".pdf", {}),
        (".svg", {}),
        (".png", {"dpi": 600}),
    ):
        fig.savefig(
            OUT.with_suffix(suffix),
            facecolor="white",
            bbox_inches="tight",
            pad_inches=0.04,
            **options,
        )
    plt.close(fig)


if __name__ == "__main__":
    main()
