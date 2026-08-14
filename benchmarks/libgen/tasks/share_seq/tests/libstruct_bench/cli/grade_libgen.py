from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from libstruct_bench.hf_io import download_hf_dataset_file, env_token
from libstruct_bench.libgen.scoring import LIBGEN_PUBLIC_METRIC_KEYS, grade_libgen
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_groundtruth_bundle,
    validate_prediction_links,
    validate_t2_prediction,
    validate_t3_prediction,
)


GROUNDTRUTH_FILENAMES = {
    "T1": "groundtruth_final_lib_struct.json",
    "T2": "groundtruth_oligos.json",
    "T3": "groundtruth_library_generation_workflow.json",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grade linked T2 oligo and T3 molecular-workflow predictions."
    )
    parser.add_argument("--t2-prediction", required=True)
    parser.add_argument("--t3-prediction", required=True)
    parser.add_argument("--protocol-id", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--groundtruth-dir")
    source.add_argument("--groundtruth-repo")
    parser.add_argument("--groundtruth-revision")
    parser.add_argument("--groundtruth-prefix")
    parser.add_argument("--t1-sha256", required=True)
    parser.add_argument("--t2-sha256", required=True)
    parser.add_argument("--t3-sha256", required=True)
    parser.add_argument("--schema-root", required=True)
    parser.add_argument("--reward-out", required=True)
    parser.add_argument("--details-out", required=True)
    parser.add_argument("--error-out", required=True)
    args = parser.parse_args(argv)

    outputs = [Path(args.reward_out), Path(args.details_out), Path(args.error_out)]
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)

    try:
        groundtruth = _load_groundtruth(args)
        validate_groundtruth_bundle(
            groundtruth,
            protocol_id=args.protocol_id,
            schema_root=Path(args.schema_root),
        )
    except Exception as error:
        _write_json(
            Path(args.error_out),
            {"kind": "verifier_configuration_error", "message": str(error)},
        )
        return 2

    try:
        t2_prediction = _load_json(Path(args.t2_prediction))
        t3_prediction = _load_json(Path(args.t3_prediction))
        validate_t2_prediction(
            t2_prediction,
            protocol_id=args.protocol_id,
            schema_root=Path(args.schema_root),
        )
        validate_t3_prediction(
            t3_prediction,
            protocol_id=args.protocol_id,
            schema_root=Path(args.schema_root),
        )
        validate_prediction_links(t2_prediction, t3_prediction)
    except (OSError, json.JSONDecodeError, LibgenValidationError) as error:
        metrics = _zero_metrics()
        _write_json(Path(args.reward_out), metrics)
        _write_json(
            Path(args.details_out),
            {"protocol_id": args.protocol_id, "prediction_valid": False},
        )
        _write_json(
            Path(args.error_out),
            {"kind": "invalid_prediction", "message": str(error)},
        )
        return 0

    metrics, details = grade_libgen(
        t2_prediction,
        t3_prediction,
        groundtruth["T2"],
        groundtruth["T3"],
    )
    details = {
        "protocol_id": args.protocol_id,
        "prediction_valid": True,
        "scoring": details,
    }
    _write_json(Path(args.reward_out), metrics)
    _write_json(Path(args.details_out), details)
    if Path(args.error_out).exists():
        Path(args.error_out).unlink()
    return 0


def _load_groundtruth(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    expected_hashes = {
        "T1": args.t1_sha256,
        "T2": args.t2_sha256,
        "T3": args.t3_sha256,
    }
    if args.groundtruth_dir:
        directory = Path(args.groundtruth_dir)
        return {
            task: _decode_groundtruth(
                (directory / filename).read_bytes(),
                expected_sha256=expected_hashes[task],
                label=f"{task} ground truth",
            )
            for task, filename in GROUNDTRUTH_FILENAMES.items()
        }
    if not args.groundtruth_revision:
        raise ValueError("--groundtruth-revision is required with --groundtruth-repo")
    prefix = (args.groundtruth_prefix or args.protocol_id).strip("/")
    token = env_token()
    if not token:
        raise ValueError("HF_TOKEN is required to read private ground truth")
    return {
        task: _decode_groundtruth(
            download_hf_dataset_file(
                repo_id=args.groundtruth_repo,
                path=f"{prefix}/{filename}",
                revision=args.groundtruth_revision,
                token=token,
            ),
            expected_sha256=expected_hashes[task],
            label=f"{task} ground truth",
        )
        for task, filename in GROUNDTRUTH_FILENAMES.items()
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _decode_groundtruth(
    data: bytes, *, expected_sha256: str, label: str
) -> dict[str, Any]:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"{label} hash mismatch: expected {expected_sha256}, found {actual}"
        )
    document = json.loads(data.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object")
    return document


def _write_json(path: Path, document: Any) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _zero_metrics() -> dict[str, float]:
    return {key: 0.0 for key in LIBGEN_PUBLIC_METRIC_KEYS}


if __name__ == "__main__":
    raise SystemExit(main())
