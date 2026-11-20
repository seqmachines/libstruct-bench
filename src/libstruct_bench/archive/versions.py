"""Pick the newest attributable verifier result without running a verifier or rewriting history."""

from __future__ import annotations

import re

from .discovery import read_json
from .storage import sha256

VERSION = re.compile(r"(?:libgen-)?(\d+)\.(\d+)\.(\d+)\Z")
METRICS = (
    "reward",
    "t2_exact_required_family_recall",
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)


def version(value):
    match = VERSION.fullmatch(str(value))
    return tuple(map(int, match.groups())) if match else ()


def latest(path, paper, study):
    verifier = path / "verifier"
    details = read_json(verifier / "details.json")
    original = details.get("benchmark_version")
    options = [(version(original), 0, "execution", verifier, original, None)]
    rescores = verifier / "rescore"
    if rescores.is_dir() and not rescores.is_symlink():
        for d in sorted(rescores.iterdir()):
            if d.is_symlink() or not d.is_dir():
                continue
            tag = read_json(d / "details.json").get(
                "benchmark_version"
            ) or d.name.removeprefix("libgen-")
            if version(tag) and any(
                (d / n).is_file() for n in ("details.json", "reward.json", "error.json")
            ):
                options.append((version(tag), 1, "rescore", d, tag, None))
    pins = study.prediction_pins.get(path.relative_to(study.runs).as_posix(), {})
    if any(
        not (path / name).is_file()
        or (path / name).resolve() != path / name
        or sha256(path / name) != expected
        for name, expected in pins.items()
    ):
        paper = []
    for item in paper:
        row, table = item["row"], item["table"]
        tag = row.get("verifier")
        if table.startswith("name_only/"):
            tag = study.name_meta.get("scorer_version")
        if table == "main/task1_recomputed":
            tag = "task1-paper-reference"
            if not row.get("prediction_sha256") or row[
                "prediction_sha256"
            ] != prediction_hash(path, row.get("prediction_path")):
                continue
        elif not version(tag):
            continue
        if any(
            row.get("prediction_" + t + "_sha256")
            and row["prediction_" + t + "_sha256"]
            != prediction_hash(path, row.get("prediction_" + t + "_path"))
            for t in ("t2", "t3")
        ):
            continue
        options.append((version(tag), 2, "paper", None, tag, item))
    selected = max(options, key=lambda a: a[:2])
    _, _, kind, directory, tag, primary = selected
    metrics = {}
    if kind == "paper":
        for opt in options:
            if opt[2] != "paper" or opt[4] != tag:
                continue
            row = opt[5]["row"]
            for k in (*METRICS, "sequence_similarity", "exact_match"):
                if row.get(k) not in (None, ""):
                    v = row[k]
                    metrics.setdefault(
                        k, v == "True" if v in {"True", "False"} else float(v)
                    )
            if opt[5]["table"].startswith("name_only/"):
                field = "name_only" if opt[5]["column"] == "name_trial" else "bundle"
                if row.get(field):
                    metrics[row["metric"]] = float(row[field])
    else:
        metrics = read_json(directory / "reward.json")
    return {
        "version": tag,
        "origin": kind,
        "metrics": metrics,
        "superseded_versions": sorted(
            {str(o[4]) for o in options if o[4] and o[4] != tag}, key=version
        ),
    }, directory


def prediction_hash(path, recorded):
    if not recorded:
        return None
    # Resolve only paths below this trial, including old repo/ and absolute prefixes.
    marker = "/" + path.name + "/"
    if marker not in recorded:
        return None
    f = path / recorded.split(marker, 1)[1]
    return (
        sha256(f)
        if f.is_file() and f.resolve() == f and f.is_relative_to(path)
        else None
    )


def frozen_results(study, paper, selected):
    """Resolve the frozen evaluation files selected by case ID and recorded hash."""
    if selected["origin"] != "paper":
        return []
    results = {}
    for ref in paper:
        row = ref["row"]
        case = row.get("case_id") or row.get(
            "name_case_id" if ref["column"] == "name_trial" else "bundle_case_id"
        )
        if not case or not re.fullmatch(r"[0-9a-f]{8,64}", case):
            continue
        names = [
            f"verifier_corrected_v4_7_0/case_results/{case}.baseline.json",
            f"camera_ready_v4_7_0/replicate_results/{case}.json",
            f"name_only_v4_7_0/results_v1/case_results/{case}.json",
        ]
        if row.get("name_is_retry") == "True" and ref["column"] == "name_trial":
            names.append(
                "name_only_v4_7_0/results_share_seq_retry_v1/retry_scored_v4_7_0.json"
            )
        for name in names:
            rel = "analysis/paper_results/" + name
            expected = study.attested_files.get("repo/" + rel)
            p = study.repo / rel
            if expected and p.resolve() == p and p.is_file() and sha256(p) == expected:
                results[rel] = ("evaluation/" + name, p, "evaluation", expected)
    return list(results.values())
