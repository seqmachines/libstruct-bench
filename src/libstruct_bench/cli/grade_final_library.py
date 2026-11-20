from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from libstruct_bench.hf_io import download_hf_dataset_file, env_token
from libstruct_bench.library_reference import grade_reference_variants, project_reference_variants
from libstruct_bench.library_structure import (
    PREDICTION_SCHEMA_VERSION, LibraryStructureValidationError, grade_library_prediction, zero_metrics,
)

REFERENCE_LOCK = Path(__file__).resolve().parents[1] / "library_reference_lock.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade final library structure predictions using the paper metrics.")
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
        prediction = json.loads(Path(args.prediction).read_bytes())
        reference_bytes = _load_ground_truth(args)
        metrics, audit = grade_final_library(
            prediction,
            reference_bytes,
            expected_protocol_id=args.protocol_id,
        )
    except Exception as exc:  # Harbor rewards must remain numeric; details go to audit.
        metrics = {**zero_metrics(), "exact_match": 0.0}
        audit = {"error": str(exc), "protocol_id": args.protocol_id}
        error_path = audit_out.parent / "error.txt"
        error_path.write_text(str(exc) + "\n", encoding="utf-8")

    reward_out.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit_out.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def grade_final_library(
    prediction: dict, reference_bytes: bytes, *, expected_protocol_id: str | None = None
) -> tuple[dict, dict]:
    """Score a canonical segmented reference or an already projected sequence reference."""
    # The paper scorer requires its original format tag. Adapt a copy at this boundary.
    if isinstance(prediction, dict) and "schema_version" not in prediction:
        protocol_id = prediction.get("protocol_id")
        if not isinstance(protocol_id, str) or not protocol_id.strip():
            raise LibraryStructureValidationError("prediction.protocol_id must be a non-empty string")
        prediction = {**prediction, "schema_version": PREDICTION_SCHEMA_VERSION}
    reference = json.loads(reference_bytes)
    digest = hashlib.sha256(reference_bytes).hexdigest()
    libraries = reference.get("libraries") or []
    if any("segments" in library for library in libraries):
        lock = json.loads(REFERENCE_LOCK.read_bytes())
        pinned = any(
            row["protocol_id"] == reference.get("protocol_id") and row["sha256"] == digest
            for row in lock["files"]
        )
        variants = project_reference_variants(reference, lock["length_alternatives"] if pinned else ())
        metrics, audit = grade_reference_variants(prediction, variants, expected_protocol_id=expected_protocol_id)
        audit["reference_projection"] = variants[audit["reference_variant_index"]]["projection"]
    else:
        metrics, audit = grade_library_prediction(prediction, reference, expected_protocol_id=expected_protocol_id)
    exact_match = float(
        metrics["sequence_similarity"] == 1.0
        and metrics["ground_truth_library_count"] == metrics["predicted_library_count"]
        and bool(audit["library_matches"])
        and all(m["predicted_index"] is not None and m["edit_distance"] == 0 for m in audit["library_matches"])
    )
    audit["groundtruth_sha256"] = digest
    return {**metrics, "exact_match": exact_match}, audit


def _load_ground_truth(args: argparse.Namespace) -> bytes:
    if args.groundtruth:
        return Path(args.groundtruth).read_bytes()
    if not args.groundtruth_repo or not args.groundtruth_path:
        raise ValueError("provide either --groundtruth or --groundtruth-repo with --groundtruth-path")
    return download_hf_dataset_file(
        repo_id=args.groundtruth_repo,
        path=args.groundtruth_path,
        revision=args.revision,
        token=env_token(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
