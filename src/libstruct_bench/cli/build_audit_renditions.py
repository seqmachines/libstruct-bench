from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.renditions import RenditionError, build_rendition_bundle


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic text, table, and figure renditions for an audit manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-dataset-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--created-at")
    parser.add_argument("--pdf-dpi", type=int, default=144)
    parser.add_argument(
        "--manifest-schema",
        type=Path,
        default=SCHEMA_DIR / "audit_input_manifest.schema.json",
    )
    parser.add_argument(
        "--rendition-schema",
        type=Path,
        default=SCHEMA_DIR / "rendition_bundle.schema.json",
    )
    args = parser.parse_args(argv)
    try:
        result = build_rendition_bundle(
            manifest_path=args.manifest,
            source_dataset_dir=args.source_dataset_dir,
            output_dir=args.out,
            manifest_schema_path=args.manifest_schema,
            rendition_schema_path=args.rendition_schema,
            created_at=args.created_at,
            pdf_dpi=args.pdf_dpi,
        )
    except RenditionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Renditions ready for {result.protocol_id}: {result.source_count} sources, "
        f"{result.artifact_count} artifacts."
    )
    print(result.bundle_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
