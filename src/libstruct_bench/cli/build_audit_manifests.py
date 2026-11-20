from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.source_catalog import (
    SourceCatalogError,
    build_manifests_from_catalog,
)


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build phase manifests using all available catalog sources."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--reviewed-protocol-count", type=int, required=True)
    parser.add_argument("--protocol-id", action="append", dest="protocol_ids")
    parser.add_argument("--created-at")
    parser.add_argument(
        "--catalog-schema",
        type=Path,
        default=SCHEMA_DIR / "source_catalog.schema.json",
    )
    parser.add_argument(
        "--manifest-schema",
        type=Path,
        default=SCHEMA_DIR / "audit_input_manifest.schema.json",
    )
    args = parser.parse_args(argv)
    try:
        result = build_manifests_from_catalog(
            catalog_path=args.catalog,
            output_dir=args.out,
            catalog_schema_path=args.catalog_schema,
            manifest_schema_path=args.manifest_schema,
            checkpoint_id=args.checkpoint_id,
            reviewed_protocol_count=args.reviewed_protocol_count,
            created_at=args.created_at,
            protocol_ids=args.protocol_ids,
        )
    except SourceCatalogError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Manifests built: {result.ready_count}/{result.protocol_count} ready, "
        f"{result.blocked_count} blocked."
    )
    print(result.report_path)
    return 1 if result.blocked_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
