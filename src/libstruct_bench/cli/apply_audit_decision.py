from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.application import ApplicationError, apply_review_decision


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministically apply a validated human audit decision.")
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--baseline", action="append", required=True, metavar="SOURCE_ID=PATH")
    parser.add_argument("--artifact-schema", action="append", metavar="SOURCE_ID=PATH")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--proposal-schema", type=Path, default=SCHEMA_DIR / "protocol_audit.v2.schema.json")
    parser.add_argument("--decision-schema", type=Path, default=SCHEMA_DIR / "review_decision.v2.schema.json")
    parser.add_argument("--application-schema", type=Path, default=SCHEMA_DIR / "application_log.v1.schema.json")
    parser.add_argument("--regression-schema", type=Path, default=SCHEMA_DIR / "accepted_correction_regression.v1.schema.json")
    args = parser.parse_args(argv)
    try:
        baselines = _mapping(args.baseline, "--baseline")
        schemas = _mapping(args.artifact_schema or [], "--artifact-schema")
        result = apply_review_decision(
            proposal_path=args.proposal,
            decision_path=args.decision,
            baseline_paths=baselines,
            artifact_schema_paths=schemas,
            output_dir=args.out,
            proposal_schema_path=args.proposal_schema,
            decision_schema_path=args.decision_schema,
            application_schema_path=args.application_schema,
            regression_schema_path=args.regression_schema,
        )
    except (ApplicationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Application log: {result.log_path}")
    print(f"Applied issues: {', '.join(result.applied_issue_ids) or 'none'}")
    return 0


def _mapping(values: list[str], flag: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, separator, path = value.partition("=")
        if not separator or not key or not path:
            raise ValueError(f"{flag} values must use SOURCE_ID=PATH")
        if key in result:
            raise ValueError(f"duplicate {flag} source ID: {key}")
        result[key] = Path(path)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
