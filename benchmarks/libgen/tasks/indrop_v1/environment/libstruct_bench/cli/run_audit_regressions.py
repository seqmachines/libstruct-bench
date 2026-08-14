from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.regressions import RegressionError, run_regressions


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run accepted-correction regression fixtures against pinned baselines."
    )
    parser.add_argument("--fixture", type=Path, action="append", required=True)
    parser.add_argument(
        "--baseline",
        action="append",
        default=[],
        metavar="SOURCE_ID=PATH",
        help="required only for fixtures whose baseline_state is present",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--created-at")
    parser.add_argument(
        "--fixture-schema",
        type=Path,
        default=SCHEMA_DIR / "accepted_correction_regression.schema.json",
    )
    parser.add_argument(
        "--results-schema",
        type=Path,
        default=SCHEMA_DIR / "regression_results.schema.json",
    )
    args = parser.parse_args(argv)
    try:
        result = run_regressions(
            fixture_paths=args.fixture,
            baseline_paths=_mapping(args.baseline),
            output_path=args.out,
            fixture_schema_path=args.fixture_schema,
            results_schema_path=args.results_schema,
            created_at=args.created_at,
        )
    except (RegressionError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Regression fixtures: {result['passed_count']} passed, "
        f"{result['failed_count']} failed."
    )
    print(args.out)
    return 1 if result["failed_count"] else 0


def _mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, separator, path = value.partition("=")
        if not separator or not key or not path:
            raise ValueError("--baseline values must use SOURCE_ID=PATH")
        if key in result:
            raise ValueError(f"duplicate baseline source ID: {key}")
        result[key] = Path(path)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
