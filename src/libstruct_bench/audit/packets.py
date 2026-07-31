from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .inventory import ROLE_ORDER, sha256_file


PACKET_SCHEMA_VERSION = "libstruct.audit_packet.v1"
MATERIALIZATION_MODES = ("copy", "symlink")


class PacketError(ValueError):
    """Raised when an audit packet cannot be safely materialized."""


@dataclass(frozen=True)
class PacketResult:
    output_dir: Path
    packet_path: Path
    protocol_id: str
    file_count: int


@dataclass(frozen=True)
class _PlannedFile:
    source_id: str
    role: str
    source_path: Path
    packet_path: PurePosixPath
    sha256: str


def build_packet(
    *,
    manifest_path: Path,
    protocols_dir: Path,
    html_dir: Path,
    output_dir: Path,
    manifest_schema_path: Path,
    packet_schema_path: Path,
    baseline_dir: Path | None = None,
    mode: str = "copy",
) -> PacketResult:
    """Verify one manifest and materialize only its listed source files."""

    if mode not in MATERIALIZATION_MODES:
        raise PacketError(
            f"materialization mode must be one of: {', '.join(MATERIALIZATION_MODES)}"
        )

    manifest_path = _required_file(manifest_path, "input manifest")
    protocols_dir = _required_directory(protocols_dir, "protocols")
    html_dir = _required_directory(html_dir, "legacy HTML")
    baseline_dir = _required_directory(
        baseline_dir if baseline_dir is not None else protocols_dir,
        "current benchmark records",
    )
    manifest_schema_path = _required_file(
        manifest_schema_path, "input manifest schema"
    )
    packet_schema_path = _required_file(packet_schema_path, "audit packet schema")
    output_dir = output_dir.expanduser().resolve()
    _validate_output_location(
        output_dir,
        protocols_dir=protocols_dir,
        html_dir=html_dir,
        baseline_dir=baseline_dir,
    )

    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PacketError(f"cannot read input manifest as JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise PacketError("input manifest must be a JSON object")
    _validate_document(
        manifest,
        manifest_schema_path,
        label="input manifest",
    )

    protocol_id = manifest["protocol_id"]
    planned_files = _plan_files(
        manifest=manifest,
        protocol_id=protocol_id,
        protocols_dir=protocols_dir,
        html_dir=html_dir,
        baseline_dir=baseline_dir,
    )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    packet_document = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "packet_id": f"{protocol_id}:packet:{manifest_sha256[:16]}",
        "protocol_id": protocol_id,
        "materialization": mode,
        "input_manifest": {
            "path": "manifest.json",
            "sha256": manifest_sha256,
        },
        "files": [
            {
                "source_id": planned.source_id,
                "role": planned.role,
                "path": planned.packet_path.as_posix(),
                "sha256": planned.sha256,
            }
            for planned in planned_files
        ],
    }
    _validate_document(
        packet_document,
        packet_schema_path,
        label="audit packet metadata",
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.building-",
            dir=output_dir.parent,
        )
    )
    try:
        packet_manifest_path = temporary_dir / "manifest.json"
        packet_manifest_path.write_bytes(manifest_bytes)
        packet_manifest_path.chmod(0o444)

        for planned in planned_files:
            destination = temporary_dir.joinpath(*planned.packet_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if mode == "copy":
                shutil.copyfile(planned.source_path, destination)
                destination.chmod(0o444)
            else:
                destination.symlink_to(planned.source_path)
            actual_hash = sha256_file(destination)
            if actual_hash != planned.sha256:
                raise PacketError(
                    f"source changed while materializing {planned.source_id}: "
                    f"expected {planned.sha256}, got {actual_hash}"
                )

        packet_path = temporary_dir / "packet.json"
        _write_json(packet_path, packet_document)
        packet_path.chmod(0o444)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise

    return PacketResult(
        output_dir=output_dir,
        packet_path=output_dir / "packet.json",
        protocol_id=protocol_id,
        file_count=len(planned_files),
    )


def _plan_files(
    *,
    manifest: dict[str, Any],
    protocol_id: str,
    protocols_dir: Path,
    html_dir: Path,
    baseline_dir: Path,
) -> list[_PlannedFile]:
    source_ids: set[str] = set()
    role_counts: dict[str, int] = {}
    planned_files: list[_PlannedFile] = []
    sources = sorted(
        manifest["sources"],
        key=lambda source: (ROLE_ORDER[source["role"]], source["path"]),
    )

    for source in sources:
        source_id = source["source_id"]
        if source_id in source_ids:
            raise PacketError(f"duplicate source_id in manifest: {source_id}")
        source_ids.add(source_id)

        role = source["role"]
        actual_path = _resolve_source_path(
            role=role,
            logical_path=source["path"],
            protocol_id=protocol_id,
            protocols_dir=protocols_dir,
            html_dir=html_dir,
            baseline_dir=baseline_dir,
        )
        actual_hash = sha256_file(actual_path)
        expected_hash = source["sha256"]
        if actual_hash != expected_hash:
            raise PacketError(
                f"stale hash for {source_id}: expected {expected_hash}, got {actual_hash}"
            )

        role_counts[role] = role_counts.get(role, 0) + 1
        packet_name = f"{role_counts[role]:03d}-{actual_path.name}"
        packet_path = PurePosixPath(role, packet_name)
        planned_files.append(
            _PlannedFile(
                source_id=source_id,
                role=role,
                source_path=actual_path,
                packet_path=packet_path,
                sha256=expected_hash,
            )
        )

    return planned_files


def _resolve_source_path(
    *,
    role: str,
    logical_path: str,
    protocol_id: str,
    protocols_dir: Path,
    html_dir: Path,
    baseline_dir: Path,
) -> Path:
    portable = PurePosixPath(logical_path)
    if portable.is_absolute() or ".." in portable.parts or "." in portable.parts:
        raise PacketError(f"unsafe portable source path: {logical_path}")

    if role == "primary_evidence":
        prefix = ("protocols", protocol_id)
        root = protocols_dir / protocol_id
    elif role == "legacy_curated_html":
        prefix = ("legacy", "scg_html")
        root = html_dir
    elif role == "current_benchmark_record":
        prefix = ("baselines", protocol_id)
        root = baseline_dir / protocol_id
    else:
        raise PacketError(f"unsupported source role: {role}")

    if portable.parts[: len(prefix)] != prefix or len(portable.parts) <= len(prefix):
        raise PacketError(
            f"source path does not match role {role!r}: {logical_path}"
        )
    relative_parts = portable.parts[len(prefix) :]
    root = root.resolve()
    unresolved_path = root.joinpath(*relative_parts)
    if unresolved_path.is_symlink():
        raise PacketError(f"source file must not be a symlink: {logical_path}")
    try:
        actual_path = unresolved_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise PacketError(f"manifest source is missing: {logical_path}") from error
    if not actual_path.is_relative_to(root):
        raise PacketError(f"manifest source escapes its configured root: {logical_path}")
    if not actual_path.is_file():
        raise PacketError(f"manifest source is not a file: {logical_path}")
    return actual_path


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


def _validate_output_location(
    output_dir: Path,
    *,
    protocols_dir: Path,
    html_dir: Path,
    baseline_dir: Path,
) -> None:
    if output_dir.exists():
        raise PacketError(f"packet output already exists: {output_dir}")
    for source_dir in {protocols_dir, html_dir, baseline_dir}:
        if output_dir == source_dir or output_dir.is_relative_to(source_dir):
            raise PacketError(
                f"packet output must not be inside source directory: {source_dir}"
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
