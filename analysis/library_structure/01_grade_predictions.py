#!/usr/bin/env python3
"""Grade every direct_api and coding_agent library-structure prediction uniformly.

Emits a tidy long-format CSV + JSON to the output dir, plus a summary.
Cross-validates coding-agent grades against the in-container verifier/reward.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path("/Users/seqmachines/playground/libstruct-bench")
sys.path.insert(0, str(REPO / "src"))

from libstruct_bench.hf_io import env_token, load_hf_json  # noqa: E402
from libstruct_bench.library_structure import (  # noqa: E402
    LibraryStructureValidationError,
    grade_library_prediction,
)

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "analysis" / "library_structure"
OUT.mkdir(parents=True, exist_ok=True)
GT_CACHE = OUT / "groundtruth"
GT_CACHE.mkdir(exist_ok=True)

RUNS = REPO / "runs" / "library_structure"

# AI -> (direct_api dir name, coding_agent dir name)
AIS = {
    "Claude": ("anthropic-claude-opus-4.8", "library-structure-claude-code-opus-xhigh"),
    "GPT": ("openai-gpt-5.5", "library-structure-codex-gpt55-xhigh"),
    "Gemini": ("google-gemini-3.1-pro-preview", "library-structure-gemini-cli-31pro-high"),
}

# ---- ground truth ---------------------------------------------------------
manifest = json.loads((REPO / "benchmarks/library_structure/protocols.json").read_text())
GT_REPO = manifest["groundtruth_repo"]
GT_REV = manifest.get("groundtruth_revision", "main")
PROTOCOLS = manifest["protocols"]
DISPLAY = {p["protocol_id"]: p["display_name"] for p in PROTOCOLS}
GT_PATH = {p["protocol_id"]: p["groundtruth_path"] for p in PROTOCOLS}

token = env_token()
ground_truth: dict[str, object] = {}
for p in PROTOCOLS:
    pid = p["protocol_id"]
    cache = GT_CACHE / f"{pid}.json"
    if cache.exists():
        ground_truth[pid] = json.loads(cache.read_text())
        continue
    doc = load_hf_json(repo_id=GT_REPO, path=GT_PATH[pid], revision=GT_REV, token=token)
    cache.write_text(json.dumps(doc, indent=2))
    ground_truth[pid] = doc
    print(f"fetched GT {pid}")
print(f"ground truth ready for {len(ground_truth)} protocols")


def grade(pred_doc, pid):
    """Return (sequence_similarity, metrics) or raise."""
    metrics, _audit = grade_library_prediction(pred_doc, ground_truth[pid], expected_protocol_id=pid)
    return metrics


rows = []  # tidy long format

# ---- direct_api -----------------------------------------------------------
for ai, (api_dir, _agent_dir) in AIS.items():
    base = RUNS / "direct_api" / api_dir
    for pid in DISPLAY:
        pdir = base / pid
        row = {
            "ai": ai, "method": "API", "protocol": pid, "protocol_display": DISPLAY[pid],
            "status": "missing", "sequence_similarity": None, "matched_sequence_similarity": None,
            "library_f1": None, "library_precision": None, "library_recall": None,
            "edit_distance": None, "predicted_library_count": None,
            "ground_truth_library_count": None, "parse_valid": None, "fail_reason": None,
        }
        if not pdir.is_dir():
            row["fail_reason"] = "no_dir"
            rows.append(row); continue
        files = set(os.listdir(pdir))
        pred_file = pdir / "prediction.json"
        if not pred_file.exists():
            row["status"] = "failed"
            if "error.json" in files:
                err = json.loads((pdir / "error.json").read_text())
                row["fail_reason"] = "api_error:" + str(err.get("error", ""))[:80]
            else:
                row["fail_reason"] = "no_prediction"
            rows.append(row); continue
        try:
            pred = json.loads(pred_file.read_text())
            m = grade(pred, pid)
            row.update(status="ok", parse_valid=1, sequence_similarity=m["sequence_similarity"],
                       matched_sequence_similarity=m["matched_sequence_similarity"],
                       library_f1=m["library_f1"], library_precision=m["library_precision"],
                       library_recall=m["library_recall"], edit_distance=m["edit_distance"],
                       predicted_library_count=m["predicted_library_count"],
                       ground_truth_library_count=m["ground_truth_library_count"])
        except (LibraryStructureValidationError, Exception) as exc:  # noqa: BLE001
            row["status"] = "failed"
            row["fail_reason"] = "parse_grade_error:" + str(exc)[:80]
        rows.append(row)

# ---- coding_agent (re-grade uniformly + cross-check reward.json) ----------
mismatches = []
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
            "status": "missing", "sequence_similarity": None, "matched_sequence_similarity": None,
            "library_f1": None, "library_precision": None, "library_recall": None,
            "edit_distance": None, "predicted_library_count": None,
            "ground_truth_library_count": None, "parse_valid": None, "fail_reason": None,
        }
        d = run_dirs.get(pid)
        if d is None:
            row["fail_reason"] = "no_dir"; rows.append(row); continue
        pred_file = d / "artifacts" / "prediction.json"
        reward_file = d / "verifier" / "reward.json"
        reward = json.loads(reward_file.read_text()) if reward_file.exists() else None
        if not pred_file.exists():
            row["status"] = "failed"; row["fail_reason"] = "no_prediction"; rows.append(row); continue
        try:
            pred = json.loads(pred_file.read_text())
            m = grade(pred, pid)
            row.update(status="ok", parse_valid=1, sequence_similarity=m["sequence_similarity"],
                       matched_sequence_similarity=m["matched_sequence_similarity"],
                       library_f1=m["library_f1"], library_precision=m["library_precision"],
                       library_recall=m["library_recall"], edit_distance=m["edit_distance"],
                       predicted_library_count=m["predicted_library_count"],
                       ground_truth_library_count=m["ground_truth_library_count"])
            if reward is not None and reward.get("prediction_parse_valid") == 1.0:
                diff = abs(reward["sequence_similarity"] - m["sequence_similarity"])
                if diff > 1e-6:
                    mismatches.append((ai, pid, reward["sequence_similarity"], m["sequence_similarity"]))
        except Exception as exc:  # noqa: BLE001
            # Agent RAN but produced an unscorable prediction (e.g. empty libraries).
            # The benchmark scores this 0.0 (prediction_parse_valid=0). It's a real
            # performance result, NOT an infra failure, so keep it as a 0.0 data point.
            if reward is not None:
                row.update(status="ok", parse_valid=0,
                           sequence_similarity=reward["sequence_similarity"],
                           matched_sequence_similarity=reward.get("matched_sequence_similarity"),
                           library_f1=reward.get("library_f1"), edit_distance=reward.get("edit_distance"),
                           predicted_library_count=reward.get("predicted_library_count"),
                           ground_truth_library_count=reward.get("ground_truth_library_count"),
                           fail_reason="unscorable_pred:" + str(exc)[:60])
            else:
                row["status"] = "failed"; row["fail_reason"] = "parse_grade_error:" + str(exc)[:80]
        rows.append(row)

# ---- write outputs --------------------------------------------------------
import csv

cols = ["ai", "method", "protocol", "protocol_display", "status", "parse_valid",
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
print("\n=== cross-check vs verifier/reward.json ===")
if mismatches:
    for ai, pid, a, b in mismatches:
        print(f"  MISMATCH {ai:7s} {pid:35s} reward={a:.4f} regrade={b:.4f}")
else:
    print("  all coding-agent re-grades match reward.json (<=1e-6)")

print("\n=== per AI x method: ok / failed / missing counts ===")
from collections import Counter
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
