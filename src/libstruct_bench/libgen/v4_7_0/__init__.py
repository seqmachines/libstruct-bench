"""Explicit opt-in corrected verifier; production remains 4.6.0."""
from .scoring import grade_libgen, score_t2, score_t3
from .version import LIBGEN_BENCHMARK_VERSION

__all__ = ["grade_libgen", "score_t2", "score_t3", "LIBGEN_BENCHMARK_VERSION"]
