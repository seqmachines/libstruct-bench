from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from libstruct_bench.libgen.error_analysis import (
    substantive_review_complete,
    task_bundle_sha256,
)


STATUS_SCHEMA_VERSION = "libstruct.libgen_pilot_review_status.v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate adjudicated Libgen pilot discrepancies and emit the gate "
            "used before planning the full production matrix."
        )
    )
    parser.add_argument("--review-root", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--schema",
        default="schemas/analysis/libgen_error_analysis.schema.json",
    )
    parser.add_argument("--record-refreeze", action="store_true")
    parser.add_argument("--recorded-by")
    parser.add_argument("--refreeze-notes")
    args = parser.parse_args(argv)

    if args.record_refreeze and not args.recorded_by:
        raise ValueError("--recorded-by is required with --record-refreeze")

    review_root = Path(args.review_root)
    manifest_path = review_root / "review_manifest.json"
    manifest = _load_json(manifest_path)
    schema = _load_json(Path(args.schema))
    validator = Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    issues: list[str] = list(manifest.get("preservation_issues", []))
    expected = int(manifest.get("expected_trial_count", 0))
    trials = manifest.get("trials", [])
    if expected != 60:
        issues.append(f"pilot manifest expects {expected} trials instead of 60")
    if len(trials) != expected:
        issues.append(f"pilot manifest contains {len(trials)}/{expected} trials")

    analyses: list[tuple[Path, dict[str, Any]]] = []
    for trial in trials:
        path = review_root / trial["analysis_path"]
        try:
            document = _load_json(path)
            validator.validate(document)
        except Exception as error:
            issues.append(f"{path}: invalid error-analysis record: {error}")
            continue
        analyses.append((path, document))
        issues.extend(_review_issues(path, document))

    tasks_root = Path(args.tasks)
    all_task_ids = sorted(
        path.name
        for path in tasks_root.iterdir()
        if path.is_dir() and (path / "task.toml").is_file()
    )
    if len(all_task_ids) != 20:
        issues.append(
            f"post-pilot benchmark freeze requires 20 generated tasks; found {len(all_task_ids)}"
        )
    current_task_digest = task_bundle_sha256(tasks_root, all_task_ids)
    if args.record_refreeze and not issues:
        manifest["benchmark_refreeze"] = {
            "status": "complete",
            "task_bundle_sha256": current_task_digest,
            "recorded_by": args.recorded_by,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "notes": args.refreeze_notes,
        }
        _write_json(manifest_path, manifest)

    refreeze = manifest.get("benchmark_refreeze", {})
    if refreeze.get("status") != "complete":
        issues.append("the benchmark has not been recorded as refrozen after pilot review")
    elif refreeze.get("task_bundle_sha256") != current_task_digest:
        issues.append(
            "the recorded post-review benchmark digest does not match the current tasks"
        )

    status = _status_document(
        manifest_path=manifest_path,
        manifest=manifest,
        analyses=analyses,
        task_bundle_digest=current_task_digest,
        issues=issues,
    )
    _write_json(Path(args.out), status)
    print(
        f"reviewed {len(analyses)}/{expected} pilot trials; "
        f"full_run_ready={str(status['full_run_ready']).lower()}"
    )
    if issues:
        print(f"pilot gate found {len(issues)} unresolved issue(s)")
    return 0 if status["full_run_ready"] else 1


def _review_issues(path: Path, document: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if document.get("run_outcome") in {
        "infrastructure_failure",
        "verifier_failure",
        "unknown",
    }:
        issues.append(
            f"{path}: unresolved non-scientific run outcome "
            f"{document.get('run_outcome')!r}"
        )

    substantive = [
        item for item in document.get("observations", []) if item.get("substantive")
    ]
    if substantive and document.get("review_status") != "complete":
        issues.append(f"{path}: top-level review_status is not complete")
    if not substantive_review_complete(document):
        issues.append(
            f"{path}: substantive observations still have unresolved validity, "
            "attribution, or adjudication"
        )
    for observation in substantive:
        if not observation.get("adjudication_notes"):
            issues.append(
                f"{path}: {observation.get('error_id')} lacks adjudication notes"
            )
        if not observation.get("adjudicated_by") or not observation.get(
            "adjudicated_at"
        ):
            issues.append(
                f"{path}: {observation.get('error_id')} lacks adjudicator provenance"
            )

    requires_process_review = any(
        item.get("attribution") in {"agent", "mixed"} for item in substantive
    )
    process = document.get("process_review", {})
    if requires_process_review:
        if process.get("review_status") != "reviewed":
            issues.append(f"{path}: agent-attributed failure lacks trajectory review")
        if process.get("successful_self_correction") == "not_reviewed":
            issues.append(
                f"{path}: successful self-correction was not assessed in the trace"
            )
        if not process.get("reviewed_by") or not process.get("reviewed_at"):
            issues.append(f"{path}: trajectory review lacks reviewer provenance")
        if process.get("categories") and not process.get("evidence"):
            issues.append(
                f"{path}: process categories lack observable trace evidence"
            )
    return issues


def _status_document(
    *,
    manifest_path: Path,
    manifest: dict[str, Any],
    analyses: list[tuple[Path, dict[str, Any]]],
    task_bundle_digest: str,
    issues: list[str],
) -> dict[str, Any]:
    categories: Counter[str] = Counter()
    validity: Counter[str] = Counter()
    attribution: Counter[str] = Counter()
    process: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    for _, document in analyses:
        outcomes[document["run_outcome"]] += 1
        process.update(document["process_review"]["categories"])
        for observation in document["observations"]:
            categories[observation["category"]] += 1
            validity[observation["benchmark_validity"]] += 1
            attribution[observation["attribution"]] += 1

    full_run_ready = not issues
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "full_run_ready": full_run_ready,
        "expected_trial_count": manifest.get("expected_trial_count"),
        "reviewed_trial_count": len(analyses),
        "task_bundle_sha256": task_bundle_digest,
        "review_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "review_bundle_sha256": _review_bundle_sha256(manifest_path, analyses),
        "benchmark_refreeze": manifest.get("benchmark_refreeze"),
        "counts": {
            "run_outcome": dict(sorted(outcomes.items())),
            "output_category": dict(sorted(categories.items())),
            "benchmark_validity": dict(sorted(validity.items())),
            "attribution": dict(sorted(attribution.items())),
            "process_category": dict(sorted(process.items())),
        },
        "issues": issues,
    }


def _review_bundle_sha256(
    manifest_path: Path,
    analyses: list[tuple[Path, dict[str, Any]]],
) -> str:
    digest = hashlib.sha256()
    for path in [manifest_path, *(item[0] for item in analyses)]:
        encoded = str(path).encode("utf-8")
        data = path.read_bytes()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return document


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
