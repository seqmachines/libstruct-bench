from __future__ import annotations

from typing import Any

from .grader import grade_oligo_lists
from .schema import (
    AUDITED_GROUNDTRUTH_SCHEMA_VERSION,
    AUDITED_PREDICTION_SCHEMA_VERSION,
    Oligo,
    PredictionValidationError,
    parse_ground_truth_document,
    parse_prediction_document,
)


_DIRECTIONS = {"5_to_3", "3_to_5", "unknown"}
_TOP_LEVEL_KEYS = {"schema_version", "protocol_id", "oligos", "notes"}
_OLIGO_KEYS = {
    "oligo_id",
    "name",
    "sequence",
    "kind",
    "direction",
    "components",
    "modifications",
    "notes",
}
_COMPONENT_KEYS = {"name", "role", "sequence", "direction", "modifications"}


def grade_audited_oligo_prediction(
    prediction_document: Any,
    ground_truth_document: Any,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Grade strict T2 predictions against audited oligo ground truth."""

    protocol_id, predicted = parse_audited_prediction(
        prediction_document, expected_protocol_id=expected_protocol_id
    )
    groundtruth_protocol_id, truth = parse_audited_groundtruth(
        ground_truth_document, expected_protocol_id=expected_protocol_id
    )
    metrics, audit = grade_oligo_lists(
        protocol_id=protocol_id,
        predicted_oligos=predicted,
        ground_truth_oligos=truth,
        ground_truth_protocol_id=groundtruth_protocol_id,
    )
    audit["ground_truth_protocol_id"] = groundtruth_protocol_id
    audit["excluded_ground_truth_count"] = sum(
        item.get("benchmark_status") == "excluded"
        for item in ground_truth_document["oligos"]
    )
    return metrics, audit


def parse_audited_prediction(
    document: Any, *, expected_protocol_id: str | None = None
) -> tuple[str, list[Oligo]]:
    if not isinstance(document, dict):
        raise PredictionValidationError("prediction must be a JSON object")
    extra = sorted(set(document) - _TOP_LEVEL_KEYS)
    if extra:
        raise PredictionValidationError(
            "prediction contains unsupported fields: " + ", ".join(extra)
        )
    raw = document.get("oligos")
    if not isinstance(raw, list):
        raise PredictionValidationError("prediction.oligos must be a list")
    for index, item in enumerate(raw):
        _validate_prediction_oligo(item, index)
    return parse_prediction_document(
        document,
        expected_protocol_id=expected_protocol_id,
        required_schema_version=AUDITED_PREDICTION_SCHEMA_VERSION,
    )


def parse_audited_groundtruth(
    document: Any, *, expected_protocol_id: str | None = None
) -> tuple[str, list[Oligo]]:
    if not isinstance(document, dict):
        raise ValueError("ground truth must be a JSON object")
    if document.get("schema_version") != AUDITED_GROUNDTRUTH_SCHEMA_VERSION:
        raise ValueError(
            "ground_truth.schema_version must be "
            f"{AUDITED_GROUNDTRUTH_SCHEMA_VERSION!r}"
        )
    protocol_id = document.get("protocol_id")
    if not isinstance(protocol_id, str) or not protocol_id.strip():
        raise ValueError("ground_truth.protocol_id must be non-empty")
    if expected_protocol_id is not None and protocol_id != expected_protocol_id:
        raise ValueError(
            f"ground_truth.protocol_id={protocol_id!r} does not match expected "
            f"{expected_protocol_id!r}"
        )
    raw = document.get("oligos")
    if not isinstance(raw, list):
        raise ValueError("ground_truth.oligos must be a list")
    ids: set[str] = set()
    included: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"ground_truth.oligos[{index}] must be an object")
        oligo_id = item.get("oligo_id")
        if not isinstance(oligo_id, str) or not oligo_id:
            raise ValueError(
                f"ground_truth.oligos[{index}].oligo_id must be non-empty"
            )
        if oligo_id in ids:
            raise ValueError(f"duplicate ground-truth oligo_id: {oligo_id}")
        ids.add(oligo_id)
        status = item.get("benchmark_status")
        if status not in {"included", "excluded"}:
            raise ValueError(
                f"ground_truth.oligos[{index}].benchmark_status is invalid"
            )
        if status == "included":
            included.append(item)
    _, oligos = parse_ground_truth_document(
        {"protocol_id": protocol_id, "oligos": included}
    )
    return protocol_id, oligos


def _validate_prediction_oligo(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise PredictionValidationError(
            f"prediction.oligos[{index}] must be an object"
        )
    extra = sorted(set(item) - _OLIGO_KEYS)
    if extra:
        raise PredictionValidationError(
            f"prediction.oligos[{index}] contains unsupported fields: "
            + ", ".join(extra)
        )
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PredictionValidationError(
            f"prediction.oligos[{index}].name must be non-empty"
        )
    direction = item.get("direction")
    if direction not in _DIRECTIONS:
        raise PredictionValidationError(
            f"prediction.oligos[{index}].direction must be 5_to_3, 3_to_5, or unknown"
        )
    sequence = item.get("sequence")
    components = item.get("components")
    has_sequence = isinstance(sequence, str) and bool(sequence.strip())
    has_components = isinstance(components, list) and bool(components)
    if not has_sequence and not has_components:
        raise PredictionValidationError(
            f"prediction.oligos[{index}] requires sequence or components"
        )
    if components is not None:
        if not isinstance(components, list) or not components:
            raise PredictionValidationError(
                f"prediction.oligos[{index}].components must be non-empty when present"
            )
        for component_index, component in enumerate(components):
            _validate_component(component, index, component_index)


def _validate_component(item: Any, parent_index: int, index: int) -> None:
    label = f"prediction.oligos[{parent_index}].components[{index}]"
    if not isinstance(item, dict):
        raise PredictionValidationError(f"{label} must be an object")
    extra = sorted(set(item) - _COMPONENT_KEYS)
    if extra:
        raise PredictionValidationError(
            f"{label} contains unsupported fields: " + ", ".join(extra)
        )
    for key in ("name", "role", "sequence"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PredictionValidationError(f"{label}.{key} must be non-empty")
    if item.get("direction") not in _DIRECTIONS:
        raise PredictionValidationError(f"{label}.direction is invalid")
