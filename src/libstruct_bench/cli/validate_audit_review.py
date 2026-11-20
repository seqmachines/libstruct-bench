from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.review import ReviewError, validate_review_decision


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a human audit decision against its immutable proposal.")
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--working", action="store_true")
    parser.add_argument("--proposal-schema", type=Path, default=SCHEMA_DIR / "protocol_audit.schema.json")
    parser.add_argument("--decision-schema", type=Path, default=SCHEMA_DIR / "review_decision.schema.json")
    args = parser.parse_args(argv)
    try:
        _, decision = validate_review_decision(
            proposal_path=args.proposal,
            decision_path=args.decision,
            proposal_schema_path=args.proposal_schema,
            decision_schema_path=args.decision_schema,
            require_final=not args.working,
        )
    except ReviewError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Validated human decision: {decision['decision_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
