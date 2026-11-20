"""Expose the versioned verifier at the stable scoring import path."""

import sys

from .v4_7_0 import scoring

# Both import paths must share the same score functions and mutable sensitivity weights.
sys.modules[__name__] = scoring
