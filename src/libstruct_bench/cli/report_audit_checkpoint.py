from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.reporting import ReportingError, build_checkpoint_report


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build reproducible field-level audit checkpoint metrics.")
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--reviewed-protocol-count", type=int, required=True)
    parser.add_argument("--proposal", type=Path, action="append", default=[])
    parser.add_argument("--decision", type=Path, action="append", default=[])
    parser.add_argument("--application-log", type=Path, action="append", default=[])
    parser.add_argument("--regression-results", type=Path)
    parser.add_argument("--previous-checkpoint", type=Path)
    parser.add_argument("--created-at")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--proposal-schema", type=Path, default=SCHEMA_DIR / "protocol_audit.v2.schema.json")
    parser.add_argument("--decision-schema", type=Path, default=SCHEMA_DIR / "review_decision.v2.schema.json")
    parser.add_argument("--report-schema", type=Path, default=SCHEMA_DIR / "checkpoint_report.v1.schema.json")
    parser.add_argument("--application-schema", type=Path, default=SCHEMA_DIR / "application_log.v1.schema.json")
    parser.add_argument("--regression-results-schema", type=Path, default=SCHEMA_DIR / "regression_results.v1.schema.json")
    args = parser.parse_args(argv)
    try:
        report = build_checkpoint_report(
            checkpoint_id=args.checkpoint_id,
            reviewed_protocol_count=args.reviewed_protocol_count,
            proposal_paths=args.proposal,
            decision_paths=args.decision,
            application_log_paths=args.application_log,
            regression_results_path=args.regression_results,
            previous_checkpoint_path=args.previous_checkpoint,
            output_path=args.out,
            proposal_schema_path=args.proposal_schema,
            decision_schema_path=args.decision_schema,
            report_schema_path=args.report_schema,
            application_schema_path=args.application_schema,
            regression_results_schema_path=args.regression_results_schema,
            created_at=args.created_at,
        )
    except ReportingError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    metrics = report["metrics"]
    print(
        f"Checkpoint {report['checkpoint_id']}: {metrics['confirmed_error_field_count']}/"
        f"{metrics['audited_field_count']} confirmed error fields."
    )
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
