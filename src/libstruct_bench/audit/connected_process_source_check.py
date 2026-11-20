from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import (
    AuditArtifactError,
    load_json_object,
    sha256_file,
    validate_document,
)


class ConnectedProcessSourceCheckError(ValueError):
    """Raised when a direct T3 source-check artifact is stale or incomplete."""


def validate_connected_process_source_check(
    *,
    report_path: Path,
    preview_dir: Path,
    audit_root: Path,
    schema_root: Path,
) -> dict[str, Any]:
    """Validate report schema, pinned hashes, record coverage, and summary counts."""

    report_path = _file(report_path, "connected-process source check")
    preview_dir = _directory(preview_dir, "connected-process preview")
    audit_root = _directory(audit_root, "audit root")
    schema_root = _directory(schema_root, "schema root")
    report = load_json_object(report_path, label="connected-process source check")
    try:
        validate_document(
            report,
            schema_root / "audit" / "connected_process_source_check.schema.json",
            label="connected-process source check",
        )
    except AuditArtifactError as error:
        raise ConnectedProcessSourceCheckError(str(error)) from error

    manifest_path = _pinned_artifact(
        preview_dir,
        report["preview_manifest"],
        "connected-process preview manifest",
    )
    manifest = load_json_object(
        manifest_path, label="connected-process preview manifest"
    )
    if report["migration_id"] != manifest["migration_id"]:
        raise ConnectedProcessSourceCheckError(
            "source check and preview use different migration IDs"
        )

    protocol_ids = [item["protocol_id"] for item in report["protocols"]]
    if len(protocol_ids) != len(set(protocol_ids)):
        raise ConnectedProcessSourceCheckError(
            "source check contains duplicate protocol IDs"
        )

    status_counts: Counter[str] = Counter()
    total_states = 0
    total_transitions = 0
    finding_ids = {item["finding_id"] for item in report["resolved_findings"]}
    if len(finding_ids) != len(report["resolved_findings"]):
        raise ConnectedProcessSourceCheckError(
            "source check contains duplicate resolved finding IDs"
        )
    unresolved_findings = {
        item["finding_id"]
        for item in report["resolved_findings"]
        if item["disposition"] == "unresolved"
    }
    for finding in report["resolved_findings"]:
        _pinned_artifact(audit_root, finding["decision"], "source-check decision")

    for protocol in report["protocols"]:
        protocol_id = protocol["protocol_id"]
        packet_path = _file(
            audit_root
            / "packets"
            / protocol_id
            / report["migration_id"]
            / "packet.json",
            f"{protocol_id} audit packet",
        )
        if sha256_file(packet_path) != protocol["packet_sha256"]:
            raise ConnectedProcessSourceCheckError(
                f"{protocol_id} source check references a stale audit packet hash"
            )
        candidate_path = _file(
            preview_dir
            / "groundtruth"
            / protocol_id
            / "groundtruth_library_generation_workflow.json",
            f"{protocol_id} candidate T3",
        )
        if sha256_file(candidate_path) != protocol["candidate_t3_sha256"]:
            raise ConnectedProcessSourceCheckError(
                f"{protocol_id} source check references a stale candidate T3 hash"
            )
        candidate = load_json_object(
            candidate_path, label=f"{protocol_id} candidate T3"
        )
        expected = {
            (record_type, record[record_id_key])
            for workflow in candidate["workflows"]
            for record_type, collection, record_id_key in (
                ("state", workflow["states"], "state_id"),
                ("transition", workflow["transitions"], "transition_id"),
            )
            for record in collection
        }
        observed_items = [
            (record["record_type"], record["record_id"])
            for record in protocol["records"]
        ]
        if len(observed_items) != len(set(observed_items)):
            raise ConnectedProcessSourceCheckError(
                f"{protocol_id} source check contains duplicate records"
            )
        observed = set(observed_items)
        if observed != expected:
            raise ConnectedProcessSourceCheckError(
                f"{protocol_id} source-check coverage differs from candidate T3; "
                f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
            )
        expected_counts = {
            "states": sum(kind == "state" for kind, _ in expected),
            "transitions": sum(kind == "transition" for kind, _ in expected),
        }
        if protocol["counts"] != expected_counts:
            raise ConnectedProcessSourceCheckError(
                f"{protocol_id} source-check counts are incorrect"
            )
        for record in protocol["records"]:
            status_counts[record["status"]] += 1
            if record["status"] != "verified":
                finding_id = record["resolution_finding_id"]
                if finding_id not in finding_ids:
                    raise ConnectedProcessSourceCheckError(
                        f"{protocol_id} record {record['record_id']} references "
                        f"unknown resolution {finding_id}"
                    )
        total_states += expected_counts["states"]
        total_transitions += expected_counts["transitions"]

    expected_summary = {
        "protocols": len(protocol_ids),
        "states": total_states,
        "transitions": total_transitions,
        "verified": status_counts["verified"],
        "conflict": status_counts["conflict"],
        "missing": status_counts["missing"],
        "ambiguous": status_counts["ambiguous"],
        "unresolved": len(unresolved_findings),
    }
    if report["summary"] != expected_summary:
        raise ConnectedProcessSourceCheckError(
            "source-check summary differs from record-level results; "
            f"expected {expected_summary}, found {report['summary']}"
        )
    return report


def _pinned_artifact(root: Path, reference: dict[str, str], label: str) -> Path:
    relative = PurePosixPath(reference["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ConnectedProcessSourceCheckError(f"{label} path must stay below its root")
    path = _file(root.joinpath(*relative.parts), label)
    if sha256_file(path) != reference["sha256"]:
        raise ConnectedProcessSourceCheckError(f"{label} hash is stale")
    return path


def _file(path: Path, label: str) -> Path:
    value = path.expanduser().resolve()
    if not value.is_file():
        raise ConnectedProcessSourceCheckError(f"{label} does not exist: {value}")
    return value


def _directory(path: Path, label: str) -> Path:
    value = path.expanduser().resolve()
    if not value.is_dir():
        raise ConnectedProcessSourceCheckError(f"{label} does not exist: {value}")
    return value
