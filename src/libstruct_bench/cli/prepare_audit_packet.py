from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.packets import PacketError, build_packet


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "audit"
DEFAULT_MANIFEST_SCHEMA = (
    SCHEMA_DIR / "audit_input_manifest.v1.schema.json"
)
DEFAULT_PACKET_SCHEMA = SCHEMA_DIR / "audit_packet.v1.schema.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify one audit input manifest and materialize only its listed "
            "files into a role-separated packet."
        )
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--protocols-dir", type=Path, required=True)
    parser.add_argument("--html-dir", type=Path, required=True)
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        help=(
            "root containing <protocol_id>/groundtruth_*.json; defaults to "
            "--protocols-dir"
        ),
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("copy", "symlink"),
        default="copy",
        help="copy creates a self-contained packet; symlink relies on external sandboxing",
    )
    parser.add_argument(
        "--manifest-schema",
        type=Path,
        default=DEFAULT_MANIFEST_SCHEMA,
    )
    parser.add_argument(
        "--packet-schema",
        type=Path,
        default=DEFAULT_PACKET_SCHEMA,
    )
    args = parser.parse_args(argv)

    try:
        result = build_packet(
            manifest_path=args.manifest,
            protocols_dir=args.protocols_dir,
            html_dir=args.html_dir,
            baseline_dir=args.baseline_dir,
            output_dir=args.out,
            mode=args.mode,
            manifest_schema_path=args.manifest_schema,
            packet_schema_path=args.packet_schema,
        )
    except PacketError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(
        f"Packet ready for {result.protocol_id}: "
        f"{result.file_count} verified files."
    )
    print(f"Packet: {result.output_dir}")
    print(f"Metadata: {result.packet_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
