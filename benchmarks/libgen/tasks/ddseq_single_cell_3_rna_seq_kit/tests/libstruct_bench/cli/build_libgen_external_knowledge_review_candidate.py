from __future__ import annotations

import argparse
import json
from pathlib import Path

from libstruct_bench.audit.external_knowledge import (
    build_external_knowledge_review_candidate,
    load_json,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a detached review candidate comparing an immutable Libgen "
            "external-knowledge package with a primer-only revision."
        )
    )
    parser.add_argument("--prior-asset-root", required=True, type=Path)
    parser.add_argument("--revised-asset-root", required=True, type=Path)
    parser.add_argument("--review-request", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--created-at",
        required=True,
        help="Pinned RFC 3339 timestamp used verbatim in the review candidate.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_path = args.out.resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite review candidate: {output_path}")
    candidate = build_external_knowledge_review_candidate(
        prior_asset_root=args.prior_asset_root.resolve(),
        revised_asset_root=args.revised_asset_root.resolve(),
        review_request=load_json(args.review_request.resolve()),
        created_at=args.created_at,
    )
    write_json(output_path, candidate)
    print(json.dumps(candidate, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
