from __future__ import annotations

import argparse
import json
from pathlib import Path

from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_prediction_links,
    validate_t2_prediction,
    validate_t3_prediction,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate linked libgen T2/T3 predictions.")
    parser.add_argument("--t2", required=True)
    parser.add_argument("--t3", required=True)
    parser.add_argument("--protocol-id", required=True)
    parser.add_argument("--schema-root", required=True)
    args = parser.parse_args(argv)

    try:
        t2 = json.loads(Path(args.t2).read_text(encoding="utf-8"))
        t3 = json.loads(Path(args.t3).read_text(encoding="utf-8"))
        schema_root = Path(args.schema_root)
        validate_t2_prediction(t2, protocol_id=args.protocol_id, schema_root=schema_root)
        validate_t3_prediction(t3, protocol_id=args.protocol_id, schema_root=schema_root)
        validate_prediction_links(t2, t3)
    except (OSError, json.JSONDecodeError, LibgenValidationError) as error:
        print(f"invalid: {error}")
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
