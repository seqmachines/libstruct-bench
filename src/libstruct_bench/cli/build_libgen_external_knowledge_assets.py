from __future__ import annotations

import argparse
import json
from pathlib import Path

from libstruct_bench.audit.external_knowledge import (
    build_external_knowledge_assets,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the frozen Libgen external-knowledge review package without "
            "modifying benchmark tasks or ground truth."
        )
    )
    parser.add_argument("--asset-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--groundtruth-root", required=True, type=Path)
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--schema-root", default=Path("schemas"), type=Path)
    parser.add_argument("--primer-markdown", required=True, type=Path)
    parser.add_argument("--primer-evidence-tsv", required=True, type=Path)
    parser.add_argument(
        "--created-at",
        required=True,
        help="Pinned RFC 3339 timestamp used verbatim for deterministic manifests.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_external_knowledge_assets(
        asset_root=args.asset_root.resolve(),
        source_root=args.source_root.resolve(),
        groundtruth_root=args.groundtruth_root.resolve(),
        audit_root=args.audit_root.resolve(),
        schema_root=args.schema_root.resolve(),
        primer_markdown=args.primer_markdown.resolve(),
        primer_evidence_tsv=args.primer_evidence_tsv.resolve(),
        created_at=args.created_at,
    )
    print(json.dumps(result["validation"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
