from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.review import ReviewError, render_review_packet


DEFAULT_SCHEMA = Path(__file__).resolve().parents[3] / "schemas" / "audit" / "protocol_audit.schema.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a console review queue for one audit proposal.")
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    try:
        report, template = render_review_packet(proposal_path=args.proposal, proposal_schema_path=args.schema, output_dir=args.out)
    except ReviewError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(report.read_text(encoding="utf-8"))
    print(f"Review queue: {report}")
    print(f"Decision template: {template}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
