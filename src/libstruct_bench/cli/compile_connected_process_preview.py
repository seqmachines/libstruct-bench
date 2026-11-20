from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.connected_process_preview import (
    ConnectedProcessPreviewError,
    compile_connected_process_preview,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compile a hash-pinned, non-promotable preview of the Libgen T3 "
            "connected-process migration."
        )
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--schema-root", type=Path, default=REPO_ROOT / "schemas")
    args = parser.parse_args(argv)

    try:
        result = compile_connected_process_preview(
            plan_path=args.plan,
            audit_root=args.audit_root,
            baseline_root=args.baseline_root,
            output_dir=args.out,
            schema_root=args.schema_root,
        )
    except ConnectedProcessPreviewError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    counts = result.observed_structure
    print(f"Preview: {result.output_dir}")
    print(f"Manifest: {result.manifest_path}")
    print(
        "Validated structure: "
        f"{counts['protocols']} protocols, {counts['workflows']} workflows, "
        f"{counts['terminal_outputs']} terminal outputs"
    )
    print("Canonical ground truth was not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
