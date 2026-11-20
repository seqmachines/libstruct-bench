from __future__ import annotations

from typing import Final

from .artifacts import CapabilityImprovementError


ACTIVE_BRANCH: Final = "cumulative"
CHECKPOINT_PREFIX: Final = "C"
BATCH_SIZE: Final = 5
BATCH_IDS: Final = ("B1", "B2", "B3", "B4", "B5")
CHECKPOINT_COUNTS: Final = (0, 5, 10, 15, 20, 25)
CHECKPOINT_LABELS: Final = tuple(
    f"{CHECKPOINT_PREFIX}{count}" for count in CHECKPOINT_COUNTS
)
LEARNED_CHECKPOINT_LABELS: Final = CHECKPOINT_LABELS[1:]
ENDPOINT_LABELS: Final = ("C25",)
REPLAY_LABELS: Final = CHECKPOINT_LABELS


def require_active_branch(branch: str) -> None:
    if branch != ACTIVE_BRANCH:
        raise CapabilityImprovementError(
            f"active capability branch must be {ACTIVE_BRANCH!r}"
        )


def checkpoint_id(protocol_count: int) -> str:
    if protocol_count not in CHECKPOINT_COUNTS:
        raise CapabilityImprovementError(
            "checkpoint protocol count must be one of "
            + ", ".join(str(item) for item in CHECKPOINT_COUNTS)
        )
    return f"{CHECKPOINT_PREFIX}{protocol_count}"


def checkpoint_before_batch(batch_id: str) -> str:
    try:
        batch_index = BATCH_IDS.index(batch_id)
    except ValueError as error:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}") from error
    return checkpoint_id(batch_index * BATCH_SIZE)


def checkpoint_after_batch(batch_id: str) -> str:
    try:
        batch_index = BATCH_IDS.index(batch_id)
    except ValueError as error:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}") from error
    return checkpoint_id((batch_index + 1) * BATCH_SIZE)


def parent_checkpoint(protocol_count: int) -> str:
    if protocol_count not in CHECKPOINT_COUNTS[1:]:
        raise CapabilityImprovementError(
            "a learned checkpoint must contain 5, 10, 15, 20, or 25 protocols"
        )
    return checkpoint_id(protocol_count - BATCH_SIZE)


def batch_for_protocol_count(protocol_count: int) -> str:
    if protocol_count not in CHECKPOINT_COUNTS[1:]:
        raise CapabilityImprovementError(
            "a learned checkpoint must contain 5, 10, 15, 20, or 25 protocols"
        )
    return f"B{protocol_count // BATCH_SIZE}"
