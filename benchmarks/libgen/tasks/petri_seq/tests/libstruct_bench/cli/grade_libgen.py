from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from libstruct_bench.hf_io import download_hf_dataset_file, env_token
from libstruct_bench.libgen.error_analysis import (
    artifact_record,
    build_error_analysis,
    build_error_analysis_failure,
)
from libstruct_bench.libgen.scoring import LIBGEN_PUBLIC_METRIC_KEYS, grade_libgen
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION
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
    parser.add_argument("--error-analysis-out")
    parser.add_argument("--trajectory")
    parser.add_argument("--trial-id")
    args = parser.parse_args(argv)

    error_analysis_path = (
        Path(args.error_analysis_out)
        if args.error_analysis_out
        else Path(args.details_out).with_name("error_analysis.json")
    )
    outputs = [
        Path(args.reward_out),
        Path(args.details_out),
        Path(args.error_out),
        error_analysis_path,
    ]
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    trial_id = (
        args.trial_id
        or os.environ.get("HARBOR_TRIAL_NAME")
        or os.environ.get("HARBOR_SESSION_ID")
        or args.protocol_id
    )
    trajectory, trajectory_path = _load_trajectory(args.trajectory)

    try:
        groundtruth = _load_groundtruth(args)
        validate_groundtruth_bundle(
            groundtruth,
            protocol_id=args.protocol_id,
            schema_root=Path(args.schema_root),
        )
    except Exception as error:
        verifier_error = {
            "kind": "verifier_configuration_error",
            "message": str(error),
        }
        _write_json(
            Path(args.error_out),
            verifier_error,
        )
        _write_standalone_error_analysis(
            path=error_analysis_path,
            trial_id=trial_id,
            protocol_id=args.protocol_id,
            details=None,
            verifier_error=verifier_error,
            t2_prediction=None,
            t3_prediction=None,
            t2_groundtruth=None,
            t3_groundtruth=None,
            t2_prediction_path=Path(args.t2_prediction),
            t3_prediction_path=Path(args.t3_prediction),
            trajectory=trajectory,
            trajectory_path=trajectory_path,
        )
        return 2

    t2_prediction: dict[str, Any] | None = None
    t3_prediction: dict[str, Any] | None = None
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
        details = {
            "benchmark_version": LIBGEN_BENCHMARK_VERSION,
            "protocol_id": args.protocol_id,
            "prediction_valid": False,
        }
        verifier_error = {"kind": "invalid_prediction", "message": str(error)}
        _write_json(Path(args.reward_out), metrics)
        _write_json(Path(args.details_out), details)
        _write_json(Path(args.error_out), verifier_error)
        _write_standalone_error_analysis(
            path=error_analysis_path,
            trial_id=trial_id,
            protocol_id=args.protocol_id,
            details=details,
            verifier_error=verifier_error,
            t2_prediction=t2_prediction,
            t3_prediction=t3_prediction,
            t2_groundtruth=groundtruth["T2"],
            t3_groundtruth=groundtruth["T3"],
            t2_prediction_path=Path(args.t2_prediction),
            t3_prediction_path=Path(args.t3_prediction),
            trajectory=trajectory,
            trajectory_path=trajectory_path,
        )
        return 0

    metrics, details = grade_libgen(
        t2_prediction,
        t3_prediction,
        groundtruth["T2"],
        groundtruth["T3"],
    )
    details = {
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "protocol_id": args.protocol_id,
        "prediction_valid": True,
        "scoring": details,
    }
    _write_json(Path(args.reward_out), metrics)
    _write_json(Path(args.details_out), details)
    if Path(args.error_out).exists():
        Path(args.error_out).unlink()
    _write_standalone_error_analysis(
        path=error_analysis_path,
        trial_id=trial_id,
        protocol_id=args.protocol_id,
        details=details,
        verifier_error=None,
        t2_prediction=t2_prediction,
        t3_prediction=t3_prediction,
        t2_groundtruth=groundtruth["T2"],
        t3_groundtruth=groundtruth["T3"],
        t2_prediction_path=Path(args.t2_prediction),
        t3_prediction_path=Path(args.t3_prediction),
        trajectory=trajectory,
        trajectory_path=trajectory_path,
    )
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
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _load_trajectory(
    requested_path: str | None,
) -> tuple[dict[str, Any] | None, Path | None]:
    candidates = (
        [Path(requested_path)]
        if requested_path
        else [Path("/logs/agent/trajectory.json")]
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            document = _load_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(document, dict):
            return document, path
    return None, None


def _write_standalone_error_analysis(
    *,
    path: Path,
    trial_id: str,
    protocol_id: str,
    details: dict[str, Any] | None,
    verifier_error: dict[str, Any] | None,
    t2_prediction: dict[str, Any] | None,
    t3_prediction: dict[str, Any] | None,
    t2_groundtruth: dict[str, Any] | None,
    t3_groundtruth: dict[str, Any] | None,
    t2_prediction_path: Path,
    t3_prediction_path: Path,
    trajectory: dict[str, Any] | None,
    trajectory_path: Path | None,
) -> None:
    inventory = _analysis_artifact_inventory(
        t2_prediction_path=t2_prediction_path,
        t3_prediction_path=t3_prediction_path,
        trajectory_path=trajectory_path,
    )
    try:
        document = build_error_analysis(
            trial_id=trial_id,
            protocol_id=protocol_id,
            result=None,
            details=details,
            verifier_error=verifier_error,
            artifact_inventory=inventory,
            t2_prediction=t2_prediction,
            t3_prediction=t3_prediction,
            t2_groundtruth=t2_groundtruth,
            t3_groundtruth=t3_groundtruth,
            trajectory=trajectory,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
            canonical_scoring=(details or {}).get("scoring"),
        )
    except Exception as error:
        document = build_error_analysis_failure(
            trial_id=trial_id,
            protocol_id=protocol_id,
            message=f"{type(error).__name__}: {error}",
            artifact_inventory=inventory,
        )
    _write_json(path, document)


def _analysis_artifact_inventory(
    *,
    t2_prediction_path: Path,
    t3_prediction_path: Path,
    trajectory_path: Path | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path, role in (
        (t2_prediction_path, "t2_prediction"),
        (t3_prediction_path, "t3_prediction"),
        (trajectory_path, "agent_trajectory"),
    ):
        if path is not None and path.is_file():
            result.append(artifact_record(path, role=role))
    return result


def _zero_metrics() -> dict[str, float]:
    return {key: 0.0 for key in LIBGEN_PUBLIC_METRIC_KEYS}


if __name__ == "__main__":
    raise SystemExit(main())
