from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.release import ReleaseError, build_release_manifest


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify release gates and build a candidate or frozen ground-truth manifest.")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--spec-schema", type=Path, default=SCHEMA_DIR / "release_spec.v1.schema.json")
    parser.add_argument("--release-schema", type=Path, default=SCHEMA_DIR / "groundtruth_release_manifest.v2.schema.json")
    args = parser.parse_args(argv)
    try:
        manifest = build_release_manifest(
            spec_path=args.spec,
            artifact_root=args.artifact_root,
            output_path=args.out,
            spec_schema_path=args.spec_schema,
            release_schema_path=args.release_schema,
        )
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"{manifest['release_status'].capitalize()} release: {manifest['release_id']}")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
