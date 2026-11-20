from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from libstruct_bench.hf_io import env_token, load_hf_json
from libstruct_bench.audited_library import (
    grade_audited_library_prediction,
    zero_metrics,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grade predictions against frozen audited final-library ground truth."
    )
    parser.add_argument("--prediction", default="/logs/artifacts/prediction.json")
    parser.add_argument("--groundtruth")
    parser.add_argument("--groundtruth-repo")
    parser.add_argument("--groundtruth-path")
    parser.add_argument("--revision")
    parser.add_argument("--protocol-id", required=True)
    parser.add_argument("--reward-out", default="/logs/verifier/reward.json")
    parser.add_argument("--audit-out", default="/logs/verifier/audit.json")
    args = parser.parse_args(argv)
    reward_out = Path(args.reward_out)
    audit_out = Path(args.audit_out)
    reward_out.parent.mkdir(parents=True, exist_ok=True)
    audit_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        prediction = _load_json(Path(args.prediction))
        groundtruth = _groundtruth(args)
        metrics, audit = grade_audited_library_prediction(
            prediction, groundtruth, expected_protocol_id=args.protocol_id
        )
    except Exception as error:
        metrics = zero_metrics()
        audit = {"error": str(error), "protocol_id": args.protocol_id}
    reward_out.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    audit_out.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def _groundtruth(args: argparse.Namespace) -> Any:
    if args.groundtruth:
        return _load_json(Path(args.groundtruth))
    if not args.groundtruth_repo or not args.groundtruth_path:
        raise ValueError(
            "provide either --groundtruth or --groundtruth-repo with --groundtruth-path"
        )
    if not isinstance(args.revision, str) or not re.fullmatch(
        r"[a-f0-9]{40,64}", args.revision
    ):
        raise ValueError(
            "Hugging Face ground truth requires an immutable 40-64 character commit hash"
        )
    return load_hf_json(
        repo_id=args.groundtruth_repo,
        path=args.groundtruth_path,
        revision=args.revision,
        token=env_token(),
    )


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    raise SystemExit(main())
