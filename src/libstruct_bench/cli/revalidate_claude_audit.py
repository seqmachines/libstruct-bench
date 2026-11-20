from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.claude_runner import (
    ClaudeAuditError,
    revalidate_rejected_comparison,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate a preserved complete Claude comparison after a "
            "deterministic harness fix, without another model call."
        )
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--rejected-run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--schema",
        type=Path,
        default=AUDIT_SCHEMAS / "protocol_audit.schema.json",
    )
    parser.add_argument(
        "--packet-schema",
        type=Path,
        default=AUDIT_SCHEMAS / "audit_packet.schema.json",
    )
    args = parser.parse_args(argv)
    try:
        result = revalidate_rejected_comparison(
            packet_dir=args.packet,
            rejected_dir=args.rejected_run,
            output_dir=args.out,
            output_schema_path=args.schema,
            packet_schema_path=args.packet_schema,
        )
    except ClaudeAuditError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Revalidated comparison: {result.artifact_path}")
    print(f"Revalidation metadata: {result.metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
