from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker


MANIFEST_SCHEMA_VERSION = "libstruct.audit_input_manifest.v1"
REPORT_SCHEMA_VERSION = "libstruct.audit_inventory_report.v1"
HTML_MAP_SCHEMA_VERSION = "libstruct.legacy_html_map.v1"

BENCHMARK_FILENAMES = (
    "groundtruth_final_lib_struct.json",
    "groundtruth_oligos.json",
)
IGNORED_FILENAMES = {".DS_Store"}
PRIMARY_MEDIA_TYPES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ROLE_ORDER = {
    "primary_evidence": 0,
    "legacy_curated_html": 1,
    "current_benchmark_record": 2,
}
STABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class InventoryError(ValueError):
    """Raised for an invalid inventory configuration or output target."""


@dataclass(frozen=True)
class DatasetReference:
    repository: str
    revision: str

    def __post_init__(self) -> None:
        if not self.repository.strip():
            raise InventoryError("dataset repository must not be empty")
        if not self.revision.strip():
            raise InventoryError("dataset revision must not be empty")

    def for_path(self, path: str) -> dict[str, str]:
        return {
            "repository": self.repository,
            "revision": self.revision,
            "path": path,
        }


@dataclass(frozen=True)
class InventoryResult:
    report_path: Path
    manifest_dir: Path
    protocol_count: int
    ready_count: int
    blocked_count: int


def build_inventory(
    *,
    protocols_dir: Path,
    html_dir: Path,
    output_dir: Path,
    schema_path: Path,
    html_map_path: Path | None = None,
    created_at: str | None = None,
    protocol_ids: Iterable[str] | None = None,
    protocol_dataset: DatasetReference | None = None,
    groundtruth_dataset: DatasetReference | None = None,
    html_dataset: DatasetReference | None = None,
    force: bool = False,
) -> InventoryResult:
    """Build content-addressed input manifests without copying source data."""

    protocols_dir = _required_directory(protocols_dir, "protocols")
    html_dir = _required_directory(html_dir, "legacy HTML")
    schema_path = _required_file(schema_path, "input manifest schema")
    output_dir = output_dir.expanduser().resolve()
    _reject_output_inside_source(output_dir, protocols_dir, html_dir)

    timestamp = _normalize_timestamp(created_at)
    validator = _manifest_validator(schema_path)
    html_overrides = _load_html_map(html_map_path)
    html_map_sha256 = (
        sha256_file(html_map_path.expanduser().resolve())
        if html_map_path is not None
        else None
    )

    all_protocol_dirs = {
        path.name: path
        for path in protocols_dir.iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    selected_ids = _select_protocol_ids(all_protocol_dirs, protocol_ids)
    unknown_overrides = sorted(set(html_overrides) - set(all_protocol_dirs))
    if unknown_overrides:
        joined = ", ".join(unknown_overrides)
        raise InventoryError(f"HTML map contains unknown protocol IDs: {joined}")

    available_html = {
        path.name: path
        for path in html_dir.iterdir()
        if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".html"
    }
    unsupported_html_entries = sorted(
        path.name
        for path in html_dir.iterdir()
        if path.name not in IGNORED_FILENAMES
        and (
            path.is_symlink()
            or (path.is_file() and path.suffix.lower() != ".html")
        )
    )

    protocol_reports: list[dict[str, Any]] = []
    manifests: dict[str, dict[str, Any]] = {}
    mapped_html: set[str] = set()
    extension_counts: dict[str, int] = {}

    for protocol_id in selected_ids:
        protocol_report, manifest = _inventory_protocol(
            protocol_id=protocol_id,
            protocol_dir=all_protocol_dirs[protocol_id],
            available_html=available_html,
            html_overrides=html_overrides,
            timestamp=timestamp,
            protocol_dataset=protocol_dataset,
            groundtruth_dataset=groundtruth_dataset,
            html_dataset=html_dataset,
            mapping_is_override=protocol_id in html_overrides,
            mapping_override_sha256=(
                html_map_sha256 if protocol_id in html_overrides else None
            ),
        )
        for extension, count in protocol_report.pop("_extension_counts").items():
            extension_counts[extension] = extension_counts.get(extension, 0) + count
        mapped_html.update(protocol_report["legacy_html_files"])

        if manifest is not None:
            errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
            if errors:
                for error in errors:
                    location = "/".join(str(part) for part in error.path) or "<root>"
                    _add_finding(
                        protocol_report,
                        "error",
                        "manifest_schema_error",
                        f"{location}: {error.message}",
                    )
                protocol_report["status"] = "blocked"
                protocol_report["manifest_path"] = None
                manifest = None

        if manifest is not None:
            protocol_report["manifest_path"] = f"manifests/{protocol_id}.json"
            manifests[protocol_id] = manifest

        protocol_reports.append(protocol_report)

    ready_count = sum(report["status"] == "ready" for report in protocol_reports)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": timestamp,
        "scope": {
            "protocol_ids": selected_ids,
        },
        "configuration": {
            "html_map_sha256": html_map_sha256,
        },
        "summary": {
            "protocol_count": len(protocol_reports),
            "ready_count": ready_count,
            "blocked_count": len(protocol_reports) - ready_count,
            "primary_files_by_extension": {
                key: extension_counts[key] for key in sorted(extension_counts)
            },
            "available_html_count": len(available_html),
            "mapped_html_count": len(mapped_html),
        },
        "protocols": protocol_reports,
        "unmapped_html_files": sorted(set(available_html) - mapped_html),
        "unsupported_html_entries": unsupported_html_entries,
    }

    _prepare_output(output_dir, force=force)
    manifest_dir = output_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for protocol_id, manifest in manifests.items():
        _write_json(manifest_dir / f"{protocol_id}.json", manifest)
    report_path = output_dir / "inventory.json"
    _write_json(report_path, report)

    return InventoryResult(
        report_path=report_path,
        manifest_dir=manifest_dir,
        protocol_count=len(protocol_reports),
        ready_count=ready_count,
        blocked_count=len(protocol_reports) - ready_count,
    )


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_protocol(
    *,
    protocol_id: str,
    protocol_dir: Path,
    available_html: dict[str, Path],
    html_overrides: dict[str, list[str]],
    timestamp: str,
    protocol_dataset: DatasetReference | None,
    groundtruth_dataset: DatasetReference | None,
    html_dataset: DatasetReference | None,
    mapping_is_override: bool,
    mapping_override_sha256: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    report: dict[str, Any] = {
        "protocol_id": protocol_id,
        "status": "ready",
        "manifest_path": None,
        "primary_file_count": 0,
        "benchmark_record_count": 0,
        "legacy_html_files": [],
        "legacy_html_mapping_source": (
            "reviewed_override" if mapping_is_override else "benchmark_metadata"
        ),
        "findings": [],
        "_extension_counts": {},
    }

    if not STABLE_ID_RE.fullmatch(protocol_id):
        _add_finding(
            report,
            "error",
            "invalid_protocol_id",
            "Protocol directory name is not a valid stable ID.",
        )

    primary_files: list[Path] = []
    unsupported_files: list[str] = []
    for path in sorted(protocol_dir.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_dir():
            continue
        relative = path.relative_to(protocol_dir)
        if path.is_symlink():
            unsupported_files.append(f"{relative.as_posix()} (symlink)")
            continue
        if path.name in IGNORED_FILENAMES:
            continue
        if relative.parent == Path(".") and path.name in BENCHMARK_FILENAMES:
            continue
        extension = path.suffix.lower()
        if extension in PRIMARY_MEDIA_TYPES:
            primary_files.append(path)
            report["_extension_counts"][extension] = (
                report["_extension_counts"].get(extension, 0) + 1
            )
        else:
            unsupported_files.append(relative.as_posix())

    report["primary_file_count"] = len(primary_files)
    if not primary_files:
        _add_finding(
            report,
            "error",
            "missing_primary_evidence",
            "No supported primary protocol files were found.",
        )
    if unsupported_files:
        _add_finding(
            report,
            "error",
            "unsupported_protocol_files",
            "Unsupported files require explicit classification: "
            + ", ".join(unsupported_files),
        )

    benchmark_paths = [protocol_dir / name for name in BENCHMARK_FILENAMES]
    benchmark_documents: list[tuple[Path, Any]] = []
    for path in benchmark_paths:
        if not path.is_file() or path.is_symlink():
            _add_finding(
                report,
                "error",
                "missing_benchmark_record",
                f"Missing required current benchmark record: {path.name}",
            )
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            _add_finding(
                report,
                "error",
                "invalid_benchmark_record",
                f"Cannot read {path.name} as JSON: {error}",
            )
            continue
        benchmark_documents.append((path, document))
    report["benchmark_record_count"] = len(benchmark_documents)

    if protocol_id in html_overrides:
        html_names = html_overrides[protocol_id]
    else:
        html_names = sorted(
            {
                value
                for _, document in benchmark_documents
                for value in _collect_source_html_files(document)
            }
        )

    invalid_html_names = [name for name in html_names if Path(name).suffix.lower() != ".html"]
    if invalid_html_names:
        _add_finding(
            report,
            "error",
            "invalid_legacy_html_reference",
            "Legacy source references must name HTML files: "
            + ", ".join(invalid_html_names),
        )
        html_names = [name for name in html_names if name not in invalid_html_names]
    if not html_names:
        _add_finding(
            report,
            "error",
            "missing_legacy_html_mapping",
            "No valid legacy HTML mapping was found; add a reviewed --html-map entry.",
        )

    resolved_html: list[Path] = []
    for name in html_names:
        if PurePosixPath(name).name != name:
            _add_finding(
                report,
                "error",
                "invalid_legacy_html_path",
                f"Legacy HTML mapping must be a filename, not a path: {name}",
            )
            continue
        path = available_html.get(name)
        if path is None:
            _add_finding(
                report,
                "error",
                "missing_legacy_html_file",
                f"Mapped legacy HTML file does not exist: {name}",
            )
            continue
        resolved_html.append(path)
    report["legacy_html_files"] = [path.name for path in resolved_html]

    if any(finding["severity"] == "error" for finding in report["findings"]):
        report["status"] = "blocked"
        return report, None

    sources: list[dict[str, Any]] = []
    for path in primary_files:
        relative = path.relative_to(protocol_dir).as_posix()
        sources.append(
            _source(
                path=path,
                source_id=_source_id("primary", relative),
                role="primary_evidence",
                portable_path=f"protocols/{protocol_id}/{relative}",
                media_type=PRIMARY_MEDIA_TYPES[path.suffix.lower()],
                title=path.name,
                dataset_reference=(
                    protocol_dataset.for_path(f"{protocol_id}/{relative}")
                    if protocol_dataset
                    else None
                ),
            )
        )
    for path in resolved_html:
        sources.append(
            _source(
                path=path,
                source_id=_source_id("legacy", path.name),
                role="legacy_curated_html",
                portable_path=f"legacy/scg_html/{path.name}",
                media_type="text/html",
                title=path.name,
                dataset_reference=(
                    html_dataset.for_path(path.name) if html_dataset else None
                ),
            )
        )
    for path, _ in benchmark_documents:
        sources.append(
            _source(
                path=path,
                source_id=_source_id("benchmark", path.name),
                role="current_benchmark_record",
                portable_path=f"baselines/{protocol_id}/{path.name}",
                media_type="application/json",
                title=path.name,
                dataset_reference=(
                    groundtruth_dataset.for_path(f"{protocol_id}/{path.name}")
                    if groundtruth_dataset
                    else None
                ),
            )
        )
    sources.sort(key=lambda source: (ROLE_ORDER[source["role"]], source["path"]))

    identity_payload = {
        "protocol_id": protocol_id,
        "sources": [
            {
                "source_id": source["source_id"],
                "role": source["role"],
                "path": source["path"],
                "sha256": source["sha256"],
            }
            for source in sources
        ],
    }
    if mapping_override_sha256 is not None:
        identity_payload["mapping_override_sha256"] = mapping_override_sha256
    identity_hash = hashlib.sha256(_canonical_json(identity_payload).encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_id": f"{protocol_id}:inputs:{identity_hash[:16]}",
        "protocol_id": protocol_id,
        "created_at": timestamp,
        "sources": sources,
    }
    if mapping_is_override:
        manifest["notes"] = (
            "Legacy curated HTML mapping supplied by reviewed inventory override "
            f"SHA-256 {mapping_override_sha256}."
        )
    return report, manifest


def _source(
    *,
    path: Path,
    source_id: str,
    role: str,
    portable_path: str,
    media_type: str,
    title: str,
    dataset_reference: dict[str, str] | None,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "source_id": source_id,
        "role": role,
        "path": portable_path,
        "sha256": sha256_file(path),
        "media_type": media_type,
        "title": title,
    }
    if dataset_reference is not None:
        source["dataset_reference"] = dataset_reference
    return source


def _source_id(prefix: str, relative_path: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._:-]+", "-", relative_path).strip("-")
    slug = slug[:100] or "source"
    path_hash = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}:{slug}:{path_hash}"


def _collect_source_html_files(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_html_file" and isinstance(child, str) and child.strip():
                found.add(child.strip())
            else:
                found.update(_collect_source_html_files(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_source_html_files(child))
    return found


def _load_html_map(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    path = _required_file(path, "legacy HTML map")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InventoryError(f"cannot read legacy HTML map: {error}") from error
    if not isinstance(document, dict):
        raise InventoryError("legacy HTML map must be a JSON object")
    if document.get("schema_version") != HTML_MAP_SCHEMA_VERSION:
        raise InventoryError(
            f"legacy HTML map schema_version must be {HTML_MAP_SCHEMA_VERSION!r}"
        )
    protocols = document.get("protocols")
    if not isinstance(protocols, dict):
        raise InventoryError("legacy HTML map protocols must be an object")

    result: dict[str, list[str]] = {}
    for protocol_id, names in protocols.items():
        if not isinstance(protocol_id, str) or not protocol_id:
            raise InventoryError("legacy HTML map protocol IDs must be non-empty strings")
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not name.strip() for name in names
        ):
            raise InventoryError(
                f"legacy HTML map entry {protocol_id!r} must be a list of filenames"
            )
        normalized = [name.strip() for name in names]
        if len(normalized) != len(set(normalized)):
            raise InventoryError(
                f"legacy HTML map entry {protocol_id!r} contains duplicate filenames"
            )
        result[protocol_id] = sorted(normalized)
    return result


def _manifest_validator(schema_path: Path) -> Draft202012Validator:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InventoryError(f"cannot read input manifest schema: {error}") from error
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _select_protocol_ids(
    all_protocol_dirs: dict[str, Path],
    protocol_ids: Iterable[str] | None,
) -> list[str]:
    if protocol_ids is None:
        return sorted(all_protocol_dirs)
    selected = sorted(set(protocol_ids))
    unknown = sorted(set(selected) - set(all_protocol_dirs))
    if unknown:
        raise InventoryError(f"unknown protocol IDs: {', '.join(unknown)}")
    if not selected:
        raise InventoryError("at least one --protocol-id is required when filtering")
    return selected


def _normalize_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise InventoryError("--created-at must be an ISO 8601 date-time") from error
    if parsed.tzinfo is None:
        raise InventoryError("--created-at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _required_directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise InventoryError(f"{label} directory does not exist: {path}")
    return resolved


def _required_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise InventoryError(f"{label} file does not exist: {path}")
    return resolved


def _reject_output_inside_source(output_dir: Path, *source_dirs: Path) -> None:
    for source_dir in source_dirs:
        if output_dir == source_dir or output_dir.is_relative_to(source_dir):
            raise InventoryError(
                f"output directory must not be inside source directory: {source_dir}"
            )
    repository_root = Path(__file__).resolve().parents[3]
    if (repository_root / ".git").exists() and (
        output_dir == repository_root or output_dir.is_relative_to(repository_root)
    ):
        raise InventoryError(
            "inventory output belongs in the private audit-data repository or a "
            "temporary directory, not in libstruct-bench"
        )


def _prepare_output(output_dir: Path, *, force: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise InventoryError(f"output path is not a directory: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not force:
        raise InventoryError(
            f"output directory is not empty: {output_dir}; pass --force to replace generated JSON"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    if force:
        manifest_dir = output_dir / "manifests"
        if manifest_dir.is_dir():
            for path in manifest_dir.glob("*.json"):
                path.unlink()


def _add_finding(
    report: dict[str, Any],
    severity: str,
    code: str,
    message: str,
) -> None:
    report["findings"].append(
        {
            "severity": severity,
            "code": code,
            "message": message,
        }
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
