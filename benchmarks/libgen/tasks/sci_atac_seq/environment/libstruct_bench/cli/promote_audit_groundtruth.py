from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.promotion import (
    PromotionError,
    promote_reviewed_groundtruth,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promote final human-approved T1-T3 candidates."
    )
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--application-log", type=Path, required=True)
    parser.add_argument("--regression-results", type=Path)
    parser.add_argument("--ground-truth-root", type=Path, required=True)
    parser.add_argument("--log-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = promote_reviewed_groundtruth(
            proposal_path=args.proposal,
            decision_path=args.decision,
            application_log_path=args.application_log,
            regression_results_path=args.regression_results,
            groundtruth_root=args.ground_truth_root,
            promotion_log_path=args.log_out,
            proposal_schema_path=AUDIT_SCHEMAS / "protocol_audit.schema.json",
            decision_schema_path=AUDIT_SCHEMAS / "review_decision.schema.json",
            application_schema_path=AUDIT_SCHEMAS / "application_log.schema.json",
            promotion_schema_path=AUDIT_SCHEMAS / "promotion_log.schema.json",
            regression_results_schema_path=(
                AUDIT_SCHEMAS / "regression_results.schema.json"
            ),
        )
    except PromotionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Approved ground truth: {result.protocol_dir}")
    print(f"Promotion log: {result.log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
