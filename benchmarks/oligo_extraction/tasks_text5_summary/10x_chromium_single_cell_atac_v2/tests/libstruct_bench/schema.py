from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


PREDICTION_SCHEMA_VERSION = "libstruct.oligo_summary_text5.inputs.v1"
EXPECTED_INPUT_IDS = ("human", "mineru", "pymupdf", "pypdf", "docling")


class PredictionValidationError(ValueError):
    """Raised when an agent prediction does not satisfy the benchmark schema."""


@dataclass(frozen=True)
class Oligo:
    name: str
    sequence: str
    direction: str = "unknown"
    oligo_id: str | None = None


@dataclass(frozen=True)
class ParsedPrediction:
    protocol_id: str
    oligos_by_input: dict[str, list[Oligo]]


@dataclass(frozen=True)
class ParsedGroundTruth:
    protocol_id: str | None
    oligos: list[Oligo]


def parse_prediction_document(document: Any, *, expected_protocol_id: str | None = None) -> ParsedPrediction:
    doc = _require_mapping(document, "prediction")

    protocol_id = _require_string(doc.get("protocol_id"), "prediction.protocol_id")
    if expected_protocol_id is not None and protocol_id != expected_protocol_id:
        raise PredictionValidationError(
            f"prediction.protocol_id={protocol_id!r} does not match expected {expected_protocol_id!r}"
        )

    schema_version = doc.get("schema_version")
    if schema_version != PREDICTION_SCHEMA_VERSION:
        raise PredictionValidationError(
            f"prediction.schema_version must be {PREDICTION_SCHEMA_VERSION!r}"
        )

    results = doc.get("results")
    if not isinstance(results, list):
        raise PredictionValidationError("prediction.results must be a list")

    oligos_by_input: dict[str, list[Oligo]] = {input_id: [] for input_id in EXPECTED_INPUT_IDS}
    seen_input_ids: set[str] = set()
    for result_index, result in enumerate(results):
        result_map = _require_mapping(result, f"prediction.results[{result_index}]")
        input_id = _require_string(result_map.get("input_id"), f"prediction.results[{result_index}].input_id")
        if input_id not in EXPECTED_INPUT_IDS:
            raise PredictionValidationError(
                f"prediction.results[{result_index}].input_id must be one of {', '.join(EXPECTED_INPUT_IDS)}"
            )
        seen_input_ids.add(input_id)
        raw_oligos = result_map.get("oligos")
        if raw_oligos is None:
            raw_oligos = result_map.get("summaries")
        if not isinstance(raw_oligos, list):
            raise PredictionValidationError(f"prediction.results[{result_index}].oligos must be a list")
        for oligo_index, item in enumerate(raw_oligos):
            oligos_by_input[input_id].extend(
                _parse_prediction_oligo(item, f"prediction.results[{result_index}].oligos[{oligo_index}]")
            )

    return ParsedPrediction(protocol_id=protocol_id, oligos_by_input=oligos_by_input)


def parse_ground_truth_document(document: Any) -> ParsedGroundTruth:
    protocol_id = document.get("protocol_id") if isinstance(document, dict) else None
    raw_oligos = document.get("oligos") if isinstance(document, dict) else document
    if not isinstance(raw_oligos, list):
        raise ValueError("ground truth must be a list or an object with an oligos list")
    oligos: list[Oligo] = []
    for index, item in enumerate(raw_oligos):
        oligos.extend(_parse_ground_truth_oligo(item, index))
    return ParsedGroundTruth(protocol_id=protocol_id if isinstance(protocol_id, str) else None, oligos=oligos)


def _parse_prediction_oligo(item: Any, label: str) -> list[Oligo]:
    item_map = _require_mapping(item, label)
    name = _require_string(item_map.get("name"), f"{label}.name")
    sequence = item_map.get("sequence")
    direction = item_map.get("direction", "unknown")
    if direction is None:
        direction = "unknown"
    if not isinstance(direction, str):
        raise PredictionValidationError(f"{label}.direction must be a string when present")
    oligo_id = item_map.get("oligo_id")
    if oligo_id is not None and not isinstance(oligo_id, str):
        raise PredictionValidationError(f"{label}.oligo_id must be a string when present")
    if isinstance(sequence, str) and sequence.strip():
        return [
            Oligo(
                name=name.strip(),
                sequence=sequence.strip(),
                direction=direction.strip() or "unknown",
                oligo_id=oligo_id,
            )
        ]
    component_oligos = _component_oligos(
        item_map,
        parent_index=0,
        parent_name=name.strip(),
        parent_direction=direction.strip() or "unknown",
        parent_oligo_id=oligo_id,
        parent_label=label,
    )
    if component_oligos:
        return component_oligos
    raise PredictionValidationError(f"{label} must have a non-empty sequence or component sequences")


def _parse_ground_truth_oligo(item: Any, index: int) -> list[Oligo]:
    if not isinstance(item, Mapping):
        raise ValueError(f"ground truth oligos[{index}] must be an object")
    name = _first_string(item, "name", "oligo_name", "new_oligo_name", "oligo_id") or f"ground_truth_{index}"
    sequence = _first_string(item, "sequence", "oligo_sequence", "new_oligo_sequence")
    direction = _first_string(item, "direction") or "unknown"
    oligo_id = _first_string(item, "oligo_id")
    if sequence:
        return [Oligo(name=name.strip(), sequence=sequence.strip(), direction=direction.strip(), oligo_id=oligo_id)]
    component_oligos = _component_oligos(
        item,
        parent_index=index,
        parent_name=name.strip(),
        parent_direction=direction.strip(),
        parent_oligo_id=oligo_id,
        parent_label=f"ground truth oligos[{index}]",
    )
    if component_oligos:
        return component_oligos
    raise ValueError(f"ground truth oligos[{index}] is missing a sequence")


def _component_oligos(
    item: Mapping[str, Any],
    *,
    parent_index: int,
    parent_name: str,
    parent_direction: str,
    parent_oligo_id: str | None,
    parent_label: str,
) -> list[Oligo]:
    raw_components = item.get("components")
    if raw_components is None:
        raw_components = item.get("strands")
    if not isinstance(raw_components, list):
        return []

    oligos: list[Oligo] = []
    for component_index, component in enumerate(raw_components):
        if not isinstance(component, Mapping):
            continue
        sequence = _first_string(component, "sequence", "component_sequence", "oligo_sequence", "new_oligo_sequence")
        if not sequence:
            continue
        role = _first_string(component, "role", "strand", "kind")
        component_name = (
            _first_string(component, "name", "component_name", "oligo_name", "new_oligo_name")
            or role
            or f"component_{component_index + 1}"
        )
        direction = _first_string(component, "direction") or _direction_from_role(role) or parent_direction
        oligo_id = _component_oligo_id(parent_oligo_id, parent_index, component_index)
        oligos.append(
            Oligo(
                name=_component_name(parent_name, component_name),
                sequence=sequence.strip(),
                direction=direction.strip() or "unknown",
                oligo_id=oligo_id,
            )
        )
    return oligos


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PredictionValidationError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PredictionValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _component_name(parent_name: str, component_name: str) -> str:
    clean_component = component_name.strip()
    if parent_name.lower() in clean_component.lower():
        return clean_component
    return f"{parent_name} {clean_component}"


def _component_oligo_id(parent_oligo_id: str | None, parent_index: int, component_index: int) -> str:
    parent = parent_oligo_id or f"oligos[{parent_index}]"
    return f"{parent}:component[{component_index}]"


def _direction_from_role(role: str | None) -> str | None:
    if role is None:
        return None
    normalized = role.lower()
    if any(token in normalized for token in ("reverse", "bottom", "antisense")):
        return "3_to_5"
    if any(token in normalized for token in ("forward", "top", "sense")):
        return "5_to_3"
    return None


def _first_string(item: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
