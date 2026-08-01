from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.packets import PacketError, build_phase_packet


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a physically isolated evidence or comparison audit packet."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-dataset-dir", type=Path, required=True)
    parser.add_argument("--groundtruth-dataset-dir", type=Path, required=True)
    parser.add_argument("--run-artifact-dir", type=Path)
    parser.add_argument("--rendition-bundle-dir", type=Path)
    parser.add_argument("--phase", choices=("evidence", "comparison"), required=True)
    parser.add_argument("--evidence-artifact", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("copy", "symlink"), default="copy")
    parser.add_argument(
        "--manifest-schema",
        type=Path,
        default=SCHEMA_DIR / "audit_input_manifest.v2.schema.json",
    )
    parser.add_argument(
        "--packet-schema",
        type=Path,
        default=SCHEMA_DIR / "audit_packet.v2.schema.json",
    )
    parser.add_argument(
        "--rendition-schema",
        type=Path,
        default=SCHEMA_DIR / "rendition_bundle.v1.schema.json",
    )
    args = parser.parse_args(argv)
    try:
        result = build_phase_packet(
            manifest_path=args.manifest,
            source_dataset_dir=args.source_dataset_dir,
            groundtruth_dataset_dir=args.groundtruth_dataset_dir,
            run_artifact_dir=args.run_artifact_dir,
            rendition_bundle_dir=args.rendition_bundle_dir,
            rendition_schema_path=args.rendition_schema,
            phase=args.phase,
            evidence_artifact_path=args.evidence_artifact,
            output_dir=args.out,
            mode=args.mode,
            manifest_schema_path=args.manifest_schema,
            packet_schema_path=args.packet_schema,
        )
    except PacketError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"{result.phase.capitalize()} packet ready for {result.protocol_id}: "
        f"{result.file_count} verified source files and "
        f"{result.rendition_count} verified renditions."
    )
    print(result.packet_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
