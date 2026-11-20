"""Verify published artifacts and rederive statistics from saved scores only.

Never imports an agent harness or writes to frozen results. Python stdlib is
sufficient for checksums and means; --statistics additionally requires NumPy.
This is table-level reproduction, not rescoring of raw predictions.
"""
import argparse
from collections import defaultdict
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
METRICS = (
    "t2_required_family_f1", "t3_molecular_transition_f1",
    "t3_state_f1", "t3_typed_edge_f1",
)


def rows(relative):
    path = ROOT / "data" / relative
    with (gzip.open(path, "rt") if path.suffix == ".gz" else path.open()) as handle:
        yield from csv.DictReader(handle)


def mean(values):
    values = list(values)
    return math.fsum(values) / len(values)


def check(actual, expected, label):
    if not math.isclose(actual, float(expected), rel_tol=0, abs_tol=2e-12):
        raise ValueError(f"{label}: calculated {actual}, saved {expected}")


def verify_artifacts():
    manifest = json.loads((ROOT / "export_manifest.json").read_text())
    for entry in manifest["artifacts"]:
        path = ROOT / entry["path"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["published_sha256"]:
            raise ValueError(f"Checksum mismatch: {entry['path']}")
        plain = gzip.decompress(data) if path.suffix == ".gz" else data
        if hashlib.sha256(plain).hexdigest() != entry["uncompressed_sha256"]:
            raise ValueError(f"Content checksum mismatch: {entry['path']}")
    return len(manifest["artifacts"])


def main_means():
    groups = defaultdict(list)
    settings = set()
    for row in rows("main/results.csv.gz"):
        settings.add(row["config_id"])
        if row["config_id"] == "original":
            groups[row["panel"], row["group"]].append(row)
    if len(settings) != 640:
        raise ValueError(f"Expected 640 scoring configurations, found {len(settings)}")
    checked = 0
    for summary in rows("main/aggregates.csv"):
        metric = summary["metric"]
        group = groups[summary["panel"], summary["group"]]
        if metric == METRICS[0] and summary["policy"] == "primary":
            group = [r for r in group if float(r["required_families"]) > 0]
        check(len(group), summary["n_protocols"], summary["summary_id"])
        check(mean(float(r[metric]) for r in group), summary["mean"], summary["summary_id"])
        checked += 1
    return checked


def repeated_executions():
    executions = list(rows("repeats/execution_scores.csv"))
    if len(executions) != 60 or any(r["valid_completion"] != "True" for r in executions):
        raise ValueError("Expected 60 valid GPT/Codex executions")
    for summary in rows("repeats/summary.csv"):
        metric = summary["metric"]
        selected = [r for r in executions if metric != METRICS[0] or float(r["required_families"]) > 0]
        by_protocol = defaultdict(list)
        for r in selected:
            by_protocol[r["protocol_id"]].append(float(r[metric]))
        if any(len(v) != 3 for v in by_protocol.values()):
            raise ValueError("Expected three executions per protocol")
        check(len(by_protocol), summary["n_protocols"], metric)
        for attempt, column in [(1, "original_macro_f1"), (2, "repeat_earlier_macro_f1"), (3, "repeat_later_macro_f1")]:
            check(mean(float(r[metric]) for r in selected if r["group"] == f"attempt_{attempt}"), summary[column], column)
        average = mean(mean(v) for v in by_protocol.values())
        check(average, summary["three_attempt_macro_f1"], metric)
        check(average - float(summary["original_macro_f1"]), summary["difference"], metric)
        ranges = [max(v) - min(v) for v in by_protocol.values()]
        check(max(ranges), summary["max_protocol_range"], metric)
        check(sum(v > .1 for v in ranges), summary["protocols_range_gt_010"], metric)
    return len(executions)


def exact_p(d):
    import numpy as np
    sums = np.array([0.])
    for x in d:
        sums = np.concatenate((sums - x, sums + x))
    tolerance = 64 * np.finfo(float).eps * max(1., math.fsum(abs(x) for x in d))
    return float(np.mean(np.abs(sums) >= abs(math.fsum(d)) - tolerance))


def name_only(statistics=False):
    checked = 0
    for family in ("native", "task1"):
        data = list(rows(f"name_only/{family}_per_protocol.csv"))
        for summary in rows(f"name_only/{family}_statistics.csv"):
            if family == "native":
                selected = [r for r in data if r["system"] == summary["system"] and r["metric"] == summary["metric"]
                            and r["eligible"] == "True" and r["valid_pair"] == "True"]
                source, target, mean_column = "bundle", "name_only", "bundle_mean"
            else:
                selected = [r for r in data if r["model_family"] == summary["model"] and r["paired"] == "True"]
                source, target, mean_column = "pdf_similarity", "name_only_similarity", "pdf_mean"
                for key in ("pdf_exact", "name_only_exact"):
                    check(sum(r[key] == "True" for r in selected), summary[key], key)
            check(len(selected), summary["n_protocols"], family)
            check(mean(float(r[source]) for r in selected), summary[mean_column], family)
            check(mean(float(r[target]) for r in selected), summary["name_only_mean"], family)
            d = [float(r[target]) - float(r[source]) for r in selected]
            check(mean(d), summary["delta"], family)
            if statistics:
                import numpy as np
                p = exact_p(d)
                check(p, summary["p_raw"], family)
                check(min(1., int(summary["correction_family_size"]) * p), summary["p_bonferroni"], family)
                rng = np.random.default_rng(np.random.SeedSequence(json.loads(summary["bootstrap_seed"])))
                draws = rng.integers(0, len(d), size=(int(summary["bootstrap_resamples"]), len(d)))
                low, high = np.quantile(np.asarray(d)[draws].mean(axis=1), [.025, .975], method="linear")
                check(low, summary["ci_low"], family)
                check(high, summary["ci_high"], family)
            checked += 1
    return checked


def model_harness(statistics=False):
    data = list(rows("model_harness/protocol_switch_magnitudes.csv"))
    summaries = list(rows("bonferroni/model_harness_primary_bonferroni.csv"))
    for metric, summary in zip(METRICS, summaries):
        selected = [r for r in data if r["metric"] == metric]
        check(len(selected), summary["Protocols"], metric)
        for source, column in [("model_switch", "Model_switch"), ("harness_switch", "Harness_switch"), ("difference", "Difference")]:
            check(mean(float(r[source]) for r in selected), summary[column], metric)
        if statistics:
            from itertools import product
            import numpy as np
            d = np.array([float(r["difference"]) for r in selected])
            p = float(np.mean(np.abs(np.array(list(product([-1, 1], repeat=len(d)))) @ d / len(d)) >= abs(d.mean()) - 1e-14))
            check(p, summary["Signflip_p"], metric)
            check(min(1., 4 * p), summary["Bonferroni_p"], metric)
            draws = np.random.default_rng(np.random.SeedSequence([20261014, len(d)])).integers(len(d), size=(10000, len(d)))
            low, high = np.quantile(d[draws].mean(axis=1), [.025, .975])
            check(low, summary["CI_low"], metric)
            check(high, summary["CI_high"], metric)
    return len(summaries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--statistics", action="store_true", help="Also reproduce paired tests and CIs (NumPy required)")
    parser.add_argument("--output", type=Path, help="Optional NEW verification report; never overwrites a file")
    args = parser.parse_args()
    report = dict(
        verified_artifacts=verify_artifacts(),
        rederived_main_aggregate_means=main_means(),
        verified_repeated_executions=repeated_executions(),
        rederived_name_only_comparisons=name_only(args.statistics),
        rederived_model_harness_comparisons=model_harness(args.statistics),
        paired_tests_and_CIs_checked=args.statistics,
        scope="Saved-score arithmetic only; no agents, rescoring, input changes, or checkpoint selection",
    )
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
