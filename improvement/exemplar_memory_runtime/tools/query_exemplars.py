#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from exemplar_memory import (
    ExemplarMemoryError,
    build_usage,
    file_sha256,
    load_catalog,
    load_json_object,
    retrieve,
    write_json_atomic,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retrieve at most three pseudonymous donor subgraphs using only "
            "controlled target-source-derived features."
        )
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--work-record", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--retrieval-out", required=True)
    parser.add_argument("--usage-out", required=True)
    parser.add_argument("--max-results", type=int, choices=(1, 2, 3), default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        query = load_json_object(Path(args.query), "exemplar query")
        work_record = load_json_object(Path(args.work_record), "target work record")
        manifest, catalog, exemplars = load_catalog(Path(args.catalog))
        work_record_sha256 = file_sha256(Path(args.work_record))
        retrieval = retrieve(
            query,
            work_record,
            manifest,
            catalog,
            exemplars,
            target_work_record_sha256=work_record_sha256,
            max_results=args.max_results,
        )
        usage = build_usage(retrieval)
        write_json_atomic(Path(args.retrieval_out), retrieval)
        write_json_atomic(Path(args.usage_out), usage)
        print(
            json.dumps(
                {
                    "status": "pass",
                    "match_count": retrieval["match_count"],
                    "query_digest": retrieval["query_digest"],
                    "retrieval_digest": retrieval["retrieval_digest"],
                    "usage_digest": usage["usage_digest"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (ExemplarMemoryError, OSError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {"status": "error", "error": str(error)},
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
