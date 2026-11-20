from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)


PACK_SCHEMA_VERSION = "libstruct.libgen_capability_pack.v1"
PACK_MANIFEST = "manifest.json"
PACK_SCHEMA = "capability_pack_manifest.schema.json"
EDITABLE_ROOTS = (
    "PLAYBOOK.md",
    "checklists",
    "schemas",
    "tools",
    "synthetic_tests",
)
IMMUTABLE_PATHS = ("adapters/codex.md",)
REQUIRED_PATHS = frozenset(
    {
        "PLAYBOOK.md",
        "checklists/evidence_ledger.md",
        "checklists/state_conservation.md",
        "checklists/transition_accounting.md",
        "checklists/final_graph_audit.md",
        "tools/_common.py",
        "tools/check_product_conservation.py",
        "tools/check_typed_edges.py",
        "tools/check_strand_pairing.py",
        "tools/check_unsupported_completion.py",
        "tools/work_record.py",
        "tools/compile_work_record.py",
        "tools/audit_predictions.py",
        "tools/control_index.json",
        "schemas/work_record.schema.json",
        "schemas/control_index.schema.json",
        "schemas/audit_report.schema.json",
        "schemas/synthetic_suite.schema.json",
        "synthetic_tests/README.md",
        "synthetic_tests/suite.json",
        "synthetic_tests/valid/t2.json",
        "synthetic_tests/valid/t3.json",
        "synthetic_tests/valid/evidence_ledger.json",
        "synthetic_tests/valid/work_record.json",
        "synthetic_tests/boundary/excluded_inventory_work_record.json",
        "synthetic_tests/invalid/malformed_work_record.json",
        "adapters/codex.md",
    }
)
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class CapabilityImprovementError(ValueError):
    """Raised when a capability artifact violates a frozen invariant."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def improvement_schema_root() -> Path:
    return repository_root() / "schemas" / "improvement"


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def with_digest(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    document = copy.deepcopy(dict(payload))
    if field in document:
        raise CapabilityImprovementError(f"payload already contains {field}")
    document[field] = canonical_digest(document)
    return document


def validate_digest(document: Mapping[str, Any], field: str) -> None:
    actual = document.get(field)
    expected = canonical_digest(
        {key: value for key, value in document.items() if key != field}
    )
    if actual != expected:
        raise CapabilityImprovementError(
            f"digest mismatch for {field}: expected {expected}, got {actual}"
        )


def safe_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CapabilityImprovementError("artifact path must be a non-empty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise CapabilityImprovementError(f"unsafe relative path: {value!r}")
    if "\x00" in value:
        raise CapabilityImprovementError("artifact path contains a NUL byte")
    return Path(*pure.parts)


def artifact_record(
    path: Path,
    *,
    relative_to: Path | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise CapabilityImprovementError(f"artifact is not a regular file: {path}")
    result: dict[str, Any] = {
        "path": (
            resolved.relative_to(relative_to.resolve()).as_posix()
            if relative_to is not None
            else resolved.as_posix()
        ),
        "sha256": sha256_file(resolved),
    }
    if role is not None:
        result["role"] = role
    return result


def build_capability_pack_manifest(pack_root: Path) -> dict[str, Any]:
    root = pack_root.expanduser().resolve()
    if not root.is_dir():
        raise CapabilityImprovementError(f"capability pack is missing: {root}")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(f"symlinks are forbidden in packs: {path}")
        if path.is_dir():
            if path.name == "__pycache__":
                raise CapabilityImprovementError(
                    f"generated cache is forbidden: {path}"
                )
            continue
        if not path.is_file():
            raise CapabilityImprovementError(f"non-regular pack entry: {path}")
        relative = path.relative_to(root).as_posix()
        if relative == PACK_MANIFEST:
            continue
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            raise CapabilityImprovementError(
                f"hidden pack entry is forbidden: {relative}"
            )
        mode = stat.S_IMODE(path.stat().st_mode)
        canonical_mode = "0555" if mode & stat.S_IXUSR else "0444"
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "mode": canonical_mode,
            }
        )
    paths = {item["path"] for item in files}
    missing = sorted(REQUIRED_PATHS - paths)
    if missing:
        raise CapabilityImprovementError(
            "capability pack is missing required files: " + ", ".join(missing)
        )
    payload: dict[str, Any] = {
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_kind": "model_neutral",
        "manifest_self_excluded": True,
        "editable_roots": list(EDITABLE_ROOTS),
        "immutable_paths": list(IMMUTABLE_PATHS),
        "files": files,
    }
    return with_digest(payload, "pack_digest")


def write_capability_pack_manifest(pack_root: Path) -> Path:
    root = pack_root.expanduser().resolve()
    manifest = build_capability_pack_manifest(root)
    path = root / PACK_MANIFEST
    write_json_atomic(path, manifest, mode=0o444 if _tree_is_frozen(root) else 0o644)
    return path


def validate_capability_pack(
    pack_root: Path,
    *,
    schema_root: Path | None = None,
) -> dict[str, Any]:
    root = pack_root.expanduser().resolve()
    manifest_path = root / PACK_MANIFEST
    try:
        manifest = load_json_object(manifest_path, label="capability-pack manifest")
        validate_document(
            manifest,
            (schema_root or improvement_schema_root()) / PACK_SCHEMA,
            label="capability-pack manifest",
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    validate_digest(manifest, "pack_digest")
    expected = build_capability_pack_manifest(root)
    if manifest != expected:
        expected_files = {item["path"]: item for item in expected["files"]}
        actual_files = {item["path"]: item for item in manifest.get("files", [])}
        changed = sorted(
            path
            for path in set(expected_files) | set(actual_files)
            if expected_files.get(path) != actual_files.get(path)
        )
        raise CapabilityImprovementError(
            "capability-pack bytes do not match manifest"
            + (": " + ", ".join(changed) if changed else "")
        )
    _validate_control_bundle(root, schema_root=schema_root)
    return manifest


def load_capability_control_bundle(
    pack_root: Path,
    *,
    schema_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the pack-owned control registry and its declarative synthetic suite."""

    root = pack_root.expanduser().resolve()
    index_path = root / "tools" / "control_index.json"
    try:
        index = load_json_object(index_path, label="capability control index")
        validate_document(
            index,
            (schema_root or improvement_schema_root())
            / "capability_control_index.schema.json",
            label="capability control index",
        )
        suite_path = root / safe_relative_path(index["synthetic_suite"])
        suite = load_json_object(suite_path, label="capability synthetic suite")
        validate_document(
            suite,
            (schema_root or improvement_schema_root())
            / "capability_synthetic_suite.schema.json",
            label="capability synthetic suite",
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    _validate_control_bundle_documents(root, index=index, suite=suite)
    return index, suite


def _validate_control_bundle(
    pack_root: Path,
    *,
    schema_root: Path | None,
) -> None:
    load_capability_control_bundle(pack_root, schema_root=schema_root)


def _validate_control_bundle_documents(
    root: Path,
    *,
    index: Mapping[str, Any],
    suite: Mapping[str, Any],
) -> None:
    referenced_paths = {
        index["work_record_schema"],
        index["compiler"]["path"],
        index["audit"]["path"],
        index["synthetic_suite"],
        *(item["implementation"] for item in index["controls"]),
    }
    for relative in sorted(referenced_paths):
        path = root / safe_relative_path(relative)
        if path.is_symlink() or not path.is_file():
            raise CapabilityImprovementError(
                f"control bundle references missing pack file: {relative}"
            )

    control_ids = [item["control_id"] for item in index["controls"]]
    if len(control_ids) != len(set(control_ids)):
        raise CapabilityImprovementError("control index contains duplicate control IDs")
    controls = {item["control_id"]: item for item in index["controls"]}
    case_ids = [item["case_id"] for item in suite["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise CapabilityImprovementError("synthetic suite contains duplicate case IDs")

    observed: dict[str, set[str]] = {control_id: set() for control_id in controls}
    for case in suite["cases"]:
        expected = case["expected"]
        contract = {
            0: "pass",
            1: "findings",
            2: "error",
        }
        if contract[expected["exit_code"]] != expected["status"]:
            raise CapabilityImprovementError(
                f"synthetic case {case['case_id']} has inconsistent exit/status"
            )
        unknown = sorted(set(case["covers"]) - set(controls))
        if unknown:
            raise CapabilityImprovementError(
                f"synthetic case {case['case_id']} covers unknown controls: "
                + ", ".join(unknown)
            )
        declared_codes = {
            code
            for control_id in case["covers"]
            for code in controls[control_id]["finding_codes"]
        }
        unexpected_codes = sorted(set(expected["finding_codes"]) - declared_codes)
        if unexpected_codes:
            raise CapabilityImprovementError(
                f"synthetic case {case['case_id']} expects unregistered finding codes: "
                + ", ".join(unexpected_codes)
            )
        for control_id in case["covers"]:
            observed[control_id].add(case["polarity"])
        for argument in case["argv"]:
            if "{pack}" in argument:
                suffix = argument.split("{pack}/", 1)
                if len(suffix) == 2:
                    safe_relative_path(suffix[1])

    for control_id, control in controls.items():
        missing = set(control["required_polarities"]) - observed[control_id]
        if missing:
            raise CapabilityImprovementError(
                f"control {control_id} lacks synthetic polarities: "
                + ", ".join(sorted(missing))
            )


def is_editable_pack_path(value: str) -> bool:
    path = safe_relative_path(value)
    pure = PurePosixPath(path.as_posix())
    if pure.as_posix() == "PLAYBOOK.md":
        return True
    return bool(
        pure.parts
        and pure.parts[0] in {"checklists", "schemas", "tools", "synthetic_tests"}
    )


def copy_capability_pack(
    source: Path,
    destination: Path,
    *,
    freeze: bool = False,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    manifest = validate_capability_pack(source)
    if destination.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite capability pack: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    copied = validate_capability_pack(destination)
    if copied["pack_digest"] != manifest["pack_digest"]:
        raise CapabilityImprovementError("copied capability pack changed digest")
    if freeze:
        freeze_tree(destination)
    return copied


def freeze_tree(root: Path) -> None:
    resolved = root.expanduser().resolve()
    for path in sorted(resolved.rglob("*"), reverse=True):
        if path.is_symlink():
            raise CapabilityImprovementError(f"cannot freeze symlink: {path}")
        if path.is_file():
            current = stat.S_IMODE(path.stat().st_mode)
            executable = bool(current & stat.S_IXUSR)
            path.chmod(0o555 if executable else 0o444)
        elif path.is_dir():
            path.chmod(0o555)
    resolved.chmod(0o555)


def thaw_tree(root: Path) -> None:
    """Make a private working copy writable; never call this on a frozen checkpoint."""

    resolved = root.expanduser().resolve()
    resolved.chmod(0o755)
    for path in sorted(resolved.rglob("*")):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            current = stat.S_IMODE(path.stat().st_mode)
            executable = bool(current & stat.S_IXUSR)
            path.chmod(0o755 if executable else 0o644)


def trees_byte_identical(left: Path, right: Path) -> bool:
    return _byte_inventory(left) == _byte_inventory(right)


def reject_private_output_in_repository(path: Path) -> None:
    resolved = path.expanduser().resolve()
    repo = repository_root().resolve()
    if resolved == repo or resolved.is_relative_to(repo):
        raise CapabilityImprovementError(
            "private improvement artifacts must not be written inside libstruct-bench"
        )


def load_and_validate(
    path: Path,
    *,
    schema_filename: str,
    digest_field: str,
    label: str,
) -> dict[str, Any]:
    try:
        document = load_json_object(path, label=label)
        validate_document(
            document,
            improvement_schema_root() / schema_filename,
            label=label,
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    validate_digest(document, digest_field)
    return document


def normalized_timestamp(value: str | None) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _byte_inventory(root: Path) -> dict[str, tuple[str, int]]:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise CapabilityImprovementError(f"directory is missing: {resolved}")
    result: dict[str, tuple[str, int]] = {}
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(f"symlink is forbidden: {path}")
        if path.is_file():
            result[path.relative_to(resolved).as_posix()] = (
                sha256_file(path),
                path.stat().st_size,
            )
    return result


def _tree_is_frozen(root: Path) -> bool:
    return not bool(stat.S_IMODE(root.stat().st_mode) & stat.S_IWUSR)


def ensure_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise CapabilityImprovementError(f"{label} must be a SHA-256 digest")
    return value
