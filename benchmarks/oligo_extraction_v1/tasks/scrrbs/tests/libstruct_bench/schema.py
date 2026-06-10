from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PREDICTION_SCHEMA_VERSION = "libstruct.oligo_extraction.v1"


class PredictionValidationError(ValueError):
    """Raised when an agent prediction does not satisfy the benchmark schema."""


@dataclass(frozen=True)
class Oligo:
    name: str
    sequence: str
    direction: str = "unknown"
    oligo_id: str | None = None


def parse_prediction_document(document: Any, *, expected_protocol_id: str | None = None) -> tuple[str, list[Oligo]]:
    if not isinstance(document, dict):
        raise PredictionValidationError("prediction must be a JSON object")

    protocol_id = document.get("protocol_id")
    if not isinstance(protocol_id, str) or not protocol_id.strip():
        raise PredictionValidationError("prediction.protocol_id must be a non-empty string")
    if expected_protocol_id is not None and protocol_id != expected_protocol_id:
        raise PredictionValidationError(
            f"prediction.protocol_id={protocol_id!r} does not match expected {expected_protocol_id!r}"
        )

    schema_version = document.get("schema_version")
    if schema_version != PREDICTION_SCHEMA_VERSION:
        raise PredictionValidationError(
            f"prediction.schema_version must be {PREDICTION_SCHEMA_VERSION!r}"
        )

    raw_oligos = document.get("oligos")
    if not isinstance(raw_oligos, list):
        raise PredictionValidationError("prediction.oligos must be a list")

    return protocol_id, [_parse_prediction_oligo(item, index) for index, item in enumerate(raw_oligos)]


def parse_ground_truth_document(document: Any) -> tuple[str | None, list[Oligo]]:
    protocol_id = document.get("protocol_id") if isinstance(document, dict) else None
    raw_oligos = document.get("oligos") if isinstance(document, dict) else document
    if not isinstance(raw_oligos, list):
        raise ValueError("ground truth must be a list or an object with an oligos list")
    return protocol_id if isinstance(protocol_id, str) else None, [
        _parse_ground_truth_oligo(item, index) for index, item in enumerate(raw_oligos)
    ]


def _parse_prediction_oligo(item: Any, index: int) -> Oligo:
    if not isinstance(item, dict):
        raise PredictionValidationError(f"prediction.oligos[{index}] must be an object")
    name = item.get("name")
    sequence = item.get("sequence")
    if not isinstance(name, str) or not name.strip():
        raise PredictionValidationError(f"prediction.oligos[{index}].name must be a non-empty string")
    if not isinstance(sequence, str) or not sequence.strip():
        raise PredictionValidationError(f"prediction.oligos[{index}].sequence must be a non-empty string")
    direction = item.get("direction", "unknown")
    if direction is None:
        direction = "unknown"
    if not isinstance(direction, str):
        raise PredictionValidationError(f"prediction.oligos[{index}].direction must be a string when present")
    oligo_id = item.get("oligo_id")
    if oligo_id is not None and not isinstance(oligo_id, str):
        raise PredictionValidationError(f"prediction.oligos[{index}].oligo_id must be a string when present")
    return Oligo(name=name.strip(), sequence=sequence.strip(), direction=direction.strip() or "unknown", oligo_id=oligo_id)


def _parse_ground_truth_oligo(item: Any, index: int) -> Oligo:
    if not isinstance(item, dict):
        raise ValueError(f"ground truth oligos[{index}] must be an object")
    name = _first_string(item, "name", "oligo_name", "new_oligo_name", "oligo_id") or f"ground_truth_{index}"
    sequence = _first_string(item, "sequence", "oligo_sequence", "new_oligo_sequence")
    if not sequence:
        raise ValueError(f"ground truth oligos[{index}] is missing a sequence")
    direction = _first_string(item, "direction") or "unknown"
    oligo_id = _first_string(item, "oligo_id")
    return Oligo(name=name.strip(), sequence=sequence.strip(), direction=direction.strip(), oligo_id=oligo_id)


def _first_string(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
