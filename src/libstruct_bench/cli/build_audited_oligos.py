from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.oligo_catalog import (
    OligoCatalogError,
    build_oligo_outputs,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a canonical oligo catalog and lineage-preserving TSV from "
            "human-approved per-protocol T2 artifacts."
        )
    )
    parser.add_argument("--t2", type=Path, action="append", required=True)
    parser.add_argument(
        "--decision",
        action="append",
        required=True,
        metavar="PROTOCOL_ID=DECISION_ID",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--created-at")
    parser.add_argument(
        "--t2-schema",
        type=Path,
        default=REPO_ROOT
        / "schemas"
        / "groundtruth"
        / "oligo_groundtruth.v1.schema.json",
    )
    parser.add_argument(
        "--catalog-schema",
        type=Path,
        default=REPO_ROOT
        / "schemas"
        / "groundtruth"
        / "oligo_catalog.v1.schema.json",
    )
    parser.add_argument(
        "--metadata-schema",
        type=Path,
        default=REPO_ROOT
        / "schemas"
        / "audit"
        / "oligo_output_build.v1.schema.json",
    )
    args = parser.parse_args(argv)
    try:
        result = build_oligo_outputs(
            t2_paths=args.t2,
            decision_ids_by_protocol=_decision_mapping(args.decision),
            output_dir=args.out,
            t2_schema_path=args.t2_schema,
            catalog_schema_path=args.catalog_schema,
            metadata_schema_path=args.metadata_schema,
            created_at=args.created_at,
        )
    except (OligoCatalogError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Audited oligos: {result.protocol_count} protocols, "
        f"{result.catalog_oligo_count} canonical entries, "
        f"{result.tsv_row_count} TSV rows."
    )
    print(result.output_dir)
    return 0


def _decision_mapping(values: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for value in values:
        protocol_id, separator, decision_id = value.partition("=")
        if not separator or not protocol_id or not decision_id:
            raise ValueError("--decision values must use PROTOCOL_ID=DECISION_ID")
        result.setdefault(protocol_id, []).append(decision_id)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
