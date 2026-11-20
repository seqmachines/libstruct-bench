"""Compatibility boundary for split/checkpoint lineage integration.

The implementation lives with the experiment and final-lock validators.  The
split transaction imports this module lazily so neither side owns the other's
orchestration logic.
"""

from .experiment import (
    build_checkpoint_reattestation,
    validate_checkpoint_reattestation,
)

__all__ = [
    "build_checkpoint_reattestation",
    "validate_checkpoint_reattestation",
]
