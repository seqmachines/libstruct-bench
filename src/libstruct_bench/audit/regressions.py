from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .application import ApplicationError, apply_json_patch
from .artifacts import (
    AuditArtifactError,
    canonical_json_bytes,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)


class RegressionError(ValueError):
    """Raised when accepted-correction regression inputs are inconsistent."""


def run_regressions(
    *,
    fixture_paths: Iterable[Path],
    baseline_paths: Mapping[str, Path],
    output_path: Path,
    fixture_schema_path: Path,
    results_schema_path: Path,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Reapply accepted patches to pinned baselines and report hash regressions."""

    fixture_schema_path = _file(fixture_schema_path, "regression fixture schema")
    results_schema_path = _file(results_schema_path, "regression results schema")
    fixtures: list[dict[str, Any]] = []
    issue_ids: set[str] = set()
    for path in fixture_paths:
        document = load_json_object(_file(path, "regression fixture"), label="regression fixture")
        _validate(document, fixture_schema_path, "regression fixture")
        if document["issue_id"] in issue_ids:
            raise RegressionError(
                f"duplicate regression issue ID: {document['issue_id']}"
            )
        issue_ids.add(document["issue_id"])
        fixtures.append(document)
    required_sources = {
        item["artifact_source_id"]
        for item in fixtures
        if item["baseline_state"] == "present"
    }
    if set(baseline_paths) != required_sources:
        raise RegressionError(
            "baseline mappings must exactly cover fixture source IDs; "
            f"missing={sorted(required_sources - set(baseline_paths))}, "
            f"extra={sorted(set(baseline_paths) - required_sources)}"
        )
    baselines: dict[str, dict[str, Any]] = {}
    for source_id, path in baseline_paths.items():
        resolved = _file(path, f"baseline {source_id}")
        baselines[source_id] = load_json_object(resolved, label=f"baseline {source_id}")

    results: list[dict[str, Any]] = []
    for fixture in sorted(fixtures, key=lambda item: item["issue_id"]):
        source_id = fixture["artifact_source_id"]
        actual_candidate_sha: str | None = None
        error_message: str | None = None
        if fixture["baseline_state"] == "absent":
            baseline: dict[str, Any] = {}
        else:
            baseline_path = _file(
                baseline_paths[source_id], f"baseline {source_id}"
            )
            actual_baseline_sha = sha256_file(baseline_path)
            baseline = baselines[source_id]
            if actual_baseline_sha != fixture["baseline_sha256"]:
                error_message = (
                    f"baseline hash changed: expected {fixture['baseline_sha256']}, "
                    f"got {actual_baseline_sha}"
                )
        if error_message is None:
            try:
                candidate = apply_json_patch(baseline, fixture["patch"])
                actual_candidate_sha = hashlib.sha256(
                    canonical_json_bytes(candidate)
                ).hexdigest()
            except ApplicationError as error:
                error_message = str(error)
            if (
                error_message is None
                and actual_candidate_sha != fixture["candidate_document_sha256"]
            ):
                error_message = (
                    "candidate hash changed after deterministic patch application"
                )
        status = "failed" if error_message else "passed"
        results.append(
            {
                "issue_id": fixture["issue_id"],
                "artifact_source_id": source_id,
                "status": status,
                "expected_sha256": fixture["candidate_document_sha256"],
                "actual_sha256": actual_candidate_sha,
                "error": error_message,
            }
        )
    failed = [item["issue_id"] for item in results if item["status"] == "failed"]
    document = {
        "schema_version": "libstruct.regression_results.v1",
        "created_at": _timestamp(created_at),
        "fixture_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "new_regressions": failed,
        "results": results,
    }
    _validate(document, results_schema_path, "regression results")
    output_path = output_path.expanduser().resolve()
    _reject_output(output_path)
    if output_path.exists():
        raise RegressionError(f"regression output already exists: {output_path}")
    write_json_atomic(output_path, document)
    return document


def _validate(document: dict[str, Any], schema: Path, label: str) -> None:
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
        raise RegressionError(str(error)) from error


def _timestamp(value: str | None) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise RegressionError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RegressionError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (path == repo or path.is_relative_to(repo)):
        raise RegressionError("private regression output must not be written inside libstruct-bench")
