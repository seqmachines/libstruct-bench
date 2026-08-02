from __future__ import annotations

import hashlib
import csv
import io
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .artifacts import canonical_json_bytes, sha256_file


MATERIALIZATION_MODES = ("copy", "symlink")
PACKET_PHASES = ("evidence", "comparison")
_RENDITION_MEDIA_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class PacketError(ValueError):
    """Raised when an audit packet cannot be safely materialized."""


@dataclass(frozen=True)
class PhasePacketResult:
    output_dir: Path
    packet_path: Path
    protocol_id: str
    phase: str
    file_count: int
    rendition_count: int


@dataclass(frozen=True)
class _PlannedFile:
    source_id: str
    role: str
    source_path: Path
    packet_path: PurePosixPath
    sha256: str
    source_kind: str | None = None
    source_sha256: str | None = None
    transformation: str | None = None
    materialized_bytes: bytes | None = None


@dataclass(frozen=True)
class _PlannedRendition:
    artifact_id: str
    source_id: str
    kind: str
    source_path: Path
    packet_path: PurePosixPath
    sha256: str
    media_type: str
    page: int | None = None
    sheet: str | None = None


def build_phase_packet(
    *,
    manifest_path: Path,
    source_dataset_dir: Path,
    groundtruth_dataset_dir: Path,
    output_dir: Path,
    manifest_schema_path: Path,
    packet_schema_path: Path,
    phase: str,
    evidence_artifact_path: Path | None = None,
    run_artifact_dir: Path | None = None,
    rendition_bundle_dir: Path | None = None,
    rendition_schema_path: Path | None = None,
    mode: str = "copy",
) -> PhasePacketResult:
    """Materialize an isolated primary-evidence or comparison packet."""

    if phase not in PACKET_PHASES:
        raise PacketError(f"phase must be one of: {', '.join(PACKET_PHASES)}")
    if mode not in MATERIALIZATION_MODES:
        raise PacketError(
            f"materialization mode must be one of: {', '.join(MATERIALIZATION_MODES)}"
        )
    manifest_path = _required_file(manifest_path, "input manifest")
    source_dataset_dir = _required_directory(source_dataset_dir, "source dataset")
    groundtruth_dataset_dir = _required_directory(
        groundtruth_dataset_dir, "ground-truth dataset"
    )
    if run_artifact_dir is not None:
        run_artifact_dir = _required_directory(run_artifact_dir, "run artifacts")
    if rendition_bundle_dir is not None:
        rendition_bundle_dir = _required_directory(
            rendition_bundle_dir, "rendition bundle"
        )
    manifest_schema_path = _required_file(
        manifest_schema_path, "input manifest schema"
    )
    packet_schema_path = _required_file(packet_schema_path, "audit packet schema")
    output_dir = output_dir.expanduser().resolve()

    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PacketError(f"cannot read input manifest as JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise PacketError("input manifest must be a JSON object")
    _validate_document(manifest, manifest_schema_path, label="input manifest")
    pending = [
        source["source_id"]
        for source in manifest["sources"]
        if source["approval_status"] == "pending"
    ]
    if pending:
        raise PacketError(
            "input manifest contains pending source decisions: " + ", ".join(pending)
        )

    if phase == "comparison":
        if evidence_artifact_path is None:
            raise PacketError("comparison phase requires --evidence-artifact")
        evidence_artifact_path = _required_file(
            evidence_artifact_path, "frozen evidence artifact"
        )
    elif evidence_artifact_path is not None:
        raise PacketError("evidence phase must not receive a frozen evidence artifact")

    protocol_id = manifest["protocol_id"]
    selected_sources = [
        source
        for source in manifest["sources"]
        if source["approval_status"] == "included"
        and _source_in_phase(source, phase)
    ]
    planned_files = _plan_phase_files(
        sources=selected_sources,
        source_dataset_dir=source_dataset_dir,
        groundtruth_dataset_dir=groundtruth_dataset_dir,
        run_artifact_dir=run_artifact_dir,
    )
    if not planned_files:
        raise PacketError(f"{phase} packet has no included files")

    source_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    primary_sources_requiring_renditions = [
        source
        for source in selected_sources
        if source["role"] == "primary_evidence"
        and source.get("media_type") in _RENDITION_MEDIA_TYPES
    ]
    planned_renditions = _plan_renditions(
        rendition_bundle_dir=rendition_bundle_dir,
        rendition_schema_path=rendition_schema_path,
        expected_sources=primary_sources_requiring_renditions,
        protocol_id=protocol_id,
        manifest_sha256=source_manifest_sha,
    )
    protected_files = {
        *(planned.source_path for planned in planned_files),
        *(planned.source_path for planned in planned_renditions),
    }
    _validate_output_location(output_dir, protected_files)
    projected_manifest = {
        "protocol_id": protocol_id,
        "phase": phase,
        "source_manifest_sha256": source_manifest_sha,
        "checkpoint": manifest["checkpoint"],
        "sources": [
            {
                key: source[key]
                for key in (
                    "source_id",
                    "role",
                    "source_kind",
                    "path",
                    "sha256",
                    "media_type",
                    "title",
                    "document_version",
                    "task_relevance",
                    "row_filter",
                )
                if key in source
            }
            for source in selected_sources
        ],
    }
    projected_bytes = canonical_json_bytes(projected_manifest)
    projected_sha = hashlib.sha256(projected_bytes).hexdigest()
    evidence_sha = (
        sha256_file(evidence_artifact_path)
        if evidence_artifact_path is not None
        else None
    )
    identity = hashlib.sha256(
        f"{source_manifest_sha}:{phase}:{evidence_sha or ''}".encode("utf-8")
    ).hexdigest()
    packet_document: dict[str, Any] = {
        "packet_id": f"{protocol_id}:{phase}-packet:{identity[:16]}",
        "protocol_id": protocol_id,
        "phase": phase,
        "materialization": mode,
        "input_manifest": {
            "path": "manifest.json",
            "sha256": projected_sha,
            "source_sha256": source_manifest_sha,
        },
        "files": [
            {
                key: value
                for key, value in {
                    "source_id": planned.source_id,
                    "role": planned.role,
                    "source_kind": planned.source_kind or "unclassified",
                    "path": planned.packet_path.as_posix(),
                    "sha256": planned.sha256,
                    "source_sha256": planned.source_sha256,
                    "transformation": planned.transformation,
                }.items()
                if value is not None
            }
            for planned in planned_files
        ],
    }
    if evidence_sha is not None:
        packet_document["frozen_evidence"] = {
            "path": "frozen_evidence/evidence.json",
            "sha256": evidence_sha,
        }
    if planned_renditions:
        packet_document["renditions"] = [
            {
                key: value
                for key, value in {
                    "artifact_id": planned.artifact_id,
                    "source_id": planned.source_id,
                    "kind": planned.kind,
                    "path": planned.packet_path.as_posix(),
                    "sha256": planned.sha256,
                    "media_type": planned.media_type,
                    "page": planned.page,
                    "sheet": planned.sheet,
                }.items()
                if value is not None
            }
            for planned in planned_renditions
        ]
    _validate_document(packet_document, packet_schema_path, label="audit packet metadata")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    try:
        projected_path = temporary_dir / "manifest.json"
        projected_path.write_bytes(projected_bytes)
        projected_path.chmod(0o444)
        for planned in planned_files:
            destination = temporary_dir.joinpath(*planned.packet_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if planned.materialized_bytes is not None:
                destination.write_bytes(planned.materialized_bytes)
                destination.chmod(0o444)
            elif mode == "copy":
                shutil.copyfile(planned.source_path, destination)
                destination.chmod(0o444)
            else:
                destination.symlink_to(planned.source_path)
            if sha256_file(destination) != planned.sha256:
                raise PacketError(f"source changed while materializing {planned.source_id}")
        for planned in planned_renditions:
            destination = temporary_dir.joinpath(*planned.packet_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if mode == "copy":
                shutil.copyfile(planned.source_path, destination)
                destination.chmod(0o444)
            else:
                destination.symlink_to(planned.source_path)
            if sha256_file(destination) != planned.sha256:
                raise PacketError(
                    f"rendition changed while materializing {planned.artifact_id}"
                )
        if evidence_artifact_path is not None:
            evidence_destination = temporary_dir / "frozen_evidence" / "evidence.json"
            evidence_destination.parent.mkdir()
            shutil.copyfile(evidence_artifact_path, evidence_destination)
            evidence_destination.chmod(0o444)
            if sha256_file(evidence_destination) != evidence_sha:
                raise PacketError("frozen evidence changed while materializing packet")
        packet_path = temporary_dir / "packet.json"
        _write_json(packet_path, packet_document)
        packet_path.chmod(0o444)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return PhasePacketResult(
        output_dir=output_dir,
        packet_path=output_dir / "packet.json",
        protocol_id=protocol_id,
        phase=phase,
        file_count=len(planned_files),
        rendition_count=len(planned_renditions),
    )


def _source_in_phase(source: dict[str, Any], phase: str) -> bool:
    role = source["role"]
    if phase == "evidence":
        return role == "primary_evidence"
    return role in {
        "legacy_curated_html",
        "current_benchmark_record",
        "benchmark_run_artifact",
    }


def _plan_phase_files(
    *,
    sources: list[dict[str, Any]],
    source_dataset_dir: Path,
    groundtruth_dataset_dir: Path,
    run_artifact_dir: Path | None,
) -> list[_PlannedFile]:
    source_ids: set[str] = set()
    role_counts: dict[str, int] = {}
    planned: list[_PlannedFile] = []
    role_order = {
        "primary_evidence": 0,
        "legacy_curated_html": 1,
        "current_benchmark_record": 2,
        "benchmark_run_artifact": 3,
    }
    for source in sorted(
        sources, key=lambda item: (role_order[item["role"]], item["path"])
    ):
        source_id = source["source_id"]
        if source_id in source_ids:
            raise PacketError(f"duplicate source_id in manifest: {source_id}")
        source_ids.add(source_id)
        role = source["role"]
        if role in {"primary_evidence", "legacy_curated_html"}:
            root = source_dataset_dir
        elif role == "current_benchmark_record":
            root = groundtruth_dataset_dir
        elif role == "benchmark_run_artifact":
            if run_artifact_dir is None:
                raise PacketError(
                    f"run artifact root is required for source {source_id}"
                )
            root = run_artifact_dir
        else:
            raise PacketError(f"unsupported source role: {role}")
        actual_path = _resolve_dataset_path(root, source["dataset_reference"]["path"])
        actual_hash = sha256_file(actual_path)
        if actual_hash != source["sha256"]:
            raise PacketError(
                f"stale hash for {source_id}: expected {source['sha256']}, got {actual_hash}"
            )
        if actual_path.stat().st_size != source["size_bytes"]:
            raise PacketError(
                f"stale size for {source_id}: expected {source['size_bytes']}, "
                f"got {actual_path.stat().st_size}"
            )
        materialized_bytes: bytes | None = None
        source_sha256: str | None = None
        transformation: str | None = None
        packet_sha256 = source["sha256"]
        if "row_filter" in source:
            if source.get("media_type") != "text/tab-separated-values":
                raise PacketError(
                    f"row_filter is only supported for TSV source {source_id}"
                )
            materialized_bytes = _filter_tsv_bytes(
                actual_path, source["row_filter"], source_id
            )
            source_sha256 = source["sha256"]
            packet_sha256 = hashlib.sha256(materialized_bytes).hexdigest()
            transformation = "tsv_row_filter"
        role_counts[role] = role_counts.get(role, 0) + 1
        packet_name = f"{role_counts[role]:03d}-{actual_path.name}"
        planned.append(
            _PlannedFile(
                source_id=source_id,
                role=role,
                source_path=actual_path,
                packet_path=PurePosixPath(role, packet_name),
                sha256=packet_sha256,
                source_kind=source["source_kind"],
                source_sha256=source_sha256,
                transformation=transformation,
                materialized_bytes=materialized_bytes,
            )
        )
    return planned


def _filter_tsv_bytes(
    path: Path, row_filter: dict[str, Any], source_id: str
) -> bytes:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if reader.fieldnames is None:
                raise PacketError(f"TSV source has no header: {source_id}")
            column = row_filter["column"]
            if column not in reader.fieldnames:
                raise PacketError(
                    f"TSV source {source_id} has no filter column {column!r}"
                )
            rows = [
                (row_number, row)
                for row_number, row in enumerate(reader, start=2)
                if row.get(column) == row_filter["value"]
            ]
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise PacketError(f"cannot filter TSV source {source_id}: {error}") from error
    output = io.StringIO(newline="")
    fieldnames = ["source_row_number", *reader.fieldnames]
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        delimiter="\t",
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    for row_number, row in rows:
        writer.writerow({"source_row_number": row_number, **row})
    return output.getvalue().encode("utf-8")


def _plan_renditions(
    *,
    rendition_bundle_dir: Path | None,
    rendition_schema_path: Path | None,
    expected_sources: list[dict[str, Any]],
    protocol_id: str,
    manifest_sha256: str,
) -> list[_PlannedRendition]:
    if not expected_sources:
        if rendition_bundle_dir is not None:
            raise PacketError(
                "a rendition bundle was supplied but no selected primary source requires it"
            )
        return []
    if rendition_bundle_dir is None:
        raise PacketError(
            "selected primary documents require --rendition-bundle-dir"
        )
    if rendition_schema_path is None:
        raise PacketError(
            "--rendition-schema is required with --rendition-bundle-dir"
        )
    schema_path = _required_file(rendition_schema_path, "rendition schema")
    bundle_path = _required_file(
        rendition_bundle_dir / "rendition_bundle.json", "rendition metadata"
    )
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PacketError(f"cannot read rendition metadata: {error}") from error
    _validate_document(bundle, schema_path, label="rendition bundle")
    if bundle["protocol_id"] != protocol_id:
        raise PacketError("rendition bundle protocol does not match packet")
    if bundle["input_manifest_sha256"] != manifest_sha256:
        raise PacketError("rendition bundle references a stale input manifest")
    expected = {source["source_id"]: source["sha256"] for source in expected_sources}
    actual = {source["source_id"]: source for source in bundle["sources"]}
    if len(actual) != len(bundle["sources"]):
        raise PacketError("rendition bundle contains duplicate source IDs")
    if set(actual) != set(expected):
        raise PacketError(
            "rendition source coverage mismatch; "
            f"missing={sorted(set(expected) - set(actual))}, "
            f"extra={sorted(set(actual) - set(expected))}"
        )
    planned: list[_PlannedRendition] = []
    for source_index, source_id in enumerate(sorted(expected), start=1):
        source = actual[source_id]
        if source["source_sha256"] != expected[source_id]:
            raise PacketError(f"rendition source hash is stale: {source_id}")
        for artifact_index, artifact in enumerate(source["artifacts"], start=1):
            actual_path = _resolve_dataset_path(
                rendition_bundle_dir, artifact["path"]
            )
            if sha256_file(actual_path) != artifact["sha256"]:
                raise PacketError(
                    f"rendition artifact hash is stale: {artifact['artifact_id']}"
                )
            if actual_path.stat().st_size != artifact["size_bytes"]:
                raise PacketError(
                    f"rendition artifact size is stale: {artifact['artifact_id']}"
                )
            packet_name = f"{artifact_index:04d}-{actual_path.name}"
            planned.append(
                _PlannedRendition(
                    artifact_id=artifact["artifact_id"],
                    source_id=source_id,
                    kind=artifact["kind"],
                    source_path=actual_path,
                    packet_path=PurePosixPath(
                        "renditions",
                        f"{source_index:03d}-{_safe_packet_component(source_id)}",
                        packet_name,
                    ),
                    sha256=artifact["sha256"],
                    media_type=artifact["media_type"],
                    page=artifact.get("page"),
                    sheet=artifact.get("sheet"),
                )
            )
    return planned


def _safe_packet_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value
    )[:100] or "source"


def _resolve_dataset_path(root: Path, logical_path: str) -> Path:
    portable = PurePosixPath(logical_path)
    if portable.is_absolute() or not portable.parts or any(
        part in {".", ".."} for part in portable.parts
    ):
        raise PacketError(f"unsafe dataset path: {logical_path}")
    root = root.resolve()
    unresolved = root.joinpath(*portable.parts)
    if unresolved.is_symlink():
        raise PacketError(f"source file must not be a symlink: {logical_path}")
    try:
        actual = unresolved.resolve(strict=True)
    except FileNotFoundError as error:
        raise PacketError(f"manifest source is missing: {logical_path}") from error
    if not actual.is_relative_to(root) or not actual.is_file():
        raise PacketError(f"manifest source escapes its configured dataset: {logical_path}")
    return actual


def _validate_document(
    document: dict[str, Any],
    schema_path: Path,
    *,
    label: str,
) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PacketError(f"cannot read {label} schema: {error}") from error
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.path) or "<root>"
    raise PacketError(f"{label} schema error at {location}: {first.message}")


def _validate_output_location(output_dir: Path, protected_files: set[Path]) -> None:
    if output_dir.exists():
        raise PacketError(f"packet output already exists: {output_dir}")
    for source_file in protected_files:
        source_parent = source_file.resolve().parent
        if (
            output_dir == source_parent
            or output_dir.is_relative_to(source_parent)
            or source_parent.is_relative_to(output_dir)
        ):
            raise PacketError(
                f"packet output overlaps an input directory: {source_parent}"
            )
    repository_root = Path(__file__).resolve().parents[3]
    if (repository_root / ".git").exists() and (
        output_dir == repository_root or output_dir.is_relative_to(repository_root)
    ):
        raise PacketError(
            "audit packets belong in the private audit-data repository or a "
            "temporary directory, not in libstruct-bench"
        )


def _required_directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise PacketError(f"{label} directory does not exist: {path}")
    return resolved


def _required_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise PacketError(f"{label} file does not exist: {path}")
    return resolved


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
