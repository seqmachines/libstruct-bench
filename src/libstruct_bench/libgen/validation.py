from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from libstruct_bench.audit.groundtruth import (
    GroundtruthValidationError,
    validate_cross_task_links,
    validate_task_document,
)


class LibgenValidationError(ValueError):
    """Raised when a libgen prediction or private truth bundle is invalid."""


def derive_required_t2_ids(t3_document: Mapping[str, Any]) -> set[str]:
    """Derive required T2 IDs from transition use and state-segment provenance."""

    required: set[str] = set()
    for workflow in t3_document.get("workflows", []):
        for transition in workflow.get("transitions", []):
            required.update(transition.get("oligo_ids", []))
        for state in workflow.get("states", []):
            for strand in state.get("strands", []):
                for segment in strand.get("segments", []):
                    required.update(
                        item["oligo_id"]
                        for item in segment.get("oligo_derivations", [])
                    )
    return required


def default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas"


def validate_t2_prediction(
    document: Any,
    *,
    protocol_id: str,
    schema_root: Path | None = None,
) -> None:
    root = schema_root or default_schema_root()
    _validate_schema(
        document,
        root / "benchmark" / "oligo_prediction.schema.json",
        "T2 prediction",
    )
    _require_protocol(document, protocol_id, "T2 prediction")
    canonical = _canonical_t2(document)
    try:
        validate_cross_task_links({"T2": canonical})
    except GroundtruthValidationError as error:
        raise LibgenValidationError(f"T2 prediction: {error}") from error


def validate_t3_prediction(
    document: Any,
    *,
    protocol_id: str,
    schema_root: Path | None = None,
) -> None:
    root = schema_root or default_schema_root()
    _validate_schema(
        document,
        root / "benchmark" / "library_generation_workflow_prediction.schema.json",
        "T3 prediction",
    )
    _require_protocol(document, protocol_id, "T3 prediction")


def validate_prediction_links(
    t2_document: dict[str, Any],
    t3_document: dict[str, Any],
) -> None:
    """Validate T3 graph semantics and its references to predicted T2 oligos."""

    try:
        validate_cross_task_links(
            {"T2": _canonical_t2(t2_document), "T3": _canonical_t3(t3_document)}
        )
    except GroundtruthValidationError as error:
        raise LibgenValidationError(f"linked T2/T3 prediction: {error}") from error


def validate_groundtruth_bundle(
    documents: Mapping[str, dict[str, Any]],
    *,
    protocol_id: str,
    schema_root: Path | None = None,
) -> None:
    """Validate the verifier-only canonical T1/T2/T3 release as a linked set."""

    root = schema_root or default_schema_root()
    groundtruth_schema_dir = root / "groundtruth"
    missing = {"T1", "T2", "T3"} - set(documents)
    if missing:
        raise LibgenValidationError(
            "ground-truth bundle is missing " + ", ".join(sorted(missing))
        )
    try:
        for task in ("T1", "T2", "T3"):
            validate_task_document(
                task,
                documents[task],
                protocol_id=protocol_id,
                schema_dir=groundtruth_schema_dir,
            )
        validate_cross_task_links(documents)
    except GroundtruthValidationError as error:
        raise LibgenValidationError(f"private ground truth: {error}") from error


def _validate_schema(document: Any, path: Path, label: str) -> None:
    if not path.exists():
        raise LibgenValidationError(f"missing {label} schema: {path}")
    import json

    schema = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return
    rendered: list[str] = []
    for error in errors[:12]:
        location = "/" + "/".join(str(item) for item in error.absolute_path)
        rendered.append(f"{location or '/'}: {error.message}")
    suffix = f"; {len(errors) - 12} more" if len(errors) > 12 else ""
    raise LibgenValidationError(f"{label} schema errors: {'; '.join(rendered)}{suffix}")


def _require_protocol(document: Any, protocol_id: str, label: str) -> None:
    if not isinstance(document, dict) or document.get("protocol_id") != protocol_id:
        actual = document.get("protocol_id") if isinstance(document, dict) else None
        raise LibgenValidationError(
            f"{label} protocol_id {actual!r} does not match {protocol_id!r}"
        )


def _canonical_t2(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    result["protocol_name"] = result["protocol_id"]
    for oligo in result["oligos"]:
        oligo["support_status"] = "explicit"
        for component in oligo["components"]:
            component["support_status"] = "explicit"
    return result


def _canonical_t3(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    result["protocol_name"] = result["protocol_id"]
    for workflow in result["workflows"]:
        for state in workflow["states"]:
            state["support_status"] = "explicit"
            for strand in state["strands"]:
                strand["support_status"] = "explicit"
            for paired_region in state["paired_regions"]:
                paired_region["support_status"] = "explicit"
            for discontinuity in state["discontinuities"]:
                discontinuity["support_status"] = "explicit"
        for transition in workflow["transitions"]:
            transition["support_status"] = "explicit"
    return result
