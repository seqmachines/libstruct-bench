from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from libstruct_bench.libgen.staging import inspect_staging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create separate agent-source and private-groundtruth HF upload trees."
    )
    parser.add_argument("--protocols", default="benchmarks/libgen/protocols.json")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--groundtruth-root", required=True)
    parser.add_argument("--schema-root", default="schemas")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    config_path = Path(args.protocols)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_root = Path(args.source_root)
    groundtruth_root = Path(args.groundtruth_root)
    report = inspect_staging(
        config,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        schema_root=Path(args.schema_root),
    )
    if not report["ready"]:
        problems = sum(len(item["errors"]) for item in report["protocols"])
        raise ValueError(f"staging is incomplete or invalid ({problems} problems); run the check command")

    output_root = Path(args.out)
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite existing export directory: {output_root}")
    source_output = output_root / "protocol_sources"
    truth_output = output_root / "groundtruth"
    source_output.mkdir(parents=True)
    truth_output.mkdir(parents=True)

    source_manifest: list[dict[str, object]] = []
    truth_manifest: list[dict[str, object]] = []
    for protocol in report["protocols"]:
        for item in protocol["sources"]:
            relative = Path(item["path"])
            destination = source_output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / relative, destination)
            source_manifest.append(item)
        for item in protocol["groundtruth"]:
            relative = Path(item["path"])
            destination = truth_output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(groundtruth_root / relative, destination)
            truth_manifest.append(item)

    (source_output / "MANIFEST.json").write_text(
        json.dumps(
            {
                "repository_role": "agent_visible_primary_protocol_sources",
                "files": source_manifest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (truth_output / "MANIFEST.json").write_text(
        json.dumps(
            {
                "repository_role": "private_verifier_groundtruth",
                "files": truth_manifest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(source_output)
    print(truth_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
