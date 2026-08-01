from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifacts import (
    AuditArtifactError,
    canonical_json_bytes,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    STABLE_ID_RE,
    validate_document,
    write_json_atomic,
)


class OligoCatalogError(ValueError):
    """Raised when reviewed T2 artifacts cannot form deterministic outputs."""


@dataclass(frozen=True)
class OligoCatalogResult:
    output_dir: Path
    catalog_path: Path
    tsv_path: Path
    metadata_path: Path
    protocol_count: int
    catalog_oligo_count: int
    tsv_row_count: int


TSV_FIELDS = (
    "protocol_id",
    "protocol_name",
    "oligo_id",
    "oligo_name",
    "oligo_sequence",
    "direction",
    "old_oligo_name",
    "old_oligo_sequence",
    "canonical_oligo_id",
    "family_id",
    "role",
    "kind",
    "benchmark_status",
    "exclusion_reason",
    "support_status",
    "source_names_json",
    "aliases_json",
    "modifications_json",
    "baseline_lineage_json",
    "decision_ids_json",
)


def build_oligo_outputs(
    *,
    t2_paths: Iterable[Path],
    decision_ids_by_protocol: Mapping[str, Iterable[str]],
    output_dir: Path,
    t2_schema_path: Path,
    catalog_schema_path: Path,
    metadata_schema_path: Path,
    created_at: str | None = None,
) -> OligoCatalogResult:
    """Aggregate approved T2 JSON into a canonical catalog and updated TSV."""

    t2_schema_path = _file(t2_schema_path, "T2 ground-truth schema")
    catalog_schema_path = _file(catalog_schema_path, "oligo catalog schema")
    metadata_schema_path = _file(metadata_schema_path, "build metadata schema")
    timestamp = _timestamp(created_at)
    inputs: list[tuple[Path, dict[str, Any], tuple[str, ...]]] = []
    protocol_ids: set[str] = set()
    for raw_path in t2_paths:
        path = _file(raw_path, "T2 ground-truth artifact")
        document = load_json_object(path, label="T2 ground-truth artifact")
        _validate(document, t2_schema_path, f"T2 artifact {path}")
        protocol_id = document["protocol_id"]
        if protocol_id in protocol_ids:
            raise OligoCatalogError(f"duplicate T2 protocol: {protocol_id}")
        protocol_ids.add(protocol_id)
        decisions = tuple(sorted(set(decision_ids_by_protocol.get(protocol_id, ()))))
        if not decisions or any(not STABLE_ID_RE.fullmatch(item) for item in decisions):
            raise OligoCatalogError(
                f"protocol {protocol_id} requires at least one valid human decision ID"
            )
        inputs.append((path, document, decisions))
    if not inputs:
        raise OligoCatalogError("at least one T2 artifact is required")
    extra_decisions = sorted(set(decision_ids_by_protocol) - protocol_ids)
    if extra_decisions:
        raise OligoCatalogError(
            "decision mapping contains protocols without a T2 artifact: "
            + ", ".join(extra_decisions)
        )

    catalog_groups: dict[str, dict[str, Any]] = {}
    tsv_rows: list[dict[str, str]] = []
    metadata_inputs: list[dict[str, Any]] = []
    for path, document, decisions in sorted(
        inputs, key=lambda item: item[1]["protocol_id"]
    ):
        protocol_id = document["protocol_id"]
        metadata_inputs.append(
            {
                "protocol_id": protocol_id,
                "path": path.as_posix(),
                "sha256": sha256_file(path),
                "decision_ids": list(decisions),
            }
        )
        seen_oligo_ids: set[str] = set()
        for oligo in sorted(document["oligos"], key=lambda item: item["oligo_id"]):
            oligo_id = oligo["oligo_id"]
            if oligo_id in seen_oligo_ids:
                raise OligoCatalogError(
                    f"duplicate oligo ID in {protocol_id}: {oligo_id}"
                )
            seen_oligo_ids.add(oligo_id)
            tsv_rows.append(
                _tsv_row(
                    protocol_id=protocol_id,
                    protocol_name=document["protocol_name"],
                    oligo=oligo,
                    decision_ids=decisions,
                )
            )
            if oligo["benchmark_status"] != "included":
                continue
            sequence = oligo.get("sequence")
            if not isinstance(sequence, str) or not sequence:
                raise OligoCatalogError(
                    f"included oligo lacks a catalog sequence: {protocol_id}/{oligo_id}"
                )
            canonical_id = oligo.get("canonical_oligo_id") or (
                f"{protocol_id}:{oligo_id}"
            )
            entry = {
                "canonical_oligo_id": canonical_id,
                "canonical_name": oligo["name"],
                "aliases": sorted(
                    (
                        set(oligo["source_names"])
                        | set(oligo["aliases"])
                        | {oligo["name"]}
                    )
                    - {oligo["name"]}
                ),
                "role": oligo["role"],
                "sequence": sequence,
                "direction": oligo["direction"],
                "family_id": oligo.get("family_id"),
                "protocol_refs": [f"{protocol_id}:{oligo_id}"],
                "decision_ids": list(decisions),
            }
            existing = catalog_groups.get(canonical_id)
            if existing is None:
                catalog_groups[canonical_id] = entry
            else:
                _merge_catalog_entry(existing, entry)

    catalog_entries = [catalog_groups[key] for key in sorted(catalog_groups)]
    identity = hashlib.sha256(canonical_json_bytes(catalog_entries)).hexdigest()
    catalog = {
        "schema_version": "libstruct.oligo_catalog.v1",
        "catalog_id": f"oligo-catalog:{identity[:20]}",
        "created_at": timestamp,
        "oligos": catalog_entries,
    }
    _validate(catalog, catalog_schema_path, "canonical oligo catalog")

    output_dir = output_dir.expanduser().resolve()
    _reject_output(output_dir)
    if output_dir.exists():
        raise OligoCatalogError(f"oligo output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    try:
        catalog_path = temporary_dir / "oligo_catalog.json"
        write_json_atomic(catalog_path, catalog)
        tsv_path = temporary_dir / "groundtruth_oligos.tsv"
        with tsv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=TSV_FIELDS, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(tsv_rows)
        metadata = {
            "schema_version": "libstruct.oligo_output_build.v1",
            "created_at": timestamp,
            "inputs": metadata_inputs,
            "catalog": {
                "path": catalog_path.name,
                "sha256": sha256_file(catalog_path),
            },
            "tsv": {"path": tsv_path.name, "sha256": sha256_file(tsv_path)},
        }
        _validate(metadata, metadata_schema_path, "oligo output build metadata")
        metadata_path = temporary_dir / "build-metadata.json"
        write_json_atomic(metadata_path, metadata)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return OligoCatalogResult(
        output_dir=output_dir,
        catalog_path=output_dir / "oligo_catalog.json",
        tsv_path=output_dir / "groundtruth_oligos.tsv",
        metadata_path=output_dir / "build-metadata.json",
        protocol_count=len(inputs),
        catalog_oligo_count=len(catalog_entries),
        tsv_row_count=len(tsv_rows),
    )


def _merge_catalog_entry(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    canonical_id = existing["canonical_oligo_id"]
    for key in (
        "canonical_name",
        "role",
        "sequence",
        "direction",
        "family_id",
    ):
        if existing.get(key) != incoming.get(key):
            raise OligoCatalogError(
                f"canonical oligo {canonical_id} has conflicting {key}; "
                "resolve it in human-reviewed T2 artifacts"
            )
    existing["aliases"] = sorted(set(existing["aliases"]) | set(incoming["aliases"]))
    existing["protocol_refs"] = sorted(
        set(existing["protocol_refs"]) | set(incoming["protocol_refs"])
    )
    existing["decision_ids"] = sorted(
        set(existing["decision_ids"]) | set(incoming["decision_ids"])
    )


def _tsv_row(
    *,
    protocol_id: str,
    protocol_name: str,
    oligo: dict[str, Any],
    decision_ids: tuple[str, ...],
) -> dict[str, str]:
    lineage = oligo["baseline_lineage"]
    old_name = next(
        (item["old_name"] for item in lineage if item.get("old_name")), ""
    )
    old_sequence = next(
        (item["old_sequence"] for item in lineage if item.get("old_sequence")),
        "",
    )
    return {
        "protocol_id": protocol_id,
        "protocol_name": protocol_name,
        "oligo_id": oligo["oligo_id"],
        "oligo_name": oligo["name"],
        "oligo_sequence": oligo.get("sequence") or "",
        "direction": oligo["direction"],
        "old_oligo_name": old_name,
        "old_oligo_sequence": old_sequence,
        "canonical_oligo_id": oligo.get("canonical_oligo_id") or "",
        "family_id": oligo.get("family_id") or "",
        "role": oligo["role"],
        "kind": oligo["kind"],
        "benchmark_status": oligo["benchmark_status"],
        "exclusion_reason": oligo.get("exclusion_reason") or "",
        "support_status": oligo["support_status"],
        "source_names_json": _compact_json(oligo["source_names"]),
        "aliases_json": _compact_json(oligo["aliases"]),
        "modifications_json": _compact_json(oligo["modifications"]),
        "baseline_lineage_json": _compact_json(lineage),
        "decision_ids_json": _compact_json(decision_ids),
    }


def _compact_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _timestamp(value: str | None) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise OligoCatalogError(str(error)) from error


def _validate(document: dict[str, Any], schema: Path, label: str) -> None:
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
        raise OligoCatalogError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise OligoCatalogError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (path == repo or path.is_relative_to(repo)):
        raise OligoCatalogError(
            "audited oligo outputs must not be written inside libstruct-bench"
        )
