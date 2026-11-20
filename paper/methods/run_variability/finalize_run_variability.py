"""Validate frozen Codex repeats and publish descriptive variability results.

No agent inference, mutable scoring overlays, hypothesis tests, or historical
artifact writes. Run with --output pointing to a new directory to reproduce.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
CAMERA = ROOT / "analysis/paper_results/camera_ready_v4_7_0"
PAPER = Path("external/manuscript")
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/libstruct-run-variability-mpl")
os.environ.setdefault("XDG_CACHE_HOME", "/private/tmp/libstruct-run-variability-cache")
METRICS = [
    "t2_required_family_f1", "t3_molecular_transition_f1",
    "t3_state_f1", "t3_typed_edge_f1",
]
LABELS = ["Oligo", "Transition", "State", "Typed edge"]
GROUPS = ["attempt_1", "attempt_2", "attempt_3"]
PDF_NAME = "figS_codex_execution_variability.pdf"


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def fresh_score(case):
    from libstruct_bench.libgen.v4_7_0.scoring import grade_libgen
    from libstruct_bench.libgen.prediction_validation import (
        validate_t2_prediction, validate_t3_prediction, validate_prediction_links,
    )
    assert case["truth"]["transform"] == "none"
    p2, p3 = load(case["p2"]), load(case["p3"])
    t2, t3 = (load(case["truth"]["paths"][t]) for t in ["T2", "T3"])
    validate_t2_prediction(p2, protocol_id=case["protocol_id"])
    validate_t3_prediction(p3, protocol_id=case["protocol_id"])
    validate_prediction_links(p2, p3)
    metrics, details = grade_libgen(p2, p3, t2, t3)
    return dict(case_id=case["case_id"], metrics=metrics, details=details, valid=True)


def plot(out, ranges, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    import numpy as np

    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.4,
        "ytick.labelsize": 7.4, "axes.linewidth": .6,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })
    fig, axes = plt.subplots(1, 2, figsize=(5.5, 3.05),
        gridspec_kw={"left": .085, "right": .985, "bottom": .23,
                     "top": .79, "wspace": .28})
    ax = axes[0]
    for x, metric in enumerate(METRICS):
        s = ranges[ranges.metric == metric].sort_values("protocol_id")
        # Fixed deterministic offsets display each protocol; no random sampling.
        jitter = np.linspace(-.19, .19, len(s))
        ax.scatter(x + jitter, s["range"], s=15, color="#438999",
                   alpha=.72, linewidths=.35, edgecolors="white", zorder=3)
        r = summary[summary.metric == metric].iloc[0]
        ax.plot([x, x], [r.range_q25, r.range_q75], color="#222222",
                lw=2.1, zorder=4)
        ax.plot([x-.11, x+.11], [r.range_q25]*2, color="#222222", lw=1, zorder=4)
        ax.plot([x-.11, x+.11], [r.range_q75]*2, color="#222222", lw=1, zorder=4)
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
    ], loc="upper center", bbox_to_anchor=(.5, -.19), ncol=2,
       fontsize=7, frameon=False, handlelength=1.2, columnspacing=1.1)

    ax = axes[1]
    styles = [
        ("original_macro_f1", "Original", "#277da8", "o", -.18),
        ("repeat_earlier_macro_f1", "Repeat 1", "#cf6846", "s", -.06),
        ("repeat_later_macro_f1", "Repeat 2", "#5b9861", "^", .06),
        ("three_attempt_macro_f1", "Three-execution mean", "#222222", "D", .18),
    ]
    for column, label, color, marker, offset in styles:
        vals = summary.set_index("metric").loc[METRICS, column].to_numpy()
        ax.scatter(np.arange(4)+offset, vals, label=label, marker=marker,
                   s=28 if marker=="D" else 23,
                   facecolor="white" if marker=="D" else color,
                   edgecolor=color, linewidth=.9, zorder=4)
    ax.set_ylim(.59, .85)
    ax.set_yticks([.60, .65, .70, .75, .80, .85])
    ax.set_ylabel("Protocol-macro F1")
    ax.set_title("Aggregate performance", fontsize=9, pad=27)
    ax.text(.5, 1.055, "Three saved executions per protocol", transform=ax.transAxes,
            ha="center", fontsize=7.4, color="#555555")
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -.19), ncol=2,
              fontsize=7, frameon=False, handlelength=1, columnspacing=1.2,
              handletextpad=.4, labelspacing=.4)
    for ax in axes:
        ax.set_xticks(range(4), LABELS)
        ax.set_xlim(-.45, 3.45)
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["bottom", "left"]].set_color("#777777")
        ax.grid(axis="y", color="#e5e5e5", linewidth=.5, zorder=0)
        ax.set_axisbelow(True)
    fig.savefig(out/PDF_NAME, format="pdf", facecolor="white", metadata={
        "Title": "LibStructBench: observed GPT/Codex execution variability",
        "Creator": "Read-only verified Codex repeat analysis",
        "CreationDate": None, "ModDate": None,
    })
    plt.close(fig)


def write_text(out, summary):
    rows = []
    for label, (_, r) in zip(LABELS, summary.iterrows()):
        rows.append(
            f"{label} & {r.original_macro_f1:.3f} & {r.three_attempt_macro_f1:.3f} & "
            f"{r.difference:+.3f} & {r.max_protocol_range:.3f} & "
            f"{int(r.protocols_range_gt_010)}/{int(r.n_protocols)} " + r"\\"
        )
    table = r"""\begin{table}[htbp]
\centering
\small
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.15}
\begin{tabularx}{\textwidth}{@{}Xrrrrr@{}}
\toprule
Metric & Original & Three-attempt mean & Difference & Max.\ range & Range $>0.10$ \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabularx}
\caption{\textbf{Observed variability across three GPT/Codex executions per protocol.}
Macro-F1 averages 19 informative protocols for oligo recovery and 20 for each
Task~3 metric. Difference is the three-attempt macro-F1 minus the original-attempt
macro-F1. Max.\ range is the largest within-protocol maximum-minus-minimum
across the three executions; the final column counts protocols exceeding a
range of 0.10. The original execution is included in the three-attempt mean.
These are descriptive comparisons, not significance or equivalence tests.}
\label{tab:execution-variability}
\end{table}
"""
    (out/"supplementary_table.tex").write_text(table)
    methods = r"""\section{Variability across repeated agent executions}
\label{app:execution-variability}

We assessed observed execution variability for GPT-5.6 Sol Max with Codex
using three saved executions on each of the 20 baseline protocols
(60 completed executions). All 60 outputs passed prediction and cross-task
validation and were evaluated with the same scoring rules and approved
ground-truth references as the main experiments. The original execution and
two additional executions used matching task prompts, Codex version 0.147.0,
and the requested maximum reasoning effort. The original executions ran on
16 August 2026 and the additional executions on 10 October 2026.
Runtime-injected context, including dates and plugin listings, differed.
These are observed repeated executions, not controlled random seeds.

For protocol $p$ and metric $m$, we calculated
$R_{pm}=\max_{r=1,2,3}F_{prm}-\min_{r=1,2,3}F_{prm}$.
We summarize these ranges by their median and interquartile range (IQR),
using linearly interpolated empirical quartiles, and count protocols with
$R_{pm}>0.10$. All protocols have equal weight. Task~2 excludes ddSEQ because
$N_{\mathrm{req}}=0$, giving 19 informative protocols; Task~3 retains all 20.
Execution-specific macro-F1 is $\bar F_{rm}=P_m^{-1}\sum_pF_{prm}$;
the three-execution estimate is
$\bar F_{\cdot m}=P_m^{-1}\sum_p(3^{-1}\sum_rF_{prm})$.
Repeat~1 and Repeat~2 denote the earlier and later additional execution
within each protocol, rather than synchronized experimental rounds.
The original execution is a constituent of the three-execution estimate,
so the two estimates are not treated as independent observations.
We report no hypothesis tests, equivalence tests, or confidence intervals
for this descriptive comparison.

\begin{figure}[htbp]
  \centering
  \includegraphics[width=\linewidth]{figures/figS_codex_execution_variability.pdf}
  \caption{\textbf{Observed GPT/Codex variability across three executions per protocol.}
  \textbf{Left:} Each point is one protocol's maximum-minus-minimum F1
  across its three executions. Open diamonds mark medians and black
  intervals the IQR; annotations count protocols with range exceeding 0.10
  (dashed line). \textbf{Right:} Protocol-macro F1 for the original execution,
  each additional execution, and their equally weighted mean.
  Repeat labels order the additional executions within each protocol;
  they are not shared seeds or synchronized rounds. The mean includes
  the original execution and is not an independent observation.
  Task~2 uses 19 informative protocols and Task~3 uses 20.
  Ranges and IQRs describe observed dispersion, not confidence intervals.}
  \label{fig:supp-execution-variability}
\end{figure}

"""
    metrics = summary.set_index("metric")
    values = "; ".join(
        f"{label.lower()} {r.original_macro_f1:.3f} to {r.three_attempt_macro_f1:.3f}"
        for label, (_, r) in zip(LABELS, summary.iterrows())
    )
    q = summary.iloc[3]
    results = (
        "Averaging the three executions changed the protocol-macro scores as follows: "
        + values + ". These aggregate changes coexisted with substantial protocol-specific "
        "variation. Oligo, transition, state, and typed-edge F1 ranges exceeded 0.10 "
        "for 5/19, 9/20, 8/20, and 12/20 protocols, respectively. "
        f"The largest observed typed-edge range was {q.max_protocol_range:.3f} "
        "for s3-ATAC. Table~\\ref{tab:execution-variability} summarizes the descriptive comparisons.\n\n"
    )
    limitations = r"""Only GPT/Codex has three executions per protocol in this analysis.
Figures~3a and~3b retain one selected attempt per system and protocol.
Protocol-bootstrap intervals in the main results describe across-protocol
uncertainty conditional on the selected attempts, rather than variability
across repeated executions. These results do not quantify run-level
variability for Claude, Gemini, Kimi, other harnesses, or the knowledge
and learning interventions. In particular, they do not provide matched
repeated $C_0$--$C_{30}$ evaluations. Modest changes in aggregate means do
not establish that one execution is sufficient, and variability from
this system cannot be used as an uncertainty estimate for other systems.
"""
    (out/"supplementary_section.tex").write_text(methods + table + "\n" + results + limitations)
    paragraph = r"""\paragraph{Variability across repeated executions.}
To assess observed execution variability, we evaluated three saved
GPT/Codex executions for each of the 20 baseline protocols.
Compared with the original attempt, averaging the three executions
changed oligo, transition, state, and typed-edge macro-F1 by
$-0.005$, $-0.014$, $-0.012$, and $-0.024$, respectively.
Nevertheless, within-protocol ranges exceeded 0.10 for 5/19 informative
oligo protocols and 9/20, 8/20, and 12/20 protocols for the three
Task~3 metrics. These descriptive results reveal execution variability
that aggregate means can obscure (Appendix~\ref{app:execution-variability}).
Figures~3a and~3b retain one selected attempt per system and protocol.
The repeats were not controlled random seeds, and this analysis neither
establishes that one execution is sufficient nor quantifies run-level
variability for other systems.
"""
    (out/"section_3_2_paragraph.tex").write_text(paragraph)
    (out/"README.txt").write_text(
        "FINALIZED GPT/CODEX EXECUTION-VARIABILITY ANALYSIS\n\n"
        "Figure: " + PDF_NAME + "\n"
        "Insert supplementary_section.tex near the end of the appendix; it includes\n"
        "the figure and supplementary table. supplementary_table.tex is a standalone\n"
        "copy, not an additional table to insert a second time. Insert\n"
        "section_3_2_paragraph.tex in Section 3.2 after the native comparison.\n"
        "The real manuscript and all existing figures remain untouched.\n\n"
        "Primary observations: execution_scores.csv (60 completed executions).\n"
        "Plot A: per_protocol_ranges.csv (79 informative protocol/metric rows).\n"
        "Plot B and table: summary.csv; all comparisons are descriptive.\n"
        "Zero-required Task 2: zero_required_executions.csv.\n"
        "Fresh scoring checks and manifest retain provenance; no agents were run.\n"
        "The two additional executions are ordered within each protocol, not\n"
        "synchronized replicate rounds. No new model ranking is calculated.\n\n"
        "Reproduce from the repository root, with a new output directory:\n"
        "PYTHONDONTWRITEBYTECODE=1 python analysis/paper_results/"
        "run_variability_v4_7_0/finalize_run_variability.py "
        "--output /private/tmp/libstruct-run-variability-reproduction\n"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent/"results_v1")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    out = args.output.resolve()
    assert not out.exists(), "Choose a new output directory; do not overwrite historical results."
    import numpy as np
    import pandas as pd
    lock = load(CAMERA/"input_manifest.json")
    extra = load(CAMERA/"extra_input_manifest.json")
    identity = load(CAMERA/"results_identity.json")
    prior = load(CAMERA.parent/"codex_replicate_sensitivity.json")
    cases = {c["case_id"]: c for c in extra["cases"]}
    members = [m for m in extra["members"] if m["panel"] == "codex_repeats"]
    assert len(cases) == len(members) == 60
    assert lock["verifier"] == "4.7.0"
    protected = {}

    def pin(path, expected=None):
        path = Path(path).resolve()
        digest = sha(path)
        if expected is not None:
            assert digest == expected, ("Input hash mismatch", str(path))
        protected[str(path)] = digest
        return digest

    for name in ["input_manifest.json", "extra_input_manifest.json", "results_identity.json",
                 "reference_consistency.csv", "selected_protocol_scores.csv",
                 "repeated_run_summary.csv", "repeated_run_per_protocol.csv"]:
        pin(CAMERA/name)
    pin(CAMERA/"results.csv", identity["sha256"])
    pin(CAMERA.parent/"codex_replicate_sensitivity.json")
    pin(PAPER/"main.tex")
    for folder in [PAPER/"figures", CAMERA/"figures"]:
        for path in folder.rglob("*"):
            if path.is_file():
                pin(path)
    # Guard the imported scorer and its repo-owned dependency closure.
    from libstruct_bench.libgen.v4_7_0 import scoring
    from libstruct_bench.libgen import prediction_validation
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if name.startswith("libstruct_bench") and file and str(file).endswith(".py"):
            pin(file, lock["inputs"][str(Path(file).resolve())])
    assert sha(scoring.__file__) == lock["scorer_sha256"]
    for path in (ROOT/"schemas/benchmark").glob("*.json"):
        pin(path, lock["inputs"].get(str(path)))
    assert scoring.T2_WEIGHTS == dict(sequence=.65, modifications=.15, kind=.10, orientation=.05, role=.05)
    assert scoring.STATE_WEIGHTS == dict(reference_strand=.5, architecture=.15, segments=.2, pairing=.15)
    assert scoring.TRANSITION_WEIGHTS == dict(operation=.3, substrates=.15, products=.2, disposition=.15, oligos=.2)
    assert scoring._T3_MIN_ASSIGNMENT_SCORE == .25
    main_cases = {c["protocol_id"]: c for c in lock["cases"]}
    prior_attempts = {
        str((ROOT/a["trial"]).resolve()): a
        for protocol in prior["protocols"] for a in protocol["attempts"]
    }
    refs = pd.read_csv(CAMERA/"reference_consistency.csv")
    protocols = sorted({m["protocol_id"] for m in members})
    assert len(protocols) == 20
    refs = refs[refs.protocol_id.isin(protocols)]
    assert len(refs) == 60
    for r in refs.to_dict("records"):
        pin(r["current_path"], r["current_sha256"])
        pin(r["approval_receipt"], lock["inputs"][r["approval_receipt"]])
    for member in members:
        case = cases[member["case_id"]]
        assert case["truth"] == main_cases[case["protocol_id"]]["truth"]
        for path in case["truth"]["paths"].values():
            pin(path, lock["inputs"][path])
        for key in ["p2", "p3"]:
            pin(case[key], extra["inputs"][case[key]])
        attempt = prior_attempts[member["trial"]]
        for inp in attempt["inputs"]:
            pin(ROOT/inp["path"], inp["sha256"])
        rr = load(Path(member["trial"])/"result.json")
        assert rr["exception_info"] is None
        agent = rr["config"]["agent"]
        assert agent["name"] == "codex" and agent["model_name"] == "gpt-5.6-sol"
        assert agent["kwargs"] == {"version": "0.147.0", "reasoning_effort": "max"}
        assert rr["agent_info"]["version"] == "0.147.0"
        trajectory = load(Path(member["trial"])/"agent/trajectory.json")
        prompts = [x["message"] for x in trajectory["steps"] if x["source"]=="user"
                   and x["message"].startswith("# T2/T3 protocol understanding:")]
        assert len(prompts) == 1
        assert hashlib.sha256(prompts[0].encode()).hexdigest() == member["task_prompt_sha256"]
        pin(CAMERA/"replicate_results"/(member["case_id"]+".json"))
    for protocol in protocols:
        rows = [m for m in members if m["protocol_id"] == protocol]
        assert {m["group"] for m in rows} == set(GROUPS)
        assert len({m["task_prompt_sha256"] for m in rows}) == 1
        repeat_dates = [next(m["started_at"] for m in rows if m["group"]==g) for g in GROUPS[1:]]
        assert repeat_dates == sorted(repeat_dates)
    columns = ["panel", "group", "protocol_id", "case_id", "config_id", "verifier",
               "scorer_sha256", "reference_set_id", "required_families",
               "prediction_t2_sha256", "prediction_t3_sha256",
               "reference_t1_sha256", "reference_t2_sha256", "reference_t3_sha256"] + METRICS
    master = pd.concat([
        c[(c.panel=="codex_repeats") & (c.config_id=="original")]
        for c in pd.read_csv(CAMERA/"results.csv", usecols=columns, chunksize=20000,
                             low_memory=False,
                             dtype={k: str for k in columns if k not in METRICS+["required_families"]})
    ])
    assert len(master) == 60 and master.case_id.nunique() == 60
    master = master.set_index("case_id")
    selected = pd.read_csv(CAMERA/"selected_protocol_scores.csv")
    native = selected[(selected.panel=="fig3a") & (selected.group=="gpt_codex")].set_index("protocol_id")
    out.mkdir(parents=True)
    (out/"fresh_scoring_checks").mkdir()
    checks, records = [], []
    by_id = {m["case_id"]: m for m in members}
    print("Verified prediction/reference/code hashes, prompts and execution settings for 60 cases.", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, result in enumerate(pool.map(fresh_score, cases.values()), 1):
            cid = result["case_id"]; case = cases[cid]; member = by_id[cid]
            previous = load(CAMERA/"replicate_results"/(cid+".json"))
            metric_error = max(abs(result["metrics"][m]-previous["metrics"][m]) for m in result["metrics"])
            assert metric_error < 1e-12 and result["details"] == previous["details"], cid
            row = master.loc[cid]
            assert row.verifier == "4.7.0" and row.scorer_sha256 == lock["scorer_sha256"]
            assert row.reference_set_id == lock["reference_set_id"]
            for key in METRICS:
                assert abs(row[key]-result["metrics"][key]) < 1e-12
            for task in ["t1", "t2", "t3"]:
                assert row["reference_"+task+"_sha256"] == sha(case["truth"]["paths"][task.upper()])
            for task, key in [("t2", "p2"), ("t3", "p3")]:
                assert row["prediction_"+task+"_sha256"] == sha(case[key])
            if member["group"] == "attempt_1":
                assert native.loc[case["protocol_id"], "trial"] == member["trial"]
                for key in METRICS:
                    assert abs(native.loc[case["protocol_id"], key]-result["metrics"][key]) < 1e-12
            t2 = result["details"]["diagnostic_metrics"]["t2"]
            assert row.required_families == t2["required_family_count"]
            record = {**member, "valid_completion": True, "verifier": "4.7.0",
                      "reference_set_id": lock["reference_set_id"],
                      "required_families": t2["required_family_count"],
                      "effective_predicted_families": t2["effective_prediction_family_count"],
                      **result["metrics"]}
            records.append(record)
            checks.append(dict(case_id=cid, protocol_id=case["protocol_id"], group=member["group"],
                maximum_saved_metric_error=metric_error, details_identical=True, master_agrees=True,
                valid_completion=True))
            dump(out/"fresh_scoring_checks"/(cid+".json"), result)
            if i % 10 == 0:
                print(f"Fresh unchanged-verifier checks: {i}/60 passed.", flush=True)
    scores = pd.DataFrame(records).sort_values(["protocol_id", "group"])
    ranges, summaries = [], []
    for metric, label in zip(METRICS, LABELS):
        rows = scores[scores.required_families>0] if metric==METRICS[0] else scores
        a = rows.pivot(index="protocol_id", columns="group", values=metric)[GROUPS].sort_index()
        assert a.shape == (19 if metric==METRICS[0] else 20, 3)
        span = a.max(axis=1)-a.min(axis=1)
        q25, median, q75 = np.quantile(span, [.25, .5, .75], method="linear")
        for protocol in a.index:
            ranges.append(dict(protocol_id=protocol, metric=metric,
                attempt_1=a.loc[protocol, "attempt_1"], attempt_2=a.loc[protocol, "attempt_2"],
                attempt_3=a.loc[protocol, "attempt_3"], range=span[protocol],
                mean=a.loc[protocol].mean(), range_gt_010=bool(span[protocol]>.1)))
        summaries.append(dict(metric=metric, label=label, n_protocols=len(a),
            original_macro_f1=a.attempt_1.mean(), repeat_earlier_macro_f1=a.attempt_2.mean(),
            repeat_later_macro_f1=a.attempt_3.mean(), three_attempt_macro_f1=a.mean(axis=1).mean(),
            difference=(a.mean(axis=1)-a.attempt_1).mean(), max_protocol_range=span.max(),
            protocols_range_gt_010=int((span>.1).sum()), range_q25=q25, range_median=median,
            range_q75=q75, max_range_protocols=";".join(span.index[span==span.max()])))
    ranges = pd.DataFrame(ranges); summary = pd.DataFrame(summaries)
    historical = pd.read_csv(CAMERA/"repeated_run_summary.csv").query("policy=='primary'").set_index("metric")
    for row in summary.to_dict("records"):
        h = historical.loc[row["metric"]]
        for key, old in [("original_macro_f1","original_mean"),("three_attempt_macro_f1","mean_three_attempts"),
                         ("difference","n3_minus_original"),("max_protocol_range","max_protocol_range"),
                         ("protocols_range_gt_010","protocols_range_gt_point1")]:
            assert np.isclose(row[key], h[old], atol=1e-12, rtol=0)
    for name, frame in [
        ("execution_scores.csv", scores), ("per_protocol_ranges.csv", ranges),
        ("summary.csv", summary), ("verification_cases.csv", pd.DataFrame(checks)),
        ("reference_verification.csv", refs),
        ("zero_required_executions.csv", scores[scores.required_families==0]),
    ]:
        frame.to_csv(out/name, index=False, float_format="%.17g")
    assert set(scores[scores.required_families==0].protocol_id) == {"ddseq_single_cell_3_rna_seq_kit"}
    plot(out, ranges, summary)
    write_text(out, summary)
    import fitz
    doc = fitz.open(out/PDF_NAME)
    assert len(doc)==1 and not doc[0].get_images()
    for block in doc[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                box = fitz.Rect(span["bbox"])
                assert doc[0].rect.contains(box), span["text"]
    preview = Path("/private/tmp/libstruct-run-variability-preview.png")
    doc[0].get_pixmap(matrix=fitz.Matrix(2.5, 2.5)).save(preview)
    assert all(sha(path)==digest for path, digest in protected.items())
    dump(out/"input_manifest.json", dict(
        reference_set_id=lock["reference_set_id"], verifier="4.7.0", scorer_sha256=lock["scorer_sha256"],
        cases=list(cases.values()), members=members, protected_sha256=protected,
        approval_policy="Unchanged hashes and approval receipts from camera-ready reference-consistency gate.",
    ))
    dump(out/"verification.json", dict(
        executions=60, valid_completions=60, invalid_completions=0, protocols=20,
        primary_task2_protocols=19, task3_protocols=20, fresh_score_checks_passed=60,
        maximum_saved_metric_error=max(c["maximum_saved_metric_error"] for c in checks),
        all_detailed_scores_identical=True, camera_ready_master_table_agrees=True,
        original_attempts_match_figure3a=True, protected_files_unchanged=True,
        vector_pdf=True, pdf_text_within_page=True, preview_path=str(preview),
        no_agent_inference=True, no_hypothesis_or_equivalence_tests=True,
        historical_C30_unchanged=True, no_model_rankings_recomputed=True,
    ))
    dump(out/"reproducibility_manifest.json", dict(
        python=sys.version, platform=platform.platform(), script_sha256=sha(__file__),
        script_path=str(Path(__file__).resolve()),
        packages={p:importlib.metadata.version(p) for p in ["numpy","pandas","matplotlib","jsonschema","PyMuPDF"]},
        quartile_method="numpy.quantile(method='linear')", jitter="deterministic equally spaced offsets",
        random_seeds=None, hypothesis_tests=None, confidence_intervals=None,
        repetition_order="original, earlier additional execution, later additional execution within protocol",
        figureA="One range/protocol/metric, median and IQR; strict range > 0.10",
        figureB="Equal-weight protocol macro for each attempt index and within-protocol three-attempt means",
        outputs={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob("*")) if p.is_file()},
    ))
    print(summary.round(6).to_string(index=False), flush=True)
    print(f"Complete: {out}\nPreview: {preview}", flush=True)


if __name__ == "__main__":
    main()
