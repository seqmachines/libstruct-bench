"""Command-line entrypoint for parser-control text5 grading."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..grader import grade_prediction


def _write_json(path: str | None, payload: object) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade parser-control text5 sequence extraction.")
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--groundtruth", required=True)
    parser.add_argument("--protocol-id", required=True)
    parser.add_argument("--reward-out", required=True)
    parser.add_argument("--matches-out")
    args = parser.parse_args(argv)

    metrics, audit = grade_prediction(
        args.prediction,
        args.groundtruth,
        expected_protocol_id=args.protocol_id,
    )
    _write_json(args.reward_out, metrics)
    _write_json(args.matches_out, audit)
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
