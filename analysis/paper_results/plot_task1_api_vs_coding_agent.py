#!/usr/bin/env python3
"""Plot the paired Task 1 comparison of direct APIs and coding agents."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
DATA = HERE / "task1_grades_long.csv"
OUT = HERE / "figures" / "fig2_task1_api_vs_coding_agent"

MODELS = (
    ("Claude", "Claude Opus 4.8"),
    ("GPT", "GPT-5.5"),
    ("Gemini", "Gemini 3.1 Pro"),
)

POINT_COLOR = "#4682B4"
GRID_COLOR = "#E1E4E8"
SPINE_COLOR = "#A6A6A6"
TEXT_COLOR = "#202124"


def load_rows() -> list[dict[str, str]]:
    with DATA.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def paired_scores(
    rows: list[dict[str, str]], model_family: str
) -> list[tuple[float, float]]:
    by_key = {
        (row["method"], row["protocol"]): row
        for row in rows
        if row["ai"] == model_family
    }
    pairs: list[tuple[float, float]] = []
    protocols = sorted({protocol for _, protocol in by_key})
    for protocol in protocols:
        api = by_key.get(("API", protocol))
        agent = by_key.get(("Agent", protocol))
        if not api or not agent or api["status"] != "ok" or agent["status"] != "ok":
            continue
        pairs.append((float(api["sequence_similarity"]), float(agent["sequence_similarity"])))
    return pairs


def draw_panel(ax: plt.Axes, pairs: list[tuple[float, float]], title: str) -> None:
    ax.plot(
        [0, 1],
        [0, 1],
        color=SPINE_COLOR,
        linewidth=1.5,
        linestyle=(0, (4, 3)),
        zorder=1,
    )

    # Aggregate identical coordinates so repeated exact/tied results remain visible.
    grouped = Counter(
        (round(api_score, 12), round(agent_score, 12))
        for api_score, agent_score in pairs
    )
    for (api_score, agent_score), count in grouped.items():
        ax.scatter(
            api_score,
            agent_score,
            s=70 + 30 * (count - 1),
            color=POINT_COLOR,
            edgecolor="white",
            linewidth=0.9,
            alpha=0.92,
            zorder=3,
        )
    ax.set_title(title, fontsize=14.5, fontweight="normal", pad=8)
    ax.set_xlim(-0.025, 1.035)
    ax.set_ylim(-0.025, 1.035)
    ax.set_aspect("equal", adjustable="box")
    ticks = (0, 0.25, 0.50, 0.75, 1.00)
    tick_labels = ("0", "0.25", "0.50", "0.75", "1")
    ax.set_xticks(ticks, tick_labels)
    ax.set_yticks(ticks, tick_labels)
    ax.tick_params(labelsize=12.5, length=4, width=1)
    ax.grid(True, color=GRID_COLOR, linewidth=0.9)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(SPINE_COLOR)
    ax.spines["left"].set_color(SPINE_COLOR)


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
            "svg.fonttype": "none",
        }
    )

    rows = load_rows()
    fig, axes = plt.subplots(1, 3, figsize=(9.2, 3.35), sharex=True, sharey=True)
    for ax, (model_family, display_name) in zip(axes, MODELS):
        draw_panel(ax, paired_scores(rows, model_family), display_name)

    fig.supxlabel("Direct API sequence similarity", fontsize=14, y=0.025)
    fig.supylabel("Coding-agent sequence similarity", fontsize=14, x=0.018)
    fig.subplots_adjust(left=0.09, right=0.99, bottom=0.21, top=0.90, wspace=0.18)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        OUT.with_suffix(".pdf"),
        facecolor="white",
        bbox_inches="tight",
        pad_inches=0.04,
    )
    plt.close(fig)


if __name__ == "__main__":
    main()
