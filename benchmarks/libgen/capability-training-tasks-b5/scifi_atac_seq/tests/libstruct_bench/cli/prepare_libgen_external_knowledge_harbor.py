from __future__ import annotations

import argparse
from pathlib import Path

from libstruct_bench.audit.external_knowledge import load_json
from libstruct_bench.audit.external_knowledge_harbor import (
    build_external_knowledge_harbor_integration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build agent-only, read-only Harbor exposures for approved Libgen "
            "external-knowledge conditions."
        )
    )
    parser.add_argument("--asset-root", required=True)
    parser.add_argument("--tasks", default="benchmarks/libgen/tasks")
    parser.add_argument("--review-candidate", required=True)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--harbor-version", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    integration = build_external_knowledge_harbor_integration(
        asset_root=Path(args.asset_root),
        tasks_root=Path(args.tasks),
        review_candidate=load_json(Path(args.review_candidate)),
        approval=load_json(Path(args.approval)),
        integration_root=Path(args.out),
        harbor_version=args.harbor_version,
        created_at=args.created_at,
    )
    print(Path(args.out) / "integration_manifest.json")
    print(integration["integration_digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
