from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


STABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class AuditArtifactError(ValueError):
    """Raised when an audit artifact is missing or invalid."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def load_json_object(path: Path, *, label: str = "JSON artifact") -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AuditArtifactError(f"{label} does not exist: {path}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditArtifactError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise AuditArtifactError(f"{label} must be a JSON object")
    return value


def schema_validator(schema_path: Path) -> Draft202012Validator:
    schema = load_json_object(schema_path, label="JSON Schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise AuditArtifactError(f"invalid JSON Schema {schema_path}: {error}") from error
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_document(
    document: dict[str, Any],
    schema_path: Path,
    *,
    label: str,
) -> None:
    validator = schema_validator(schema_path)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.path) or "<root>"
    raise AuditArtifactError(f"{label} schema error at {location}: {first.message}")


def write_json_atomic(path: Path, value: Any, *, mode: int | None = None) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        if mode is not None:
            path.chmod(mode)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def normalize_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuditArtifactError("timestamp must be ISO 8601") from error
    if parsed.tzinfo is None:
        raise AuditArtifactError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
