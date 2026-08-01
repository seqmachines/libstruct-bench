from __future__ import annotations

import csv
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

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


CATALOG_SCHEMA_VERSION = "libstruct.source_catalog.v1"
MANIFEST_SCHEMA_VERSION = "libstruct.audit_input_manifest.v2"
MANIFEST_REPORT_VERSION = "libstruct.audit_manifest_report.v2"
HTML_MAP_SCHEMA_VERSION = "libstruct.legacy_html_map.v1"
IGNORED_FILENAMES = {".DS_Store"}
PRIMARY_MEDIA_TYPES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
CURRENT_RECORD_KIND_BY_FILENAME = {
    "groundtruth_final_lib_struct.json": "current_t1",
    "groundtruth_oligos.json": "current_t2",
    "groundtruth_library_generation_workflow.json": "current_t3",
}

_KIND_MAP = {
    "paper": "original_paper",
    "foundational_paper": "original_paper",
    "supplement": "supplementary_methods",
    "supplement_pdf": "supplementary_methods",
    "supplement_docx": "supplementary_methods",
    "supplement_table": "oligo_table",
    "protocol": "protocol_document",
    "protocol_article": "protocol_document",
    "author_protocol": "protocol_document",
    "vendor_protocol": "vendor_source",
    "technical_note": "vendor_source",
}


class SourceCatalogError(ValueError):
    """Raised when source discovery or approval state is inconsistent."""


@dataclass(frozen=True)
class SourceCatalogResult:
    catalog_path: Path
    protocol_count: int
    source_count: int
    pending_count: int


@dataclass(frozen=True)
class ManifestBuildResult:
    report_path: Path
    manifest_dir: Path
    protocol_count: int
    ready_count: int
    blocked_count: int


def build_source_catalog(
    *,
    protocols_dir: Path,
    html_dir: Path,
    html_asset_root: Path | None = None,
    output_path: Path,
    schema_path: Path,
    source_repository: str,
    source_revision: str,
    groundtruth_repository: str,
    groundtruth_revision: str,
    source_manifest_tsv: Path | None = None,
    html_map_path: Path | None = None,
    oligo_tsv_path: Path | None = None,
    source_protocols_prefix: str = "protocols",
    groundtruth_protocols_prefix: str = "groundtruth",
    html_prefix: str = "scg_html",
    oligo_tsv_dataset_path: str = "groundtruth/groundtruth_oligos.tsv",
    previous_catalog_path: Path | None = None,
    created_at: str | None = None,
) -> SourceCatalogResult:
    """Discover all local inputs and create a reviewable, version-pinned catalog."""

    protocols_dir = _directory(protocols_dir, "protocols")
    html_dir = _directory(html_dir, "legacy HTML")
    html_asset_root = _directory(
        html_asset_root if html_asset_root is not None else html_dir.parent,
        "HTML asset root",
    )
    schema_path = _file(schema_path, "source catalog schema")
    source_protocols_prefix = _dataset_prefix(
        source_protocols_prefix, "source protocols"
    )
    groundtruth_protocols_prefix = _dataset_prefix(
        groundtruth_protocols_prefix, "ground-truth protocols"
    )
    html_prefix = _dataset_prefix(html_prefix, "legacy HTML")
    oligo_tsv_dataset_path = _dataset_path(
        "", oligo_tsv_dataset_path, label="oligo TSV dataset path"
    )
    timestamp = _timestamp(created_at)
    if not source_repository.strip() or not _immutable_revision(source_revision):
        raise SourceCatalogError(
            "source Hugging Face repository and immutable 40-64 character commit hash are required"
        )
    if not groundtruth_repository.strip() or not _immutable_revision(
        groundtruth_revision
    ):
        raise SourceCatalogError(
            "ground-truth repository and immutable 40-64 character commit hash are required"
        )

    ledger = _load_source_ledger(source_manifest_tsv, protocols_dir)
    html_map = _load_html_map(html_map_path)
    previous = _load_previous(previous_catalog_path, schema_path)
    datasets = [
        {
            "dataset_id": "protocol_sources",
            "provider": "huggingface",
            "repository": source_repository.strip(),
            "revision": source_revision.strip(),
            "repo_type": "dataset",
        },
        {
            "dataset_id": "benchmark_baselines",
            "provider": "huggingface",
            "repository": groundtruth_repository.strip(),
            "revision": groundtruth_revision.strip(),
            "repo_type": "dataset",
        },
    ]

    protocols: list[dict[str, Any]] = []
    for protocol_dir in sorted(
        (path for path in protocols_dir.iterdir() if path.is_dir() and not path.is_symlink()),
        key=lambda path: path.name,
    ):
        protocol_id = protocol_dir.name
        if not STABLE_ID_RE.fullmatch(protocol_id):
            raise SourceCatalogError(f"invalid protocol ID: {protocol_id}")
        sources = _discover_protocol_sources(
            protocol_id=protocol_id,
            protocol_dir=protocol_dir,
            protocols_dir=protocols_dir,
            html_dir=html_dir,
            html_asset_root=html_asset_root,
            html_names=_html_names(protocol_id, protocol_dir, html_map),
            ledger=ledger,
            oligo_tsv_path=oligo_tsv_path,
            source_protocols_prefix=source_protocols_prefix,
            groundtruth_protocols_prefix=groundtruth_protocols_prefix,
            html_prefix=html_prefix,
            oligo_tsv_dataset_path=oligo_tsv_dataset_path,
            previous=previous,
        )
        protocols.append({"protocol_id": protocol_id, "sources": sources})

    identity = {
        "datasets": datasets,
        "protocols": [
            {
                "protocol_id": protocol["protocol_id"],
                "sources": [
                    {
                        "source_id": source["source_id"],
                        "role": source["role"],
                        "source_kind": source["source_kind"],
                        "path": source.get("path"),
                        "sha256": source.get("sha256"),
                        "declared_sha256": source.get("declared_sha256"),
                        "integrity_status": source.get("integrity_status"),
                        "task_relevance": source["task_relevance"],
                        "row_filter": source.get("row_filter"),
                        "approval_status": source["approval_status"],
                    }
                    for source in protocol["sources"]
                ],
            }
            for protocol in protocols
        ],
    }
    identity_hash = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    document = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "catalog_id": f"source-catalog:{identity_hash[:20]}",
        "created_at": timestamp,
        "datasets": datasets,
        "protocols": protocols,
        "notes": (
            "Discovery does not imply approval. Review every pending source and preserve "
            "excluded or unavailable entries with a reason."
        ),
    }
    _validate(document, schema_path, "source catalog")
    output_path = output_path.expanduser().resolve()
    _reject_code_repo_output(output_path)
    if output_path.exists():
        raise SourceCatalogError(f"source catalog output already exists: {output_path}")
    write_json_atomic(output_path, document)
    source_count = sum(len(protocol["sources"]) for protocol in protocols)
    pending_count = sum(
        source["approval_status"] == "pending"
        for protocol in protocols
        for source in protocol["sources"]
    )
    return SourceCatalogResult(output_path, len(protocols), source_count, pending_count)


def build_manifests_from_catalog(
    *,
    catalog_path: Path,
    output_dir: Path,
    catalog_schema_path: Path,
    manifest_schema_path: Path,
    checkpoint_id: str,
    reviewed_protocol_count: int,
    created_at: str | None = None,
    protocol_ids: Iterable[str] | None = None,
) -> ManifestBuildResult:
    """Build strict per-protocol v2 manifests from a reviewed source catalog."""

    catalog_path = _file(catalog_path, "source catalog")
    catalog_schema_path = _file(catalog_schema_path, "source catalog schema")
    manifest_schema_path = _file(manifest_schema_path, "input manifest schema")
    if reviewed_protocol_count < 0:
        raise SourceCatalogError("reviewed protocol count must be non-negative")
    if not STABLE_ID_RE.fullmatch(checkpoint_id):
        raise SourceCatalogError("checkpoint ID must be a stable identifier")
    catalog = load_json_object(catalog_path, label="source catalog")
    _validate(catalog, catalog_schema_path, "source catalog")
    catalog_sha = sha256_file(catalog_path)
    datasets = {item["dataset_id"]: item for item in catalog["datasets"]}
    protocols = {item["protocol_id"]: item for item in catalog["protocols"]}
    selected = sorted(set(protocol_ids) if protocol_ids is not None else protocols)
    unknown = sorted(set(selected) - set(protocols))
    if unknown:
        raise SourceCatalogError(f"unknown protocol IDs: {', '.join(unknown)}")

    output_dir = output_dir.expanduser().resolve()
    _reject_code_repo_output(output_dir)
    if output_dir.exists():
        raise SourceCatalogError(f"manifest output already exists: {output_dir}")
    manifest_dir = output_dir / "manifests"
    manifest_dir.mkdir(parents=True)
    timestamp = _timestamp(created_at)
    reports: list[dict[str, Any]] = []
    ready_count = 0
    for ordinal, protocol_id in enumerate(selected, start=1):
        protocol = protocols[protocol_id]
        findings: list[dict[str, str]] = []
        pending = [source["source_id"] for source in protocol["sources"] if source["approval_status"] == "pending"]
        if pending:
            findings.append({"severity": "error", "code": "pending_source_review", "message": ", ".join(pending)})
        included_roles = {
            source["role"]
            for source in protocol["sources"]
            if source["approval_status"] == "included"
        }
        for role in ("primary_evidence", "legacy_curated_html", "current_benchmark_record"):
            if role not in included_roles:
                findings.append({"severity": "error", "code": f"missing_included_{role}", "message": f"No reviewed included source has role {role}."})
        if findings:
            reports.append({"protocol_id": protocol_id, "status": "blocked", "manifest_path": None, "findings": findings})
            continue

        manifest_sources = [
            _manifest_source(source, datasets)
            for source in protocol["sources"]
        ]
        identity_hash = hashlib.sha256(
            canonical_json_bytes({"protocol_id": protocol_id, "sources": manifest_sources, "catalog": catalog_sha})
        ).hexdigest()
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "manifest_id": f"{protocol_id}:inputs:{identity_hash[:16]}",
            "protocol_id": protocol_id,
            "created_at": timestamp,
            "source_catalog_sha256": catalog_sha,
            "checkpoint": {
                "checkpoint_id": checkpoint_id,
                "protocol_ordinal": ordinal,
                "reviewed_protocol_count": reviewed_protocol_count,
            },
            "sources": manifest_sources,
        }
        _validate(manifest, manifest_schema_path, f"input manifest for {protocol_id}")
        manifest_path = manifest_dir / f"{protocol_id}.json"
        write_json_atomic(manifest_path, manifest)
        reports.append({"protocol_id": protocol_id, "status": "ready", "manifest_path": f"manifests/{protocol_id}.json", "findings": []})
        ready_count += 1

    report = {
        "schema_version": MANIFEST_REPORT_VERSION,
        "created_at": timestamp,
        "source_catalog_sha256": catalog_sha,
        "summary": {"protocol_count": len(selected), "ready_count": ready_count, "blocked_count": len(selected) - ready_count},
        "protocols": reports,
    }
    report_path = output_dir / "manifest-report.json"
    write_json_atomic(report_path, report)
    return ManifestBuildResult(report_path, manifest_dir, len(selected), ready_count, len(selected) - ready_count)


def _discover_protocol_sources(
    *,
    protocol_id: str,
    protocol_dir: Path,
    protocols_dir: Path,
    html_dir: Path,
    html_asset_root: Path,
    html_names: list[str],
    ledger: dict[str, dict[str, str]],
    oligo_tsv_path: Path | None,
    source_protocols_prefix: str,
    groundtruth_protocols_prefix: str,
    html_prefix: str,
    oligo_tsv_dataset_path: str,
    previous: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    discovered_primary_paths: set[str] = set()
    for path in sorted(protocol_dir.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.is_symlink() or path.name in IGNORED_FILENAMES:
            continue
        relative = path.relative_to(protocol_dir).as_posix()
        if relative in CURRENT_RECORD_KIND_BY_FILENAME:
            role = "current_benchmark_record"
            kind = CURRENT_RECORD_KIND_BY_FILENAME[relative]
            dataset_id = "benchmark_baselines"
            portable = _dataset_path(
                groundtruth_protocols_prefix, protocol_id, relative
            )
            media_type = "application/json"
            task = {
                "current_t1": "T1",
                "current_t2": "T2",
                "current_t3": "T3",
            }[kind]
            metadata: dict[str, Any] = {"task_relevance": [task]}
        elif path.suffix.lower() in PRIMARY_MEDIA_TYPES:
            role = "primary_evidence"
            row = ledger.get(f"{protocol_id}/{relative}", {})
            kind = _KIND_MAP.get(row.get("kind", ""), "unclassified")
            dataset_id = "protocol_sources"
            portable = _dataset_path(
                source_protocols_prefix, protocol_id, relative
            )
            media_type = PRIMARY_MEDIA_TYPES[path.suffix.lower()]
            metadata = {
                "title": row.get("title") or path.name,
                "original_uri": row.get("landing_url") or row.get("direct_url") or None,
                "notes": row.get("notes") or None,
                "declared_sha256": row.get("sha256") or None,
            }
            discovered_primary_paths.add(f"{protocol_id}/{relative}")
        else:
            continue
        discovered.append(
            _catalog_source(
                protocol_id=protocol_id,
                role=role,
                source_kind=kind,
                dataset_id=dataset_id,
                path=portable,
                local_path=path,
                media_type=media_type,
                metadata=metadata,
                previous=previous,
            )
        )

    ledger_prefix = f"{protocol_id}/"
    for expected_path, row in sorted(ledger.items()):
        if not expected_path.startswith(ledger_prefix):
            continue
        portable = PurePosixPath(expected_path)
        if (
            portable.is_absolute()
            or any(part in {".", ".."} for part in portable.parts)
            or expected_path in discovered_primary_paths
        ):
            continue
        discovered.append(
            _missing_catalog_source(
                protocol_id=protocol_id,
                role="primary_evidence",
                source_kind=_KIND_MAP.get(row.get("kind", ""), "unclassified"),
                path=_dataset_path(source_protocols_prefix, expected_path),
                previous=previous,
                notes=(
                    f"Expected by SOURCE_MANIFEST.tsv but missing from the local snapshot. "
                    f"{row.get('notes', '').strip()}"
                ).strip(),
                declared_sha256=row.get("sha256") or None,
            )
        )

    for name in html_names:
        html_path = html_dir / name
        if html_path.is_file() and not html_path.is_symlink():
            discovered.append(
                _catalog_source(
                    protocol_id=protocol_id,
                    role="legacy_curated_html",
                    source_kind="legacy_html",
                    dataset_id="protocol_sources",
                    path=_dataset_path(html_prefix, name),
                    local_path=html_path,
                    media_type="text/html",
                    metadata={"title": name},
                    previous=previous,
                )
            )
            for asset in _html_assets(html_path):
                asset_path, logical = _resolve_html_asset(
                    html_asset_root=html_asset_root,
                    html_path=html_path,
                    asset=asset,
                    html_prefix=html_prefix,
                )
                if (
                    asset_path is not None
                    and asset_path.is_file()
                    and not asset_path.is_symlink()
                ):
                    discovered.append(
                        _catalog_source(
                            protocol_id=protocol_id,
                            role="legacy_curated_html",
                            source_kind="legacy_asset",
                            dataset_id="protocol_sources",
                            path=logical,
                            local_path=asset_path,
                            media_type=_asset_media_type(asset_path),
                            metadata={"title": asset_path.name},
                            previous=previous,
                        )
                    )
                else:
                    discovered.append(
                        _missing_catalog_source(
                            protocol_id=protocol_id,
                            role="legacy_curated_html",
                            source_kind="legacy_asset",
                            path=logical,
                            previous=previous,
                            notes=(
                                f"Referenced by {name} as {asset!r} but missing, "
                                "external, or outside the approved HTML snapshot."
                            ),
                        )
                    )

    if oligo_tsv_path is not None:
        tsv = _file(oligo_tsv_path, "oligo TSV baseline")
        discovered.append(
            _catalog_source(
                protocol_id=protocol_id,
                role="current_benchmark_record",
                source_kind="oligo_tsv_baseline",
                dataset_id="benchmark_baselines",
                path=oligo_tsv_dataset_path,
                local_path=tsv,
                media_type="text/tab-separated-values",
                metadata={
                    "title": tsv.name,
                    "task_relevance": ["T2"],
                    "row_filter": {
                        "column": "protocol_id",
                        "value": protocol_id,
                        "include_source_row_number": True,
                    },
                },
                previous=previous,
            )
        )
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for source in discovered:
        key = (source["role"], source.get("path", ""))
        existing = unique.get(key)
        if existing is not None:
            if existing.get("sha256") != source.get("sha256"):
                raise SourceCatalogError(
                    f"conflicting duplicate source for {protocol_id}: {key[1]}"
                )
            old_note = existing.get("notes")
            new_note = source.get("notes")
            if new_note and new_note != old_note:
                existing["notes"] = (
                    f"{old_note} {new_note}" if old_note else new_note
                )
            continue
        unique[key] = source
    return sorted(
        unique.values(),
        key=lambda source: (
            source["role"],
            source.get("path", ""),
            source["source_id"],
        ),
    )


def _catalog_source(
    *, protocol_id: str, role: str, source_kind: str, dataset_id: str, path: str,
    local_path: Path, media_type: str, metadata: dict[str, Any],
    previous: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    digest = sha256_file(local_path)
    source_id = _source_id(role, path)
    source: dict[str, Any] = {
        "source_id": source_id,
        "role": role,
        "source_kind": source_kind,
        "approval_status": "pending",
        "task_relevance": ["T1", "T2", "T3"],
        "dataset_id": dataset_id,
        "path": path,
        "sha256": digest,
        "size_bytes": local_path.stat().st_size,
        "media_type": media_type,
    }
    source.update({key: value for key, value in metadata.items() if value is not None})
    if "declared_sha256" in source:
        source["integrity_status"] = (
            "matched" if source["declared_sha256"] == digest else "mismatch"
        )
    old = previous.get((protocol_id, role, path))
    if (
        old is not None
        and old.get("sha256") == digest
        and old.get("row_filter") == source.get("row_filter")
        and old.get("declared_sha256") == source.get("declared_sha256")
    ):
        for key in ("source_kind", "approval_status", "task_relevance", "review", "document_version", "original_uri", "notes"):
            if key in old:
                source[key] = old[key]
    return source


def _missing_catalog_source(
    *, protocol_id: str, role: str, source_kind: str, path: str,
    previous: dict[tuple[str, str, str], dict[str, Any]], notes: str,
    declared_sha256: str | None = None,
) -> dict[str, Any]:
    source = {"source_id": _source_id(role, path), "role": role, "source_kind": source_kind, "approval_status": "pending", "task_relevance": ["T1", "T2", "T3"], "path": path, "notes": notes}
    if declared_sha256:
        source["declared_sha256"] = declared_sha256
        source["integrity_status"] = "missing"
    old = previous.get((protocol_id, role, path))
    if old is not None and old.get("approval_status") in {"excluded", "unavailable"}:
        for key in ("approval_status", "task_relevance", "review", "notes"):
            if key in old:
                source[key] = old[key]
    return source


def _manifest_source(source: dict[str, Any], datasets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = {key: source[key] for key in ("source_id", "role", "source_kind", "approval_status", "task_relevance")}
    for key in ("path", "sha256", "declared_sha256", "integrity_status", "size_bytes", "media_type", "title", "document_version", "original_uri", "row_filter", "review", "notes"):
        if key in source:
            result[key] = source[key]
    if source["approval_status"] == "included":
        dataset = datasets[source["dataset_id"]]
        result["dataset_reference"] = {"provider": dataset["provider"], "repository": dataset["repository"], "revision": dataset["revision"], "path": source["path"]}
    return result


def _load_source_ledger(path: Path | None, protocols_dir: Path) -> dict[str, dict[str, str]]:
    if path is None:
        candidate = protocols_dir / "SOURCE_MANIFEST.tsv"
        if not candidate.is_file():
            return {}
        path = candidate
    path = _file(path, "source manifest TSV")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except (OSError, UnicodeDecodeError) as error:
        raise SourceCatalogError(f"cannot read source manifest TSV: {error}") from error
    return {row.get("local_file", "").strip(): row for row in rows if row.get("local_file", "").strip()}


def _load_previous(path: Path | None, schema_path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    if path is None:
        return {}
    document = load_json_object(_file(path, "previous source catalog"), label="previous source catalog")
    _validate(document, schema_path, "previous source catalog")
    return {
        (protocol["protocol_id"], source["role"], source.get("path", "")): source
        for protocol in document["protocols"]
        for source in protocol["sources"]
    }


def _html_names(protocol_id: str, protocol_dir: Path, overrides: dict[str, list[str]]) -> list[str]:
    if protocol_id in overrides:
        return overrides[protocol_id]
    names: set[str] = set()
    for filename in CURRENT_RECORD_KIND_BY_FILENAME:
        path = protocol_dir / filename
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        names.update(_collect_html_names(value))
    return sorted(name for name in names if PurePosixPath(name).name == name and name.lower().endswith(".html"))


def _collect_html_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {child.strip() for key, child in value.items() if key == "source_html_file" and isinstance(child, str) and child.strip()}
        for key, child in value.items():
            if key != "source_html_file":
                found.update(_collect_html_names(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_collect_html_names(child))
        return found
    return set()


def _html_assets(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    assets = re.findall(r"(?:src|href)=[\"']([^\"']+\.(?:svg|png|jpe?g|gif))(?:[?#][^\"']*)?[\"']", text, flags=re.IGNORECASE)
    return sorted(set(assets))


def _resolve_html_asset(
    *, html_asset_root: Path, html_path: Path, asset: str, html_prefix: str
) -> tuple[Path | None, str]:
    """Resolve only local assets contained by the approved HTML snapshot."""

    asset_path = PurePosixPath(asset)
    if asset_path.is_absolute() or "://" in asset or asset.startswith("data:"):
        digest = hashlib.sha256(asset.encode("utf-8")).hexdigest()[:12]
        return None, _dataset_path(
            html_prefix,
            "__external__",
            f"{digest}-{asset_path.name or 'asset'}",
        )
    candidate = (html_path.parent / Path(*asset_path.parts)).resolve()
    approved_root = html_asset_root.resolve()
    if not candidate.is_relative_to(approved_root):
        digest = hashlib.sha256(asset.encode("utf-8")).hexdigest()[:12]
        return None, _dataset_path(
            html_prefix,
            "__external__",
            f"{digest}-{asset_path.name or 'asset'}",
        )
    relative = candidate.relative_to(approved_root).as_posix()
    return candidate, posixpath.normpath(relative)


def _asset_media_type(path: Path) -> str:
    return {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}.get(path.suffix.lower(), "application/octet-stream")


def _load_html_map(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    document = load_json_object(_file(path, "legacy HTML map"), label="legacy HTML map")
    if document.get("schema_version") != HTML_MAP_SCHEMA_VERSION or not isinstance(document.get("protocols"), dict):
        raise SourceCatalogError("invalid legacy HTML map")
    result: dict[str, list[str]] = {}
    for protocol_id, values in document["protocols"].items():
        if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
            raise SourceCatalogError(f"invalid HTML mapping for {protocol_id}")
        if any(
            PurePosixPath(value).name != value
            or not value.lower().endswith(".html")
            for value in values
        ):
            raise SourceCatalogError(f"unsafe HTML mapping for {protocol_id}")
        result[protocol_id] = sorted(set(values))
    return result


def _source_id(role: str, path: str) -> str:
    prefix = {"primary_evidence": "primary", "legacy_curated_html": "legacy", "current_benchmark_record": "benchmark", "benchmark_run_artifact": "run"}[role]
    slug = re.sub(r"[^A-Za-z0-9._:-]+", "-", path).strip("-")[:100] or "source"
    return f"{prefix}:{slug}:{hashlib.sha256(path.encode()).hexdigest()[:10]}"


def _dataset_prefix(value: str, label: str) -> str:
    value = value.strip().strip("/")
    if not value:
        return ""
    return _dataset_path("", value, label=f"{label} dataset prefix")


def _dataset_path(prefix: str, *parts: str, label: str = "dataset path") -> str:
    candidates = ([prefix] if prefix else []) + list(parts)
    path = PurePosixPath(*candidates)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in path.as_posix()
    ):
        raise SourceCatalogError(f"unsafe {label}: {path}")
    return path.as_posix()


def _directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise SourceCatalogError(f"{label} directory does not exist: {path}")
    return resolved


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SourceCatalogError(f"{label} does not exist: {path}")
    return resolved


def _timestamp(value: str | None) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise SourceCatalogError(str(error)) from error


def _immutable_revision(value: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{40,64}", value.strip()))


def _validate(document: dict[str, Any], schema_path: Path, label: str) -> None:
    try:
        validate_document(document, schema_path, label=label)
    except AuditArtifactError as error:
        raise SourceCatalogError(str(error)) from error


def _reject_code_repo_output(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (path == repo or path.is_relative_to(repo)):
        raise SourceCatalogError("private catalog and manifest outputs must not be written inside libstruct-bench")
