from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .validation import validate_groundtruth_bundle


GROUNDTRUTH_FILENAMES = {
    "T1": "groundtruth_final_lib_struct.json",
    "T2": "groundtruth_oligos.json",
    "T3": "groundtruth_library_generation_workflow.json",
}


def inspect_staging(
    config: dict[str, Any],
    *,
    source_root: Path,
    groundtruth_root: Path,
    schema_root: Path,
) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for protocol in config.get("protocols", []):
        protocol_id = protocol.get("protocol_id", "<missing>")
        errors: list[str] = []
        source_files: list[dict[str, Any]] = []
        for source in protocol.get("sources", []):
            relative = _safe_path(source.get("path"))
            path = source_root / relative
            expected = source.get("sha256")
            if not path.is_file():
                errors.append(f"missing source: {relative.as_posix()}")
                continue
            actual = sha256_file(path)
            if actual != expected:
                errors.append(
                    f"source hash mismatch: {relative.as_posix()} expected {expected}, found {actual}"
                )
            source_files.append(
                {"path": relative.as_posix(), "sha256": actual, "size_bytes": path.stat().st_size}
            )

        truth_files: list[dict[str, Any]] = []
        documents: dict[str, dict[str, Any]] = {}
        for task, filename in GROUNDTRUTH_FILENAMES.items():
            path = groundtruth_root / protocol_id / filename
            if not path.is_file():
                errors.append(f"missing {task}: {protocol_id}/{filename}")
                continue
            try:
                documents[task] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                errors.append(f"invalid JSON {protocol_id}/{filename}: {error}")
                continue
            truth_files.append(
                {
                    "path": f"{protocol_id}/{filename}",
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        if len(documents) == 3:
            try:
                validate_groundtruth_bundle(
                    documents,
                    protocol_id=protocol_id,
                    schema_root=schema_root,
                )
            except ValueError as error:
                errors.append(str(error))
        reports.append(
            {
                "protocol_id": protocol_id,
                "ready": not errors,
                "errors": errors,
                "sources": source_files,
                "groundtruth": truth_files,
            }
        )
    return {
        "ready": bool(reports) and all(item["ready"] for item in reports),
        "protocol_count": len(reports),
        "ready_protocol_count": sum(item["ready"] for item in reports),
        "protocols": reports,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("source path must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe source path: {value!r}")
    return path
