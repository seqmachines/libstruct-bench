from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from libstruct_bench.hf_io import env_token, load_hf_json
from libstruct_bench.library_structure import grade_library_prediction, zero_metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade v0 final library-structure predictions.")
    parser.add_argument("--prediction", default="/logs/artifacts/prediction.json")
    parser.add_argument("--groundtruth")
    parser.add_argument("--groundtruth-repo")
    parser.add_argument("--groundtruth-path")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--protocol-id")
    parser.add_argument("--reward-out", default="/logs/verifier/reward.json")
    parser.add_argument("--audit-out", default="/logs/verifier/audit.json")
    args = parser.parse_args(argv)

    reward_out = Path(args.reward_out)
    audit_out = Path(args.audit_out)
    reward_out.parent.mkdir(parents=True, exist_ok=True)
    audit_out.parent.mkdir(parents=True, exist_ok=True)

    try:
        prediction = _load_json_file(Path(args.prediction))
        ground_truth = _load_ground_truth(args)
        metrics, audit = grade_library_prediction(
            prediction,
            ground_truth,
            expected_protocol_id=args.protocol_id,
        )
    except Exception as exc:  # Harbor rewards must remain numeric; details go to audit.
        metrics = zero_metrics()
        audit = {"error": str(exc), "protocol_id": args.protocol_id}
        error_path = audit_out.parent / "error.txt"
        error_path.write_text(str(exc) + "\n", encoding="utf-8")

    reward_out.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit_out.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _load_ground_truth(args: argparse.Namespace) -> Any:
    if args.groundtruth:
        return _load_json_file(Path(args.groundtruth))
    if not args.groundtruth_repo or not args.groundtruth_path:
        raise ValueError("provide either --groundtruth or --groundtruth-repo with --groundtruth-path")
    return load_hf_json(
        repo_id=args.groundtruth_repo,
        path=args.groundtruth_path,
        revision=args.revision,
        token=env_token(),
    )


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    raise SystemExit(main())
