from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from libstruct_bench.audit.groundtruth import (
    GroundtruthValidationError,
    validate_cross_task_links,
    validate_task_document,
)
from libstruct_bench.libgen.prediction_validation import (
    LibgenPredictionValidationError,
    validate_prediction_links as validate_agent_prediction_links,
    validate_t2_prediction as validate_agent_t2_prediction,
    validate_t3_prediction as validate_agent_t3_prediction,
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
    """Apply exactly the prediction contract exposed to the agent."""

    try:
        validate_agent_t2_prediction(
            document,
            protocol_id=protocol_id,
            schema_root=schema_root or default_schema_root(),
        )
    except LibgenPredictionValidationError as error:
        raise LibgenValidationError(str(error)) from error


def validate_t3_prediction(
    document: Any,
    *,
    protocol_id: str,
    schema_root: Path | None = None,
) -> None:
    """Apply exactly the prediction contract exposed to the agent."""

    try:
        validate_agent_t3_prediction(
            document,
            protocol_id=protocol_id,
            schema_root=schema_root or default_schema_root(),
        )
    except LibgenPredictionValidationError as error:
        raise LibgenValidationError(str(error)) from error


def validate_prediction_links(
    t2_document: dict[str, Any],
    t3_document: dict[str, Any],
) -> None:
    """Validate links using exactly the contract exposed to the agent."""

    try:
        validate_agent_prediction_links(t2_document, t3_document)
    except LibgenPredictionValidationError as error:
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
