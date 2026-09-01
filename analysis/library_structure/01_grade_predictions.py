#!/usr/bin/env python3
"""Grade every direct_api and coding_agent library-structure prediction uniformly.

Emits a tidy long-format CSV + JSON to the output dir, plus a summary.
Cross-validates coding-agent grades against the in-container verifier/reward.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path("/Users/seqmachines/playground/libstruct-bench")
sys.path.insert(0, str(REPO / "src"))

from libstruct_bench.library_structure import (  # noqa: E402
    grade_library_prediction,
    zero_metrics,
)

parser = argparse.ArgumentParser()
parser.add_argument(
    "output_root",
    nargs="?",
    default=REPO / "analysis" / "library_structure",
    type=Path,
)
parser.add_argument(
    "--groundtruth-root",
    default=REPO / "analysis" / "library_structure" / "groundtruth",
    type=Path,
)
args = parser.parse_args()

OUT = args.output_root.resolve()
OUT.mkdir(parents=True, exist_ok=True)
GROUNDTRUTH_ROOT = args.groundtruth_root.resolve()

RUNS = REPO / "runs" / "library_structure"

# AI -> (direct_api dir name, coding_agent dir name)
AIS = {
    "Claude": ("anthropic-claude-opus-4.8", "library-structure-claude-code-opus-xhigh"),
    "GPT": ("openai-gpt-5.5", "library-structure-codex-gpt55-xhigh"),
    "Gemini": ("google-gemini-3.1-pro-preview", "library-structure-gemini-cli-31pro-high"),
}

# ---- frozen Task 1 ground truth --------------------------------------------
manifest = json.loads((REPO / "benchmarks/library_structure/protocols.json").read_text())
PROTOCOLS = manifest["protocols"]
DISPLAY = {p["protocol_id"]: p["display_name"] for p in PROTOCOLS}

ground_truth: dict[str, object] = {}
ground_truth_path: dict[str, Path] = {}
ground_truth_sha256: dict[str, str] = {}
for p in PROTOCOLS:
    pid = p["protocol_id"]
    candidates = (
        GROUNDTRUTH_ROOT / f"{pid}.json",
        GROUNDTRUTH_ROOT / pid / "groundtruth_final_lib_struct.json",
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        expected = " or ".join(str(candidate) for candidate in candidates)
        raise FileNotFoundError(f"missing frozen Task 1 ground truth: {expected}")
    raw = path.read_bytes()
    ground_truth[pid] = json.loads(raw)
    ground_truth_path[pid] = path
    ground_truth_sha256[pid] = hashlib.sha256(raw).hexdigest()
print(f"frozen Task 1 ground truth ready for {len(ground_truth)} protocols")


def grade(pred_doc, pid):
    """Return (sequence_similarity, metrics) or raise."""
    metrics, _audit = grade_library_prediction(pred_doc, ground_truth[pid], expected_protocol_id=pid)
    return metrics


def apply_metrics(row, metrics):
    row.update(
        status="ok",
        parse_valid=int(metrics["prediction_parse_valid"]),
        sequence_similarity=metrics["sequence_similarity"],
        matched_sequence_similarity=metrics["matched_sequence_similarity"],
        library_f1=metrics["library_f1"],
        library_precision=metrics["library_precision"],
        library_recall=metrics["library_recall"],
        edit_distance=metrics["edit_distance"],
        predicted_library_count=metrics["predicted_library_count"],
        ground_truth_library_count=metrics["ground_truth_library_count"],
    )


rows = []  # tidy long format

# ---- direct_api -----------------------------------------------------------
for ai, (api_dir, _agent_dir) in AIS.items():
    base = RUNS / "direct_api" / api_dir
    for pid in DISPLAY:
        pdir = base / pid
        row = {
            "ai": ai, "method": "API", "protocol": pid, "protocol_display": DISPLAY[pid],
            "groundtruth_path": str(ground_truth_path[pid]),
            "groundtruth_sha256": ground_truth_sha256[pid],
            "status": "missing", "sequence_similarity": None, "matched_sequence_similarity": None,
            "library_f1": None, "library_precision": None, "library_recall": None,
            "edit_distance": None, "predicted_library_count": None,
            "ground_truth_library_count": None, "parse_valid": None, "fail_reason": None,
        }
        if not pdir.is_dir():
            row["fail_reason"] = "no_dir"
            rows.append(row)
            continue
        files = set(os.listdir(pdir))
        pred_file = pdir / "prediction.json"
        if not pred_file.exists():
            row["status"] = "failed"
            if "error.json" in files:
                err = json.loads((pdir / "error.json").read_text())
                row["fail_reason"] = "api_error:" + str(err.get("error", ""))[:80]
            else:
                row["fail_reason"] = "no_prediction"
            rows.append(row)
            continue
        try:
            pred = json.loads(pred_file.read_text())
            m = grade(pred, pid)
            apply_metrics(row, m)
        except Exception as exc:  # noqa: BLE001
            apply_metrics(row, zero_metrics())
            row["fail_reason"] = "unscorable_prediction:" + str(exc)[:80]
        rows.append(row)

# ---- coding_agent (re-grade uniformly) -------------------------------------
for ai, (_api_dir, agent_dir) in AIS.items():
    base = RUNS / "coding_agent" / agent_dir
    # map protocol_id -> run dir via authoritative result.json task path
    # (dir names are truncated, e.g. 10x_chromium_3_gene_expression_v4 -> ...v)
    run_dirs = {}
    for d in sorted(base.iterdir()):
        rj = d / "result.json"
        if not d.is_dir() or not rj.exists():
            continue
        pid = os.path.basename(json.loads(rj.read_text())["config"]["task"]["path"])
        if pid in DISPLAY:
            run_dirs[pid] = d
    for pid in DISPLAY:
        row = {
            "ai": ai, "method": "Agent", "protocol": pid, "protocol_display": DISPLAY[pid],
            "groundtruth_path": str(ground_truth_path[pid]),
            "groundtruth_sha256": ground_truth_sha256[pid],
            "status": "missing", "sequence_similarity": None, "matched_sequence_similarity": None,
            "library_f1": None, "library_precision": None, "library_recall": None,
            "edit_distance": None, "predicted_library_count": None,
            "ground_truth_library_count": None, "parse_valid": None, "fail_reason": None,
        }
        d = run_dirs.get(pid)
        if d is None:
            row["fail_reason"] = "no_dir"
            rows.append(row)
            continue
        pred_file = d / "artifacts" / "prediction.json"
        if not pred_file.exists():
            row["status"] = "failed"
            row["fail_reason"] = "no_prediction"
            rows.append(row)
            continue
        try:
            pred = json.loads(pred_file.read_text())
            m = grade(pred, pid)
            apply_metrics(row, m)
        except Exception as exc:  # noqa: BLE001
            # A completed but invalid prediction is a scientific score of zero,
            # not a missing infrastructure result.
            apply_metrics(row, zero_metrics())
            row["fail_reason"] = "unscorable_prediction:" + str(exc)[:80]
        rows.append(row)

# ---- write outputs --------------------------------------------------------
cols = ["ai", "method", "protocol", "protocol_display", "groundtruth_path",
        "groundtruth_sha256", "status", "parse_valid",
        "sequence_similarity", "matched_sequence_similarity", "library_f1", "library_precision",
        "library_recall", "edit_distance", "predicted_library_count",
        "ground_truth_library_count", "fail_reason"]
with (OUT / "grades_long.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow(r)
(OUT / "grades_long.json").write_text(json.dumps(rows, indent=2))

# ---- summary --------------------------------------------------------------
print("\n=== per AI x method: ok / failed / missing counts ===")
c = Counter((r["ai"], r["method"], r["status"]) for r in rows)
for ai in AIS:
    for method in ("API", "Agent"):
        line = " ".join(f"{s}={c[(ai,method,s)]}" for s in ("ok","failed","missing"))
        print(f"  {ai:7s} {method:5s}  {line}")

print("\n=== API failures (to be removed) ===")
for r in rows:
    if r["method"] == "API" and r["status"] != "ok":
        print(f"  {r['ai']:7s} {r['protocol']:35s} {r['status']:8s} {r['fail_reason']}")

print(f"\nwrote {OUT/'grades_long.csv'}  ({len(rows)} rows)")
