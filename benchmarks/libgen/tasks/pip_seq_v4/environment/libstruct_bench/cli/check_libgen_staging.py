from __future__ import annotations

import argparse
import json
from pathlib import Path

from libstruct_bench.libgen.staging import inspect_staging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check approved sources and canonical T1/T2/T3 before HF export."
    )
    parser.add_argument("--protocols", default="benchmarks/libgen/protocols.json")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--groundtruth-root", required=True)
    parser.add_argument("--schema-root", default="schemas")
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.protocols).read_text(encoding="utf-8"))
    report = inspect_staging(
        config,
        source_root=Path(args.source_root),
        groundtruth_root=Path(args.groundtruth_root),
        schema_root=Path(args.schema_root),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        Path(args.report).write_text(rendered, encoding="utf-8")
    print(
        f"ready protocols: {report['ready_protocol_count']}/{report['protocol_count']}"
    )
    for protocol in report["protocols"]:
        for error in protocol["errors"]:
            print(f"{protocol['protocol_id']}: {error}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
