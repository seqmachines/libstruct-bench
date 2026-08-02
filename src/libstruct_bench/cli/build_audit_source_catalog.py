from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.source_catalog import SourceCatalogError, build_source_catalog


SCHEMA = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "audit"
    / "source_catalog.schema.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover every audit input and create a reviewable source catalog."
    )
    parser.add_argument("--protocols-dir", type=Path, required=True)
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        required=True,
        help="directory containing per-protocol current benchmark JSON baselines",
    )
    parser.add_argument("--html-dir", type=Path, required=True)
    parser.add_argument(
        "--html-asset-root",
        type=Path,
        help="approved root containing scg_html and any linked data assets; defaults to html-dir parent",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--local-snapshots",
        action="store_true",
        help="content-address local inputs without requiring published repositories",
    )
    parser.add_argument("--source-repository")
    parser.add_argument("--source-revision")
    parser.add_argument("--groundtruth-repository")
    parser.add_argument("--groundtruth-revision")
    parser.add_argument("--source-manifest-tsv", type=Path)
    parser.add_argument("--html-map", type=Path)
    parser.add_argument("--oligo-tsv", type=Path)
    parser.add_argument(
        "--source-protocols-prefix",
        default="protocols",
        help="path prefix for protocol files inside the source dataset",
    )
    parser.add_argument(
        "--groundtruth-protocols-prefix",
        default="ground_truth_audit/baselines",
        help="path prefix for current records inside the ground-truth dataset",
    )
    parser.add_argument(
        "--html-prefix",
        default="scg_html",
        help="path prefix for legacy HTML inside the source dataset",
    )
    parser.add_argument(
        "--oligo-tsv-dataset-path",
        default="groundtruth_oligos.tsv",
        help="path of the global oligo TSV inside the ground-truth dataset",
    )
    parser.add_argument("--previous-catalog", type=Path)
    parser.add_argument("--schema", type=Path, default=SCHEMA)
    parser.add_argument("--created-at")
    args = parser.parse_args(argv)

    try:
        result = build_source_catalog(
            protocols_dir=args.protocols_dir,
            baseline_dir=args.baseline_dir,
            html_dir=args.html_dir,
            html_asset_root=args.html_asset_root,
            output_path=args.out,
            schema_path=args.schema,
            source_repository=args.source_repository,
            source_revision=args.source_revision,
            groundtruth_repository=args.groundtruth_repository,
            groundtruth_revision=args.groundtruth_revision,
            local_snapshots=args.local_snapshots,
            source_manifest_tsv=args.source_manifest_tsv,
            html_map_path=args.html_map,
            oligo_tsv_path=args.oligo_tsv,
            source_protocols_prefix=args.source_protocols_prefix,
            groundtruth_protocols_prefix=args.groundtruth_protocols_prefix,
            html_prefix=args.html_prefix,
            oligo_tsv_dataset_path=args.oligo_tsv_dataset_path,
            previous_catalog_path=args.previous_catalog,
            created_at=args.created_at,
        )
    except SourceCatalogError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Catalog created: {result.protocol_count} protocols, "
        f"{result.source_count} sources, {result.pending_count} pending review."
    )
    print(result.catalog_path)
    return 1 if result.pending_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
