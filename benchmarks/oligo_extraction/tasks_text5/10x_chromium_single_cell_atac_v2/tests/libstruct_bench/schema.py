"""Schema helpers for the parser-control text5 oligo recovery benchmark."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


PREDICTION_SCHEMA_VERSION = "libstruct.parser_control_text5.v1"
GROUND_TRUTH_SCHEMA_VERSION = "libstruct.parser_control_text5.groundtruth.v1"
EXPECTED_INPUT_IDS = ("human", "mineru", "pymupdf", "pypdf", "docling")


class SchemaError(ValueError):
    """Raised when a prediction or ground-truth document is malformed."""


@dataclass(frozen=True)
class ParsedPrediction:
    protocol_id: str
    sequences_by_input: dict[str, list[str]]


@dataclass(frozen=True)
class ParsedGroundTruth:
    protocol_id: str
    sequences: list[str]
    expected_input_ids: tuple[str, ...]


def _require_mapping(document: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        raise SchemaError(f"{label} must be a JSON object")
    return document


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{label} must be a non-empty string")
    return value.strip()


def _extract_sequence_items(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaError(f"{label} must be a list")

    sequences: list[str] = []
    for idx, item in enumerate(value):
        item_label = f"{label}[{idx}]"
        if isinstance(item, str):
            sequence = item
        elif isinstance(item, Mapping):
            sequence = item.get("sequence")
        else:
            raise SchemaError(f"{item_label} must be a string or object with a sequence field")
        sequences.append(_require_string(sequence, f"{item_label}.sequence"))
    return sequences


def parse_prediction_document(document: Any) -> ParsedPrediction:
    doc = _require_mapping(document, "prediction")
    schema_version = doc.get("schema_version")
    if schema_version != PREDICTION_SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version must be {PREDICTION_SCHEMA_VERSION!r}; got {schema_version!r}"
        )

    protocol_id = _require_string(doc.get("protocol_id"), "protocol_id")
    sequences_by_input: dict[str, list[str]] = {}

    if "results" in doc:
        results = doc["results"]
        if not isinstance(results, list):
            raise SchemaError("results must be a list")
        for idx, result in enumerate(results):
            result_map = _require_mapping(result, f"results[{idx}]")
            input_id = _require_string(result_map.get("input_id"), f"results[{idx}].input_id")
            if input_id not in EXPECTED_INPUT_IDS:
                raise SchemaError(
                    f"results[{idx}].input_id must be one of {', '.join(EXPECTED_INPUT_IDS)}"
                )
            if "sequences" in result_map:
                sequences = _extract_sequence_items(result_map.get("sequences"), f"results[{idx}].sequences")
            else:
                sequences = _extract_sequence_items(result_map.get("oligos"), f"results[{idx}].oligos")
            sequences_by_input.setdefault(input_id, []).extend(sequences)
    elif "inputs" in doc:
        inputs = doc["inputs"]
        if not isinstance(inputs, Mapping):
            raise SchemaError("inputs must be an object mapping input IDs to sequence lists")
        for input_id, value in inputs.items():
            input_id = _require_string(input_id, "inputs key")
            if input_id not in EXPECTED_INPUT_IDS:
                raise SchemaError(f"inputs contains unknown input ID {input_id!r}")
            sequences_by_input[input_id] = _extract_sequence_items(value, f"inputs.{input_id}")
    else:
        raise SchemaError("prediction must contain results or inputs")

    return ParsedPrediction(protocol_id=protocol_id, sequences_by_input=sequences_by_input)


def parse_ground_truth_document(document: Any) -> ParsedGroundTruth:
    doc = _require_mapping(document, "ground truth")
    protocol_id = _require_string(doc.get("protocol_id"), "protocol_id")

    expected_input_ids_value = doc.get("expected_input_ids", EXPECTED_INPUT_IDS)
    if not isinstance(expected_input_ids_value, list | tuple):
        raise SchemaError("expected_input_ids must be a list")
    expected_input_ids = tuple(_require_string(value, "expected_input_ids entry") for value in expected_input_ids_value)
    unknown = [value for value in expected_input_ids if value not in EXPECTED_INPUT_IDS]
    if unknown:
        raise SchemaError(f"expected_input_ids contains unknown IDs: {', '.join(unknown)}")

    if "sequences" in doc:
        sequences = _extract_sequence_items(doc.get("sequences"), "sequences")
    else:
        sequences = _extract_sequence_items(doc.get("oligos"), "oligos")

    if not sequences:
        raise SchemaError("ground truth must contain at least one sequence")

    return ParsedGroundTruth(
        protocol_id=protocol_id,
        sequences=sequences,
        expected_input_ids=expected_input_ids,
    )
