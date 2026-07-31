from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.inventory import (
    DatasetReference,
    InventoryError,
    build_inventory,
)


DEFAULT_SCHEMA = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "audit"
    / "audit_input_manifest.v1.schema.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory protocol evidence, legacy HTML, and current benchmark "
            "records into content-addressed audit manifests."
        )
    )
    parser.add_argument("--protocols-dir", type=Path, required=True)
    parser.add_argument("--html-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--html-map", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--created-at",
        help="fixed ISO 8601 timestamp; recommended for reproducible rebuilds",
    )
    parser.add_argument(
        "--protocol-id",
        action="append",
        dest="protocol_ids",
        help="inventory only this protocol; repeat to select multiple protocols",
    )
    parser.add_argument("--protocol-repository")
    parser.add_argument("--protocol-revision")
    parser.add_argument("--groundtruth-repository")
    parser.add_argument("--groundtruth-revision")
    parser.add_argument("--html-repository")
    parser.add_argument("--html-revision")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace JSON previously generated in the output directory",
    )
    args = parser.parse_args(argv)

    try:
        result = build_inventory(
            protocols_dir=args.protocols_dir,
            html_dir=args.html_dir,
            output_dir=args.out,
            schema_path=args.schema,
            html_map_path=args.html_map,
            created_at=args.created_at,
            protocol_ids=args.protocol_ids,
            protocol_dataset=_dataset_reference(
                args.protocol_repository,
                args.protocol_revision,
                "--protocol-repository",
                "--protocol-revision",
            ),
            groundtruth_dataset=_dataset_reference(
                args.groundtruth_repository,
                args.groundtruth_revision,
                "--groundtruth-repository",
                "--groundtruth-revision",
            ),
            html_dataset=_dataset_reference(
                args.html_repository,
                args.html_revision,
                "--html-repository",
                "--html-revision",
            ),
            force=args.force,
        )
    except InventoryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(
        f"Inventory complete: {result.ready_count}/{result.protocol_count} ready, "
        f"{result.blocked_count} blocked."
    )
    print(f"Manifests: {result.manifest_dir}")
    print(f"Report: {result.report_path}")
    return 1 if result.blocked_count else 0


def _dataset_reference(
    repository: str | None,
    revision: str | None,
    repository_flag: str,
    revision_flag: str,
) -> DatasetReference | None:
    if repository is None and revision is None:
        return None
    if repository is None or revision is None:
        raise InventoryError(
            f"{repository_flag} and {revision_flag} must be supplied together"
        )
    return DatasetReference(repository=repository, revision=revision)


if __name__ == "__main__":
    raise SystemExit(main())
