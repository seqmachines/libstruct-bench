from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_groundtruth_bundle,
)

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    freeze_tree,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    validate_digest,
    with_digest,
)
from .split_design import (
    CUMULATIVE_CHECKPOINT_LABELS,
    FINAL_DEVELOPMENT_BATCHES,
    FINAL_REBALANCE_POLICY,
    FINAL_TRANSFER_ANNOTATIONS,
    FINAL_TRANSFER_PANEL,
    FINAL_TRANSFER_PURPOSE,
    FINAL_TRANSFER_STRATA,
    FIXED_VALIDATION_PANEL,
)
from .mutation_lock import experiment_mutation_lock


FROZEN_SPLIT_SCHEMA_VERSION = "libstruct.libgen_capability_frozen_split.v1"
TEST_ISOLATION_AUDIT_SCHEMA_VERSION = (
    "libstruct.libgen_capability_test_isolation_audit.v1"
)
SPLIT_SUPERSESSION_SCHEMA_VERSION = "libstruct.libgen_capability_split_supersession.v1"
TRANSFER_PANEL_COMMITMENT_SCHEMA_VERSION = (
    "libstruct.libgen_transfer_panel_commitment.v1"
)
PANEL_ID = "frozen-retrospective-transfer-panel-v1"
VALIDATION_PANEL_ID = "fixed-validation-panel-v1"
VALIDATION_LEARNING_VISIBILITY = "five_protocol_macro_aggregate_only_no_example_memory"
ACTIVE_SPLIT_PATH = Path("design/frozen_split.json")
ACTIVE_ISOLATION_AUDIT_PATH = Path("design/test_isolation_audit.json")
ACTIVE_REATTESTATION_PATH = Path("design/checkpoint_reattestation.json")
FINAL_LOCK_CANDIDATES = (
    Path("design/final_lock.json"),
    Path("design/final_development_lock.json"),
    Path("design/transfer_panel_authorization.json"),
    Path("final"),
)
TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".txt",
    ".tsv",
    ".csv",
    ".toml",
    ".yaml",
    ".yml",
}
AUDITED_ARTIFACT_SCOPES = ("rounds", "packs", "checkpoints", "final", "runs")
ISOLATION_CATEGORIES = (
    "groundtruth_learning_contribution",
    "verifier_error_learning_contribution",
    "proposal_contribution",
    "human_decision_contribution",
    "synthetic_test_design_contribution",
    "capability_update_contribution",
    "development_access_paths",
    "harbor_or_replay_exposure",
)
LEARNING_ISOLATION_CATEGORIES = ISOLATION_CATEGORIES[:6]
GROUNDTRUTH_FILENAMES = (
    "groundtruth_final_lib_struct.json",
    "groundtruth_oligos.json",
    "groundtruth_library_generation_workflow.json",
)


def build_frozen_split(
    *,
    batches: Sequence[Mapping[str, Any]],
    panel_protocol_ids: Sequence[str],
    recorded_at: str,
    status: str,
    transfer_strata: Mapping[str, Sequence[str]] | None,
    transfer_annotations: Sequence[Mapping[str, str]],
    purposes: Sequence[str],
    rebalance_policy: str,
    split_id: str,
    validation_protocol_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the one active 25/5/10 split.

    Superseded split bytes are preserved under history and are intentionally
    outside the active schema.  Use ``_build_superseded_frozen_split`` only
    while constructing an immutable historical archive.
    """

    if status != "active":
        raise CapabilityImprovementError(
            "the current frozen-split builder only emits active 25/5/10 splits"
        )
    batch_values = [
        {
            "batch_id": str(batch["batch_id"]),
            "phase": str(batch["phase"]),
            "checkpoint_size": int(batch["checkpoint_size"]),
            "protocol_ids": list(batch["protocol_ids"]),
        }
        for batch in batches
    ]
    panel_values = list(panel_protocol_ids)
    strata_values = [
        {"name": name, "protocol_ids": list(protocol_ids)}
        for name, protocol_ids in (transfer_strata or {}).items()
    ]
    annotation_values = [dict(item) for item in transfer_annotations]
    split_payload = {
        "development_batches": batch_values,
        "final_test_panel": {
            "panel_id": PANEL_ID,
            "protocol_ids": panel_values,
            "transfer_strata": strata_values,
            "transfer_annotations": annotation_values,
            "purposes": list(purposes),
            "commitment_sha256": _final_panel_commitment_digest(),
        },
        "rebalance_policy": rebalance_policy,
    }
    validation_values = list(validation_protocol_ids)
    if tuple(validation_values) != FIXED_VALIDATION_PANEL:
        raise CapabilityImprovementError(
            "active validation panel differs from the fixed five protocols"
        )
    from .validation import validation_panel_commitment_digest

    split_payload["validation_panel"] = {
        "panel_id": VALIDATION_PANEL_ID,
        "protocol_ids": validation_values,
        "evaluation_checkpoints": list(CUMULATIVE_CHECKPOINT_LABELS),
        "learning_visibility": VALIDATION_LEARNING_VISIBILITY,
        "commitment_sha256": validation_panel_commitment_digest(),
    }
    _validate_partition(batch_values, validation_values, panel_values)
    if transfer_strata is not None:
        stratified = [
            protocol_id
            for item in strata_values
            for protocol_id in item["protocol_ids"]
        ]
        if len(stratified) != len(panel_values) or set(stratified) != set(panel_values):
            raise CapabilityImprovementError(
                "transfer strata must partition the frozen test panel"
            )
    _validate_transfer_annotations(panel_values, strata_values, annotation_values)
    _validate_active_split_design(split_payload)
    payload: dict[str, Any] = {
        "schema_version": FROZEN_SPLIT_SCHEMA_VERSION,
        "split_id": split_id,
        "recorded_at": normalized_timestamp(recorded_at),
        "status": status,
        **split_payload,
        "split_digest": canonical_digest(split_payload),
    }
    result = with_digest(payload, "split_manifest_digest")
    validate_document(
        result,
        improvement_schema_root() / "frozen_split.schema.json",
        label="frozen capability split",
    )
    return result


def _build_superseded_frozen_split(
    *,
    batches: Sequence[Mapping[str, Any]],
    panel_protocol_ids: Sequence[str],
    recorded_at: str,
    transfer_strata: Mapping[str, Sequence[str]],
    transfer_annotations: Sequence[Mapping[str, str]],
    purposes: Sequence[str],
    rebalance_policy: str,
    split_id: str,
) -> dict[str, Any]:
    """Serialize legacy 30/10 split bytes for immutable history only."""

    batch_values = [
        {
            "batch_id": str(batch["batch_id"]),
            "phase": str(batch["phase"]),
            "checkpoint_size": int(batch["checkpoint_size"]),
            "protocol_ids": list(batch["protocol_ids"]),
        }
        for batch in batches
    ]
    panel_values = list(panel_protocol_ids)
    strata_values = [
        {"name": name, "protocol_ids": list(protocol_ids)}
        for name, protocol_ids in transfer_strata.items()
    ]
    annotation_values = [dict(item) for item in transfer_annotations]
    _validate_superseded_partition(batch_values, panel_values)
    _validate_transfer_annotations(panel_values, strata_values, annotation_values)
    semantic = {
        "development_batches": batch_values,
        "final_test_panel": {
            "panel_id": PANEL_ID,
            "protocol_ids": panel_values,
            "transfer_strata": strata_values,
            "transfer_annotations": annotation_values,
            "purposes": list(purposes),
        },
        "rebalance_policy": rebalance_policy,
    }
    return with_digest(
        {
            "schema_version": FROZEN_SPLIT_SCHEMA_VERSION,
            "split_id": split_id,
            "recorded_at": normalized_timestamp(recorded_at),
            "status": "superseded",
            **semantic,
            "split_digest": canonical_digest(semantic),
        },
        "split_manifest_digest",
    )


def validate_superseded_frozen_split(path: Path) -> dict[str, Any]:
    """Validate a preserved pre-migration 30/10 split without active schema reuse."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read superseded frozen split {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise CapabilityImprovementError("superseded frozen split must be an object")
    required = {
        "schema_version",
        "split_id",
        "recorded_at",
        "status",
        "development_batches",
        "final_test_panel",
        "rebalance_policy",
        "split_digest",
        "split_manifest_digest",
    }
    if set(value) != required:
        raise CapabilityImprovementError(
            "superseded frozen split has an invalid closed-world shape"
        )
    if value.get("status") != "superseded":
        raise CapabilityImprovementError(
            "archived frozen split must retain superseded status"
        )
    validate_digest(value, "split_manifest_digest")
    semantic = {
        "development_batches": value["development_batches"],
        "final_test_panel": value["final_test_panel"],
        "rebalance_policy": value["rebalance_policy"],
    }
    if canonical_digest(semantic) != value.get("split_digest"):
        raise CapabilityImprovementError(
            "superseded frozen split has a stale semantic digest"
        )
    panel = value["final_test_panel"]
    if not isinstance(panel, Mapping):
        raise CapabilityImprovementError(
            "superseded frozen split panel must be an object"
        )
    _validate_superseded_partition(
        value["development_batches"], panel.get("protocol_ids", ())
    )
    _validate_transfer_annotations(
        panel.get("protocol_ids", ()),
        panel.get("transfer_strata", ()),
        panel.get("transfer_annotations", ()),
    )
    return value


def validate_frozen_split(path: Path) -> dict[str, Any]:
    result = load_and_validate(
        path,
        schema_filename="frozen_split.schema.json",
        digest_field="split_manifest_digest",
        label="frozen capability split",
    )
    semantic = {
        "development_batches": result["development_batches"],
        "final_test_panel": result["final_test_panel"],
        "rebalance_policy": result["rebalance_policy"],
    }
    if "validation_panel" in result:
        semantic["validation_panel"] = result["validation_panel"]
    if canonical_digest(semantic) != result["split_digest"]:
        raise CapabilityImprovementError("frozen split has a stale semantic digest")
    _validate_partition(
        result["development_batches"],
        result.get("validation_panel", {}).get("protocol_ids", ()),
        result["final_test_panel"]["protocol_ids"],
    )
    _validate_transfer_annotations(
        result["final_test_panel"]["protocol_ids"],
        result["final_test_panel"]["transfer_strata"],
        result["final_test_panel"]["transfer_annotations"],
    )
    _validate_active_split_design(result)
    return result


def validate_transfer_panel_commitment(path: Path) -> dict[str, Any]:
    result = load_and_validate(
        path,
        schema_filename="transfer_panel_commitment.schema.json",
        digest_field="commitment_manifest_digest",
        label="frozen transfer-panel commitment",
    )
    _validate_transfer_panel_commitment_document(result)
    return result


def build_test_isolation_audit(
    *,
    experiment_root: Path,
    active_batches: Sequence[Mapping[str, Any]],
    audited_at: str,
    transfer_access_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    protocol_ids = tuple(FINAL_TRANSFER_PANEL)
    hits = _learning_reference_hits(
        root,
        protocol_ids,
        transfer_access_policy=transfer_access_policy,
    )
    categories: dict[str, list[dict[str, str]]] = {
        category: [] for category in ISOLATION_CATEGORIES
    }
    for hit in hits:
        relative = hit["path"]
        lowered = relative.lower()
        scope = _audited_artifact_scope(Path(relative).parts)
        assigned_learning_category = False
        if scope == "rounds":
            if (
                relative.endswith("/packet.json")
                or "/inputs/evidence/" in relative
                or "groundtruth" in lowered
            ):
                categories["groundtruth_learning_contribution"].append(hit)
                assigned_learning_category = True
            if any(
                token in lowered
                for token in (
                    "verifier",
                    "error_analysis",
                    "reward.json",
                    "details.json",
                )
            ):
                categories["verifier_error_learning_contribution"].append(hit)
                assigned_learning_category = True
            if any(
                token in lowered
                for token in ("proposal", "candidate", "proposer-workspace")
            ):
                categories["proposal_contribution"].append(hit)
                assigned_learning_category = True
            if "/human/" in relative and "decision" in lowered:
                categories["human_decision_contribution"].append(hit)
                assigned_learning_category = True
            if "synthetic_tests" in lowered:
                categories["synthetic_test_design_contribution"].append(hit)
                assigned_learning_category = True
            if any(token in lowered for token in ("application", "/output-pack/")):
                categories["capability_update_contribution"].append(hit)
                assigned_learning_category = True
            # A hit anywhere in a learning round is itself evidence that the
            # frozen test panel crossed into capability-learning state.  File
            # naming is not a safe basis for allowing an otherwise-unclassified
            # artifact through the isolation gate, so conservatively record it
            # as a potential capability-update contribution.
            if not assigned_learning_category:
                categories["capability_update_contribution"].append(hit)
        if scope in {"packs", "checkpoints"}:
            categories["capability_update_contribution"].append(hit)
            if "synthetic_tests" in lowered:
                categories["synthetic_test_design_contribution"].append(hit)
        if scope in {"final", "runs"}:
            categories["harbor_or_replay_exposure"].append(hit)

    development = {
        protocol_id for batch in active_batches for protocol_id in batch["protocol_ids"]
    }
    overlap = [item for item in protocol_ids if item in development]
    if overlap:
        manifest_path = root / "design" / "experiment_manifest.json"
        manifest_sha = (
            sha256_file(manifest_path) if manifest_path.is_file() else "0" * 64
        )
        categories["development_access_paths"] = [
            {
                "protocol_id": protocol_id,
                "path": "design/experiment_manifest.json",
                "sha256": manifest_sha,
            }
            for protocol_id in overlap
        ]

    checks: list[dict[str, Any]] = []
    for category, findings in categories.items():
        if category == "development_access_paths" and findings:
            status = "migration_required"
        else:
            status = "fail" if findings else "pass"
        checks.append(
            {
                "category": category,
                "status": status,
                "findings": _deduplicate_findings(findings),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": TEST_ISOLATION_AUDIT_SCHEMA_VERSION,
        "audit_id": "libgen-capability-final-test-isolation",
        "audited_at": normalized_timestamp(audited_at),
        "protocol_ids": list(protocol_ids),
        "inspection_scope": "protocol_identifiers_artifact_roles_paths_and_hashes_only",
        "test_groundtruth_content_opened": False,
        "test_score_values_opened": False,
        "checks": checks,
        "learning_isolation": (
            "fail"
            if any(categories[category] for category in LEARNING_ISOLATION_CATEGORIES)
            else "pass"
        ),
        "development_access_isolation": ("migration_required" if overlap else "pass"),
    }
    result = with_digest(payload, "audit_digest")
    validate_document(
        result,
        improvement_schema_root() / "test_isolation_audit.schema.json",
        label="final-test isolation audit",
    )
    _validate_test_isolation_document(result)
    return result


def validate_test_isolation_audit(path: Path) -> dict[str, Any]:
    result = load_and_validate(
        path,
        schema_filename="test_isolation_audit.schema.json",
        digest_field="audit_digest",
        label="final-test isolation audit",
    )
    _validate_test_isolation_document(result)
    return result


def freeze_test_split(
    *,
    experiment_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    recorded_at: str,
    authorize_freeze: bool = False,
) -> dict[str, Any]:
    """Run a read-only preflight or freeze under an exclusive transaction lock."""

    root = experiment_root.expanduser().resolve()
    if not authorize_freeze:
        journal = root.parent / f".{root.name}.split-freeze-journal.json"
        if journal.exists():
            raise CapabilityImprovementError(
                "an interrupted authorized split freeze requires recovery before preflight"
            )
        if (root / ACTIVE_SPLIT_PATH).is_file():
            return _existing_frozen_split_result(root)
        return _freeze_test_split_locked(
            experiment_root=root,
            source_root=source_root,
            groundtruth_root=groundtruth_root,
            recorded_at=recorded_at,
            authorize_freeze=False,
        )
    with _split_freeze_lock(root):
        recovered = _recover_split_transaction(root)
        if recovered is not None:
            return recovered
        if (root / ACTIVE_SPLIT_PATH).is_file():
            return _existing_frozen_split_result(root)
        return _freeze_test_split_locked(
            experiment_root=root,
            source_root=source_root,
            groundtruth_root=groundtruth_root,
            recorded_at=recorded_at,
            authorize_freeze=authorize_freeze,
        )


def _existing_frozen_split_result(root: Path) -> dict[str, Any]:
    from .experiment import validate_experiment_manifest
    from .validation import validation_panel_commitment_digest

    manifest = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    split = validate_frozen_split(root / ACTIVE_SPLIT_PATH)
    expected_batches = _public_batches(FINAL_DEVELOPMENT_BATCHES)
    if (
        split["status"] != "active"
        or split["development_batches"] != expected_batches
        or split.get("validation_panel")
        != {
            "panel_id": VALIDATION_PANEL_ID,
            "protocol_ids": list(FIXED_VALIDATION_PANEL),
            "evaluation_checkpoints": list(CUMULATIVE_CHECKPOINT_LABELS),
            "learning_visibility": VALIDATION_LEARNING_VISIBILITY,
            "commitment_sha256": validation_panel_commitment_digest(),
        }
        or split["final_test_panel"]["protocol_ids"] != list(FINAL_TRANSFER_PANEL)
        or split["final_test_panel"]["transfer_strata"]
        != [
            {"name": name, "protocol_ids": list(protocol_ids)}
            for name, protocol_ids in FINAL_TRANSFER_STRATA.items()
        ]
        or split["final_test_panel"]["transfer_annotations"]
        != [dict(item) for item in FINAL_TRANSFER_ANNOTATIONS]
        or split["final_test_panel"]["purposes"] != list(FINAL_TRANSFER_PURPOSE)
    ):
        raise CapabilityImprovementError(
            "an active frozen split exists but differs from the final design"
        )
    isolation = validate_test_isolation_audit(root / ACTIVE_ISOLATION_AUDIT_PATH)
    if (
        isolation["learning_isolation"] != "pass"
        or isolation["development_access_isolation"] != "pass"
    ):
        raise CapabilityImprovementError(
            "the active frozen split has a failing isolation audit"
        )
    return {
        "status": "test_split_already_frozen",
        "new_experiment_digest": manifest["experiment_digest"],
        "new_split_digest": split["split_digest"],
        "development_protocol_ids": [
            protocol_id
            for batch in FINAL_DEVELOPMENT_BATCHES
            for protocol_id in batch["protocol_ids"]
        ],
        "validation_protocol_ids": list(FIXED_VALIDATION_PANEL),
        "test_protocol_ids": list(FINAL_TRANSFER_PANEL),
        "transfer_annotations": [dict(item) for item in FINAL_TRANSFER_ANNOTATIONS],
        "frozen_split_path": (root / ACTIVE_SPLIT_PATH).as_posix(),
        "test_isolation_audit_path": (root / ACTIVE_ISOLATION_AUDIT_PATH).as_posix(),
        "checkpoint_reattestation_path": (root / ACTIVE_REATTESTATION_PATH).as_posix(),
        "harbor_run_started": False,
    }


def _freeze_test_split_locked(
    *,
    experiment_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    recorded_at: str,
    authorize_freeze: bool = False,
) -> dict[str, Any]:
    """Inspect or atomically replace the active v1 development/test split.

    With ``authorize_freeze=False`` this is a read-only preflight.  The
    authorized path archives the stale design and rounds on the same
    filesystem, writes the final split and isolation controls, and never
    launches Harbor or an agent process.
    """

    root = experiment_root.expanduser().resolve()
    prior_manifest_path = root / "design" / "experiment_manifest.json"
    from .experiment import validate_experiment_manifest
    from .governance import (
        build_transfer_access_policy,
        validate_transfer_access_policy,
    )

    prior = validate_experiment_manifest(
        prior_manifest_path,
        experiment_root=root,
    )
    prior_policy = validate_transfer_access_policy(
        root / prior["frozen_retrospective_transfer_panel"]["access_policy"]["path"]
    )
    _validate_freeze_roots_against_prior_policy(
        policy=prior_policy,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
    )

    proposed_panel_commitment = _final_panel_commitment_digest()
    proposed_policy = build_transfer_access_policy(
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        panel_commitment_sha256=proposed_panel_commitment,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        baseline_run_roots=(),
    )
    preflight = _preflight(
        root,
        prior,
        recorded_at,
        transfer_access_policy=proposed_policy,
    )
    old_split = _build_superseded_frozen_split(
        batches=prior["batches"],
        panel_protocol_ids=prior["frozen_retrospective_transfer_panel"]["protocol_ids"],
        recorded_at=prior["created_at"],
        transfer_strata={
            "unclassified_superseded_panel": prior[
                "frozen_retrospective_transfer_panel"
            ]["protocol_ids"]
        },
        transfer_annotations=[
            {
                "protocol_id": protocol_id,
                "stratum": "unclassified_superseded_panel",
                "rationale": (
                    "Superseded pre-freeze panel; no final transfer stratum was "
                    "assigned."
                ),
            }
            for protocol_id in prior["frozen_retrospective_transfer_panel"][
                "protocol_ids"
            ]
        ],
        purposes=("superseded_unstratified_transfer_panel",),
        rebalance_policy="preexisting_frozen_partition",
        split_id=f"superseded-{prior['experiment_digest'][:16]}",
    )
    new_split = build_frozen_split(
        batches=FINAL_DEVELOPMENT_BATCHES,
        validation_protocol_ids=FIXED_VALIDATION_PANEL,
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        recorded_at=recorded_at,
        status="active",
        transfer_strata=FINAL_TRANSFER_STRATA,
        transfer_annotations=FINAL_TRANSFER_ANNOTATIONS,
        purposes=FINAL_TRANSFER_PURPOSE,
        rebalance_policy=FINAL_REBALANCE_POLICY,
        split_id="libgen-capability-final-test-split",
    )
    projected_audit = build_test_isolation_audit(
        experiment_root=root,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=recorded_at,
        transfer_access_policy=proposed_policy,
    )
    if preflight["audit"]["learning_isolation"] != "pass":
        raise CapabilityImprovementError(
            "final-test protocols already contributed to capability learning"
        )
    if projected_audit["development_access_isolation"] != "pass":
        raise CapabilityImprovementError(
            "the projected final split still exposes test protocols to development"
        )
    result = {
        "status": "ready_to_freeze" if not authorize_freeze else "staging",
        "prior_experiment_digest": prior["experiment_digest"],
        "prior_panel_commitment_sha256": prior["frozen_retrospective_transfer_panel"][
            "commitment_sha256"
        ],
        "new_panel_commitment_sha256": proposed_panel_commitment,
        "new_transfer_access_policy_digest": proposed_policy["policy_digest"],
        "old_split_digest": old_split["split_digest"],
        "new_split_digest": new_split["split_digest"],
        "development_protocol_ids": [
            protocol_id
            for batch in FINAL_DEVELOPMENT_BATCHES
            for protocol_id in batch["protocol_ids"]
        ],
        "validation_protocol_ids": list(FIXED_VALIDATION_PANEL),
        "test_protocol_ids": list(FINAL_TRANSFER_PANEL),
        "batches": _public_batches(FINAL_DEVELOPMENT_BATCHES),
        "transfer_strata": {
            key: list(value) for key, value in FINAL_TRANSFER_STRATA.items()
        },
        "transfer_annotations": [dict(item) for item in FINAL_TRANSFER_ANNOTATIONS],
        "preflight_isolation_audit": preflight["audit"],
        "projected_isolation_audit": projected_audit,
        "completed_checkpoint_ids": preflight["completed_checkpoint_ids"],
        "human_review_state": preflight["human_review_state"],
        "harbor_run_started": False,
    }
    if not authorize_freeze:
        return result
    return _apply_test_split(
        root=root,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        prior=prior,
        old_split=old_split,
        new_split=new_split,
        isolation_audit=projected_audit,
        preflight=preflight,
        recorded_at=recorded_at,
        base_result=result,
    )


def _preflight(
    root: Path,
    prior: Mapping[str, Any],
    recorded_at: str,
    transfer_access_policy: Mapping[str, Any],
) -> dict[str, Any]:
    if not root.is_dir():
        raise CapabilityImprovementError(f"experiment root is missing: {root}")
    existing_final = [
        path.as_posix() for path in FINAL_LOCK_CANDIDATES if (root / path).exists()
    ]
    if existing_final:
        raise CapabilityImprovementError(
            "final lock, transfer authorization, or replay already exists: "
            + ", ".join(existing_final)
        )
    prospective_rounds = [
        f"rounds/B{index}"
        for index in range(3, 7)
        if (root / "rounds" / f"B{index}").exists()
        and any((root / "rounds" / f"B{index}").rglob("*"))
    ]
    if prospective_rounds:
        raise CapabilityImprovementError(
            "B3-B6 learning artifacts already exist: " + ", ".join(prospective_rounds)
        )
    discovered = (
        {
            path.name
            for path in (root / "checkpoints").iterdir()
            if path.is_dir() and (path / "checkpoint.json").is_file()
        }
        if (root / "checkpoints").is_dir()
        else set()
    )
    if discovered != {"A5", "A10"}:
        raise CapabilityImprovementError(
            "split freeze requires exactly the existing A5 and A10 checkpoints"
        )
    completed = ["A5", "A10"]
    human_state, human_restage_fingerprint = _empty_h5_restage_snapshot(root / "rounds")
    audit = build_test_isolation_audit(
        experiment_root=root,
        active_batches=prior["batches"],
        audited_at=recorded_at,
        transfer_access_policy=transfer_access_policy,
    )
    return {
        "audit": audit,
        "completed_checkpoint_ids": completed,
        "human_review_state": human_state,
        "human_restage_fingerprint": human_restage_fingerprint,
        "prior_manifest_sha256": sha256_file(
            root / "design" / "experiment_manifest.json"
        ),
    }


def _apply_test_split(
    *,
    root: Path,
    source_root: Path,
    groundtruth_root: Path,
    prior: Mapping[str, Any],
    old_split: Mapping[str, Any],
    new_split: Mapping[str, Any],
    isolation_audit: Mapping[str, Any],
    preflight: Mapping[str, Any],
    recorded_at: str,
    base_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Perform the authorized transaction.

    The final manifest and checkpoint re-attestation are assembled by the
    checkpoint integration module.  Keeping that import local prevents an
    experiment/split constant cycle.
    """

    # Local imports keep split constants dependency-only for experiment.py.
    from .experiment import (
        build_checkpoint_reattestation,
        validate_experiment_manifest,
    )
    from .governance import build_transfer_access_policy

    parent = root.parent
    stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.split-freeze-", dir=parent))
    archive_relative = (
        Path("history/superseded") / f"test-split-{prior['experiment_digest'][:16]}"
    )
    archive = root / archive_relative
    if archive.exists():
        shutil.rmtree(stage, ignore_errors=True)
        raise CapabilityImprovementError(
            f"split-supersession archive already exists: {archive}"
        )
    journal = parent / f".{root.name}.split-freeze-journal.json"
    old_design = root / "design"
    old_rounds = root / "rounds"
    archived_design = archive / "design"
    archived_rounds = archive / "rounds"
    new_design_stage = stage / "design"
    new_design_stage.mkdir(parents=True)
    commit_validated = False
    try:
        if (
            sha256_file(old_design / "experiment_manifest.json")
            != preflight["prior_manifest_sha256"]
        ):
            raise CapabilityImprovementError(
                "experiment manifest changed after split-freeze preflight"
            )
        panel_commitment = _final_panel_commitment_digest()
        policy = build_transfer_access_policy(
            panel_protocol_ids=FINAL_TRANSFER_PANEL,
            panel_commitment_sha256=panel_commitment,
            source_root=source_root,
            groundtruth_root=groundtruth_root,
            baseline_run_roots=(),
        )
        if policy["policy_digest"] != base_result["new_transfer_access_policy_digest"]:
            raise CapabilityImprovementError(
                "final-test source or ground-truth hashes changed after preflight"
            )
        prospective_inventory = _groundtruth_inventory_for(
            groundtruth_root,
            [
                protocol_id
                for batch in FINAL_DEVELOPMENT_BATCHES
                if batch["phase"] == "prospective"
                for protocol_id in batch["protocol_ids"]
            ],
        )
        write_json_atomic(
            new_design_stage / "frozen_split.json", dict(new_split), mode=0o444
        )
        write_json_atomic(
            new_design_stage / "test_isolation_audit.json",
            dict(isolation_audit),
            mode=0o444,
        )
        write_json_atomic(
            new_design_stage / "transfer_access_policy.json", policy, mode=0o444
        )
        commitment_path = new_design_stage / "transfer_panel_commitment.json"
        write_json_atomic(
            commitment_path,
            _transfer_panel_commitment(panel_commitment),
            mode=0o444,
        )
        _copy_retained_design(old_design, new_design_stage)
        # Re-attestation is built against the old immutable checkpoints.  The
        # archived paths become valid only after the same-filesystem commit.
        reattestation = build_checkpoint_reattestation(
            prior_experiment_manifest=prior,
            active_batches=FINAL_DEVELOPMENT_BATCHES,
            prior_split_digest=old_split["split_digest"],
            frozen_split_path=new_design_stage / "frozen_split.json",
            test_isolation_audit_path=new_design_stage / "test_isolation_audit.json",
            checkpoint_paths=[
                root / "checkpoints" / "A5" / "checkpoint.json",
                root / "checkpoints" / "A10" / "checkpoint.json",
            ],
            experiment_root=root,
            created_at=recorded_at,
        )
        write_json_atomic(
            new_design_stage / "checkpoint_reattestation.json",
            reattestation,
            mode=0o444,
        )
        archived_design_records = _tree_artifact_records(
            old_design, relative_prefix=archive_relative / "design"
        )
        rounds_digest, rounds_count = _tree_digest_optional(old_rounds)
        supersession = _build_split_supersession(
            prior=prior,
            old_split=old_split,
            new_split=new_split,
            archive_relative=archive_relative,
            archived_design_records=archived_design_records,
            rounds_digest=rounds_digest,
            rounds_count=rounds_count,
            completed_checkpoint_ids=preflight["completed_checkpoint_ids"],
            human_review_state=preflight["human_review_state"],
            isolation_audit=isolation_audit,
            checkpoint_reattestation=reattestation,
            recorded_at=recorded_at,
        )
        write_json_atomic(stage / "split_supersession.json", supersession)
        write_json_atomic(stage / "old_frozen_split.json", dict(old_split))
        manifest = _build_active_experiment_manifest(
            prior=prior,
            prospective_inventory=prospective_inventory,
            panel_commitment=panel_commitment,
            new_split_path=new_design_stage / "frozen_split.json",
            isolation_path=new_design_stage / "test_isolation_audit.json",
            reattestation_path=new_design_stage / "checkpoint_reattestation.json",
            policy_path=new_design_stage / "transfer_access_policy.json",
            commitment_path=commitment_path,
            supersession_path=stage / "split_supersession.json",
            archive_relative=archive_relative,
        )
        write_json_atomic(
            new_design_stage / "experiment_manifest.json", manifest, mode=0o444
        )
        _write_transaction_journal(
            journal,
            root=root,
            archive_relative=archive_relative,
            stage=stage,
            prior_experiment_digest=prior["experiment_digest"],
            new_experiment_digest=manifest["experiment_digest"],
            phase="prepared",
        )
        _assert_h5_restage_snapshot(
            rounds_root=old_rounds,
            expected_state=preflight["human_review_state"],
            expected_fingerprint=preflight["human_restage_fingerprint"],
            context="immediately before split-freeze archival",
        )
        archive.mkdir(parents=True)
        os.replace(old_design, archived_design)
        if old_rounds.exists():
            os.replace(old_rounds, archived_rounds)
        if old_rounds.exists():
            raise CapabilityImprovementError(
                "active rounds unexpectedly reappeared after split-freeze archival"
            )
        _assert_h5_restage_snapshot(
            rounds_root=archived_rounds,
            expected_state=preflight["human_review_state"],
            expected_fingerprint=preflight["human_restage_fingerprint"],
            context="in the split-freeze archive before restaging",
        )
        os.replace(stage / "old_frozen_split.json", archive / "frozen_split.json")
        os.replace(
            stage / "split_supersession.json", archive / "split_supersession.json"
        )
        os.replace(new_design_stage, root / "design")
        _reattach_supersession_reference(root, archive_relative)
        validate_experiment_manifest(
            root / "design" / "experiment_manifest.json",
            experiment_root=root,
        )
        if preflight["human_review_state"] == (
            "empty_in_progress_archived_for_restage"
        ):
            _restage_empty_h5(
                root=root,
                archived_rounds=archived_rounds,
                source_root=source_root,
                groundtruth_root=groundtruth_root,
                recorded_at=recorded_at,
                expected_fingerprint=preflight["human_restage_fingerprint"],
            )
            refreshed_audit = build_test_isolation_audit(
                experiment_root=root,
                active_batches=FINAL_DEVELOPMENT_BATCHES,
                audited_at=recorded_at,
                transfer_access_policy=policy,
            )
            if refreshed_audit != isolation_audit:
                raise CapabilityImprovementError(
                    "restaged empty H5 review changed the final-test isolation audit"
                )
        _write_transaction_journal(
            journal,
            root=root,
            archive_relative=archive_relative,
            stage=stage,
            prior_experiment_digest=prior["experiment_digest"],
            new_experiment_digest=manifest["experiment_digest"],
            phase="validated",
        )
        commit_validated = True
        freeze_tree(archive)
        _remove_file(journal)
        shutil.rmtree(stage, ignore_errors=True)
        return {
            **dict(base_result),
            "status": "test_split_frozen",
            "new_experiment_digest": manifest["experiment_digest"],
            "new_panel_commitment_sha256": panel_commitment,
            "archive_path": archive.as_posix(),
            "frozen_split_path": (root / ACTIVE_SPLIT_PATH).as_posix(),
            "test_isolation_audit_path": (
                root / ACTIVE_ISOLATION_AUDIT_PATH
            ).as_posix(),
            "checkpoint_reattestation_path": (
                root / ACTIVE_REATTESTATION_PATH
            ).as_posix(),
            "supersession_path": (archive / "split_supersession.json").as_posix(),
        }
    except BaseException as error:
        if commit_validated:
            raise CapabilityImprovementError(
                "split commit validated but archival freezing was interrupted; "
                "rerun the same command to complete journal recovery"
            ) from error
        _rollback_split_transaction(
            root=root,
            archive=archive,
            archived_design=archived_design,
            archived_rounds=archived_rounds,
            old_design=old_design,
            old_rounds=old_rounds,
        )
        _remove_file(journal)
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _build_active_experiment_manifest(
    *,
    prior: Mapping[str, Any],
    prospective_inventory: Mapping[str, Mapping[str, Any]],
    panel_commitment: str,
    new_split_path: Path,
    isolation_path: Path,
    reattestation_path: Path,
    policy_path: Path,
    commitment_path: Path,
    supersession_path: Path,
    archive_relative: Path,
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(prior))
    payload.pop("experiment_digest", None)
    payload["batches"] = []
    prospective_entries = []
    for batch in FINAL_DEVELOPMENT_BATCHES:
        record = {
            "batch_id": batch["batch_id"],
            "phase": batch["phase"],
            "checkpoint_size": batch["checkpoint_size"],
            "protocol_ids": list(batch["protocol_ids"]),
            "groundtruth_commitment_sha256": None,
        }
        if batch["phase"] == "prospective":
            entries = [
                prospective_inventory[protocol_id]
                for protocol_id in batch["protocol_ids"]
            ]
            prospective_entries.extend(entries)
            record["groundtruth_commitment_sha256"] = canonical_digest(
                {"batch_id": batch["batch_id"], "entries": entries}
            )
        payload["batches"].append(record)
    payload["prospective_groundtruth"] = {
        "set_id": "libgen-prospective-development-v1",
        "protocol_count": 20,
        "commitment_sha256": canonical_digest(
            {
                "set_id": "libgen-prospective-development-v1",
                "entries": prospective_entries,
            }
        ),
        "current_schema_valid_count": sum(
            item["linked_validation"] == "pass" for item in prospective_entries
        ),
        "compatibility_status": (
            "current_schema_valid"
            if all(item["linked_validation"] == "pass" for item in prospective_entries)
            else "hash_pinned_schema_migration_required"
        ),
        "agent_visibility": "none_until_s0_and_both_active_branches_terminal",
    }
    payload["frozen_retrospective_transfer_panel"] = {
        "set_id": PANEL_ID,
        "classification": "frozen_retrospective_transfer_panel",
        "protocol_count": 10,
        "protocol_ids": list(FINAL_TRANSFER_PANEL),
        "transfer_strata": [
            {"name": name, "protocol_ids": list(protocol_ids)}
            for name, protocol_ids in FINAL_TRANSFER_STRATA.items()
        ],
        "transfer_annotations": [dict(item) for item in FINAL_TRANSFER_ANNOTATIONS],
        "purposes": list(FINAL_TRANSFER_PURPOSE),
        "commitment_sha256": panel_commitment,
        "selection_timing": (
            "preserved_from_superseded_30_10_split_before_clean_C0_restart"
        ),
        "capability_updates_before_selection": 2,
        "selected_before_any_capability_update": False,
        "selected_after_baseline_inspection": True,
        "selection_basis": "protocol_identity_and_predeclared_transfer_structure_without_test_groundtruth_or_scores",
        "test_scores_inspected": False,
        "baseline_mode": "post_lock_c0_replay",
        "unseen_or_sealed_claim": True,
        "improvement_visibility": "blocked_for_worker_critic_and_human_review_console",
        "endpoint_labels": ["S0", "A30", "H30"],
        "access_policy": _artifact_ref(
            policy_path, "policy_digest", relative="design/transfer_access_policy.json"
        ),
    }
    payload["transfer_panel_commitment"] = _artifact_ref(
        commitment_path,
        "commitment_manifest_digest",
        relative="design/transfer_panel_commitment.json",
    )
    payload["frozen_split"] = _artifact_ref(
        new_split_path,
        "split_manifest_digest",
        relative=ACTIVE_SPLIT_PATH.as_posix(),
    )
    payload["test_isolation_audit"] = _artifact_ref(
        isolation_path,
        "audit_digest",
        relative=ACTIVE_ISOLATION_AUDIT_PATH.as_posix(),
    )
    payload["checkpoint_reattestation"] = _artifact_ref(
        reattestation_path,
        "reattestation_digest",
        relative=ACTIVE_REATTESTATION_PATH.as_posix(),
    )
    payload["split_supersession"] = _artifact_ref(
        supersession_path,
        "supersession_digest",
        relative=(archive_relative / "split_supersession.json").as_posix(),
    )
    return with_digest(payload, "experiment_digest")


def _transfer_panel_commitment(commitment: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": TRANSFER_PANEL_COMMITMENT_SCHEMA_VERSION,
        "set_id": PANEL_ID,
        "classification": "frozen_retrospective_transfer_panel",
        "protocol_count": 10,
        "protocol_ids": list(FINAL_TRANSFER_PANEL),
        "transfer_strata": [
            {"name": name, "protocol_ids": list(protocol_ids)}
            for name, protocol_ids in FINAL_TRANSFER_STRATA.items()
        ],
        "transfer_annotations": [dict(item) for item in FINAL_TRANSFER_ANNOTATIONS],
        "purposes": list(FINAL_TRANSFER_PURPOSE),
        "commitment_sha256": commitment,
        "selection_timing": (
            "preserved_from_superseded_30_10_split_before_clean_C0_restart"
        ),
        "capability_updates_before_selection": 2,
        "selected_before_any_capability_update": False,
        "selected_after_baseline_inspection": True,
        "selection_basis": "protocol_identity_and_predeclared_transfer_structure_without_test_groundtruth_or_scores",
        "test_scores_inspected": False,
        "baseline_mode": "post_lock_c0_replay",
        "unseen_or_sealed_claim": True,
    }
    result = with_digest(payload, "commitment_manifest_digest")
    validate_document(
        result,
        improvement_schema_root() / "transfer_panel_commitment.schema.json",
        label="frozen transfer-panel commitment",
    )
    _validate_transfer_panel_commitment_document(result)
    return result


def _final_panel_commitment_payload() -> dict[str, Any]:
    return {
        "set_id": PANEL_ID,
        "protocol_ids": list(FINAL_TRANSFER_PANEL),
        "transfer_strata": [
            {"name": name, "protocol_ids": list(protocol_ids)}
            for name, protocol_ids in FINAL_TRANSFER_STRATA.items()
        ],
        "transfer_annotations": [dict(item) for item in FINAL_TRANSFER_ANNOTATIONS],
        "purposes": list(FINAL_TRANSFER_PURPOSE),
    }


def _final_panel_commitment_digest() -> str:
    return canonical_digest(_final_panel_commitment_payload())


def _validate_transfer_panel_commitment_document(
    document: Mapping[str, Any],
) -> None:
    _validate_transfer_annotations(
        document["protocol_ids"],
        document["transfer_strata"],
        document["transfer_annotations"],
    )
    semantic = {
        "set_id": document["set_id"],
        "protocol_ids": document["protocol_ids"],
        "transfer_strata": document["transfer_strata"],
        "transfer_annotations": document["transfer_annotations"],
        "purposes": document["purposes"],
    }
    if canonical_digest(semantic) != document["commitment_sha256"]:
        raise CapabilityImprovementError(
            "transfer-panel commitment does not bind its annotations"
        )


def _build_split_supersession(
    *,
    prior: Mapping[str, Any],
    old_split: Mapping[str, Any],
    new_split: Mapping[str, Any],
    archive_relative: Path,
    archived_design_records: Sequence[Mapping[str, str]],
    rounds_digest: str | None,
    rounds_count: int,
    completed_checkpoint_ids: Sequence[str],
    human_review_state: str,
    isolation_audit: Mapping[str, Any],
    checkpoint_reattestation: Mapping[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SPLIT_SUPERSESSION_SCHEMA_VERSION,
        "supersession_id": f"final-test-split-{prior['experiment_digest'][:16]}",
        "recorded_at": normalized_timestamp(recorded_at),
        "prior_experiment_digest": prior["experiment_digest"],
        "prior_panel_commitment_sha256": prior["frozen_retrospective_transfer_panel"][
            "commitment_sha256"
        ],
        "old_split_digest": old_split["split_digest"],
        "new_split_digest": new_split["split_digest"],
        "archive_root": archive_relative.as_posix(),
        "archived_design_artifacts": [dict(item) for item in archived_design_records],
        "archived_round_tree_digest": rounds_digest,
        "archived_round_file_count": rounds_count,
        "completed_checkpoint_ids": list(completed_checkpoint_ids),
        "human_review_state": human_review_state,
        "test_isolation_audit_digest": isolation_audit["audit_digest"],
        "checkpoint_reattestation_digest": checkpoint_reattestation[
            "reattestation_digest"
        ],
        "history_eligibility": "immutable_superseded_split_history_only",
    }
    result = with_digest(payload, "supersession_digest")
    validate_document(
        result,
        improvement_schema_root() / "split_supersession.schema.json",
        label="capability split supersession",
    )
    return result


def _validate_freeze_roots_against_prior_policy(
    *,
    policy: Mapping[str, Any],
    source_root: Path,
    groundtruth_root: Path,
) -> None:
    for role, supplied_root, label in (
        ("target_source", source_root, "source_root"),
        ("approved_groundtruth", groundtruth_root, "groundtruth_root"),
    ):
        parent_roots = {
            Path(str(item["path"])).expanduser().resolve().parent
            for item in policy["blocked_trees"]
            if item.get("role") == role
        }
        if len(parent_roots) != 1:
            raise CapabilityImprovementError(
                f"prior transfer access policy has inconsistent {role} roots"
            )
        expected_root = next(iter(parent_roots))
        if supplied_root.expanduser().resolve() != expected_root:
            raise CapabilityImprovementError(
                f"split-freeze {label} differs from the prior active access policy"
            )


def _empty_h5_restage_snapshot(
    rounds_root: Path,
) -> tuple[str, dict[str, Any] | None]:
    human_round = rounds_root / "B1" / "human"
    decision_path = human_round / "decision.json"
    if not decision_path.exists():
        return "absent", None
    decision = load_and_validate(
        decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="in-progress H5 decision",
    )
    if decision["review_state"] != "in_progress" or decision["change_decisions"]:
        raise CapabilityImprovementError(
            "H5 contains human decisions and cannot be rebound to another split"
        )
    workspace = human_round / "proposer-workspace"
    workspace_digest, workspace_count = _tree_digest_optional(workspace)
    if workspace_digest is None or workspace_count == 0:
        raise CapabilityImprovementError(
            "empty H5 review lacks a nonempty workspace for deterministic restaging"
        )
    return (
        "empty_in_progress_archived_for_restage",
        {
            "decision_sha256": sha256_file(decision_path),
            "decision_digest": decision["decision_digest"],
            "workspace_tree_digest": workspace_digest,
            "workspace_file_count": workspace_count,
        },
    )


def _assert_h5_restage_snapshot(
    *,
    rounds_root: Path,
    expected_state: str,
    expected_fingerprint: Mapping[str, Any] | None,
    context: str,
) -> None:
    state, fingerprint = _empty_h5_restage_snapshot(rounds_root)
    if state != expected_state or fingerprint != expected_fingerprint:
        raise CapabilityImprovementError(
            f"H5 empty decision or workspace changed {context}"
        )


def _groundtruth_inventory_for(
    root: Path,
    protocol_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    resolved = root.expanduser().resolve()
    result: dict[str, dict[str, Any]] = {}
    for protocol_id in sorted(protocol_ids):
        artifacts = []
        documents: dict[str, dict[str, Any]] = {}
        for task, filename in zip(("T1", "T2", "T3"), GROUNDTRUTH_FILENAMES):
            path = resolved / protocol_id / filename
            if not path.is_file():
                raise CapabilityImprovementError(
                    f"private development ground truth is missing {protocol_id}/{filename}"
                )
            artifacts.append({"filename": filename, "sha256": sha256_file(path)})
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CapabilityImprovementError(
                    f"cannot read private development ground truth {protocol_id}/{filename}: {error}"
                ) from error
            if not isinstance(value, dict):
                raise CapabilityImprovementError(
                    f"private development ground truth must be an object: {protocol_id}/{filename}"
                )
            documents[task] = value
        try:
            validate_groundtruth_bundle(documents, protocol_id=protocol_id)
        except LibgenValidationError as error:
            linked_validation = "fails_current_schema"
            validation_error_sha256 = canonical_digest(str(error))
        else:
            linked_validation = "pass"
            validation_error_sha256 = None
        result[protocol_id] = {
            "protocol_id": protocol_id,
            "artifacts": artifacts,
            "linked_validation": linked_validation,
            "validation_error_sha256": validation_error_sha256,
        }
    return result


def _restage_empty_h5(
    *,
    root: Path,
    archived_rounds: Path,
    source_root: Path,
    groundtruth_root: Path,
    recorded_at: str,
    expected_fingerprint: Mapping[str, Any] | None,
) -> None:
    from .experiment import validate_experiment_manifest
    from .governance import validate_transfer_access_policy
    from .isolation import prepare_isolated_worker_workspace
    from .local_learning import run_local_learning
    from .packets import build_batch_packet_from_frozen_runs
    from .workflow import create_decision_template

    active_rounds = root / "rounds"
    if active_rounds.exists():
        raise CapabilityImprovementError(
            "active rounds unexpectedly reappeared before empty H5 restaging"
        )
    _assert_h5_restage_snapshot(
        rounds_root=archived_rounds,
        expected_state="empty_in_progress_archived_for_restage",
        expected_fingerprint=expected_fingerprint,
        context="in the split-freeze archive before restaging",
    )
    old_round = archived_rounds / "B1" / "human"
    old_workspace = old_round / "proposer-workspace"
    old_decision_path = old_round / "decision.json"
    if not old_workspace.is_dir() or not old_decision_path.is_file():
        raise CapabilityImprovementError(
            "empty H5 review lacks the workspace required for deterministic restaging"
        )
    old_decision = load_and_validate(
        old_decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="archived empty H5 decision",
    )
    experiment = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    policy_path = (
        root
        / experiment["frozen_retrospective_transfer_panel"]["access_policy"]["path"]
    )
    policy = validate_transfer_access_policy(policy_path)
    active_round = root / "rounds" / "B1" / "human"
    packet_path = active_round / "packet.json"
    active_round.mkdir(parents=True, exist_ok=True)
    parent_pack = root / experiment["initial_pack"]["references"]["H0"]
    packet = build_batch_packet_from_frozen_runs(
        experiment_manifest=experiment,
        branch="human",
        batch_id="B1",
        parent_pack_digest=experiment["initial_pack"]["pack_digest"],
        run_root=Path(experiment["retrospective_development_baseline"]["job_root"]),
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        transfer_access_policy=policy,
    )
    write_json_atomic(packet_path, packet, mode=0o444)
    workspace = active_round / "proposer-workspace"
    workspace_manifest = prepare_isolated_worker_workspace(
        experiment_manifest=experiment,
        packet_path=packet_path,
        parent_pack_root=parent_pack,
        access_policy_path=policy_path,
        output_root=workspace,
        mode="improvement_worker",
    )
    contract = workspace_manifest["agent_contract"]
    draft_relative = Path(str(contract["draft_output_path"]))
    event_relative = Path(str(contract["event_log_path"]))
    old_draft = old_workspace / draft_relative
    old_events = old_workspace / event_relative
    if not old_draft.is_file() or not old_events.is_file():
        raise CapabilityImprovementError(
            "empty H5 workspace lacks its proposal draft or event log"
        )
    new_candidates = workspace / "candidates"
    old_candidates = old_workspace / "candidates"
    if not old_candidates.is_dir():
        raise CapabilityImprovementError("empty H5 workspace lacks candidate bytes")
    shutil.copytree(old_candidates, new_candidates, dirs_exist_ok=True)
    (workspace / draft_relative).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(old_draft, workspace / draft_relative)
    (workspace / event_relative).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(old_events, workspace / event_relative)
    old_stderr = old_workspace / "outputs" / "proposal.stderr.log"
    if old_stderr.is_file():
        shutil.copy2(old_stderr, workspace / "outputs" / "proposal.stderr.log")
    learned = run_local_learning(
        experiment_root=root,
        batch_id="B1",
        branch="human",
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        parent_pack_root=parent_pack,
        round_root=active_round,
        workspace_root=workspace,
        agent_runner=lambda _request: (_ for _ in ()).throw(
            AssertionError("empty H5 restaging must not run an agent")
        ),
    )
    proposal_path = Path(learned["proposal_path"])
    decision = create_decision_template(
        proposal_path=proposal_path,
        reviewer_kind="human",
        reviewer_id=old_decision["reviewer"]["reviewer_id"],
        started_at=old_decision["started_at"] or recorded_at,
    )
    write_json_atomic(active_round / "decision.json", decision)


def _learning_reference_hits(
    root: Path,
    protocol_ids: Sequence[str],
    *,
    transfer_access_policy: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    scan_roots = tuple(root / name for name in AUDITED_ARTIFACT_SCOPES) + (
        root / "history",
    )
    needles = {protocol_id: protocol_id.encode("utf-8") for protocol_id in protocol_ids}
    blocked_roots: dict[str, list[Path]] = {
        protocol_id: [] for protocol_id in protocol_ids
    }
    blocked_hashes: dict[str, set[str]] = {
        protocol_id: set() for protocol_id in protocol_ids
    }
    if transfer_access_policy is not None:
        if tuple(transfer_access_policy.get("blocked_protocol_ids", ())) != tuple(
            protocol_ids
        ):
            raise CapabilityImprovementError(
                "isolation audit policy covers another test panel"
            )
        for tree in transfer_access_policy.get("blocked_trees", ()):
            protocol_id = tree.get("protocol_id")
            role = tree.get("role")
            if protocol_id not in blocked_roots or role not in {
                "target_source",
                "approved_groundtruth",
            }:
                continue
            tree_root = Path(str(tree["path"])).expanduser().resolve()
            blocked_roots[protocol_id].append(tree_root)
            for blocked_file in sorted(tree_root.rglob("*")):
                if blocked_file.is_symlink():
                    raise CapabilityImprovementError(
                        f"blocked test tree contains a symlink: {blocked_file}"
                    )
                if blocked_file.is_file():
                    blocked_hashes[protocol_id].add(sha256_file(blocked_file))
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        paths = [scan_root] if scan_root.is_file() else sorted(scan_root.rglob("*"))
        for path in paths:
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            relative_parts_tuple = path.relative_to(root).parts
            scope = _audited_artifact_scope(relative_parts_tuple)
            if scope is None:
                # Superseded design manifests legitimately name both panels.
                # Only learning/evaluation subtrees inside history are evidence
                # of prior contribution or exposure.
                continue
            relative_parts = set(relative_parts_tuple)
            file_sha = sha256_file(path)
            content = b""
            if path.suffix.lower() in TEXT_SUFFIXES:
                try:
                    content = path.read_bytes()
                except OSError as error:
                    raise CapabilityImprovementError(
                        f"cannot scan capability artifact identifiers: {path}: {error}"
                    ) from error
            for protocol_id, needle in needles.items():
                path_match = protocol_id in relative_parts
                content_match = needle in content
                blocked_path_match = any(
                    blocked_root.as_posix().encode("utf-8") in content
                    for blocked_root in blocked_roots[protocol_id]
                )
                blocked_hash_match = file_sha in blocked_hashes[protocol_id]
                if (
                    path_match
                    or content_match
                    or blocked_path_match
                    or blocked_hash_match
                ):
                    hits.append(
                        {
                            "protocol_id": protocol_id,
                            "path": relative,
                            "sha256": file_sha,
                        }
                    )
    return sorted(hits, key=lambda item: (item["path"], item["protocol_id"]))


def _audited_artifact_scope(parts: Sequence[str]) -> str | None:
    if not parts:
        return None
    if parts[0] in AUDITED_ARTIFACT_SCOPES:
        return parts[0]
    if parts[0] == "history":
        return next(
            (part for part in parts[1:] if part in AUDITED_ARTIFACT_SCOPES),
            None,
        )
    return None


def _validate_test_isolation_document(document: Mapping[str, Any]) -> None:
    if tuple(document.get("protocol_ids", ())) != FINAL_TRANSFER_PANEL:
        raise CapabilityImprovementError(
            "test-isolation audit covers another frozen panel"
        )
    checks = document.get("checks")
    if not isinstance(checks, list):
        raise CapabilityImprovementError("test-isolation checks must be an array")
    categories = [item.get("category") for item in checks if isinstance(item, Mapping)]
    if (
        len(checks) != len(ISOLATION_CATEGORIES)
        or tuple(categories) != ISOLATION_CATEGORIES
    ):
        raise CapabilityImprovementError(
            "test-isolation audit must contain the exact eight ordered categories once"
        )
    by_category = {str(item["category"]): item for item in checks}
    for category in ISOLATION_CATEGORIES:
        item = by_category[category]
        findings = item.get("findings")
        if not isinstance(findings, list):
            raise CapabilityImprovementError(
                f"test-isolation findings are invalid for {category}"
            )
        identities = []
        for finding in findings:
            if finding.get("protocol_id") not in FINAL_TRANSFER_PANEL:
                raise CapabilityImprovementError(
                    f"test-isolation finding is outside the panel: {category}"
                )
            identities.append(
                (
                    finding.get("protocol_id"),
                    finding.get("path"),
                    finding.get("sha256"),
                )
            )
        if len(identities) != len(set(identities)):
            raise CapabilityImprovementError(
                f"test-isolation audit repeats a finding: {category}"
            )
        expected_status = (
            "migration_required"
            if category == "development_access_paths" and findings
            else "fail"
            if findings
            else "pass"
        )
        if item.get("status") != expected_status:
            raise CapabilityImprovementError(
                f"test-isolation status contradicts findings: {category}"
            )
    expected_learning = (
        "fail"
        if any(
            by_category[category]["findings"]
            for category in LEARNING_ISOLATION_CATEGORIES
        )
        else "pass"
    )
    if document.get("learning_isolation") != expected_learning:
        raise CapabilityImprovementError(
            "test-isolation learning status contradicts category findings"
        )
    expected_access = (
        "migration_required"
        if by_category["development_access_paths"]["findings"]
        else "pass"
    )
    if document.get("development_access_isolation") != expected_access:
        raise CapabilityImprovementError(
            "test-isolation development status contradicts category findings"
        )


def _validate_partition(
    batches: Sequence[Mapping[str, Any]],
    validation_protocol_ids: Sequence[str],
    panel_protocol_ids: Sequence[str],
) -> None:
    development = [
        protocol_id for batch in batches for protocol_id in batch["protocol_ids"]
    ]
    validation = list(validation_protocol_ids)
    all_protocols = development + validation + list(panel_protocol_ids)
    if any(len(batch["protocol_ids"]) != 5 for batch in batches):
        raise CapabilityImprovementError(
            "split batches must each contain five protocols"
        )
    if len(batches) != 5 or len(development) != 25 or len(validation) != 5:
        raise CapabilityImprovementError(
            "active split requires exact 25/5/10 membership"
        )
    if len(panel_protocol_ids) != 10:
        raise CapabilityImprovementError("split requires ten final-test protocols")
    if len(set(all_protocols)) != 40:
        raise CapabilityImprovementError("split protocols must be disjoint and unique")


def _validate_active_split_design(document: Mapping[str, Any]) -> None:
    from .validation import validation_panel_commitment_digest

    expected_strata = [
        {"name": name, "protocol_ids": list(protocol_ids)}
        for name, protocol_ids in FINAL_TRANSFER_STRATA.items()
    ]
    expected_validation = {
        "panel_id": VALIDATION_PANEL_ID,
        "protocol_ids": list(FIXED_VALIDATION_PANEL),
        "evaluation_checkpoints": list(CUMULATIVE_CHECKPOINT_LABELS),
        "learning_visibility": VALIDATION_LEARNING_VISIBILITY,
        "commitment_sha256": validation_panel_commitment_digest(),
    }
    validation = document.get("validation_panel")
    if not isinstance(validation, Mapping):
        raise CapabilityImprovementError("active split lacks its validation panel")
    for field, expected in expected_validation.items():
        if validation.get(field) != expected:
            raise CapabilityImprovementError(
                f"active split validation panel has stale {field}"
            )
    panel = document.get("final_test_panel")
    if not isinstance(panel, Mapping):
        raise CapabilityImprovementError("active split lacks its final-test panel")
    if (
        document.get("development_batches")
        != _public_batches(FINAL_DEVELOPMENT_BATCHES)
        or tuple(panel.get("protocol_ids", ())) != FINAL_TRANSFER_PANEL
        or panel.get("transfer_strata") != expected_strata
        or panel.get("transfer_annotations")
        != [dict(item) for item in FINAL_TRANSFER_ANNOTATIONS]
        or panel.get("purposes") != list(FINAL_TRANSFER_PURPOSE)
        or panel.get("commitment_sha256") != _final_panel_commitment_digest()
        or document.get("rebalance_policy") != FINAL_REBALANCE_POLICY
    ):
        raise CapabilityImprovementError(
            "active frozen split differs from the exact 25/5/10 design"
        )


def _validate_superseded_partition(
    batches: Sequence[Mapping[str, Any]],
    panel_protocol_ids: Sequence[str],
) -> None:
    development = [
        protocol_id for batch in batches for protocol_id in batch["protocol_ids"]
    ]
    if (
        len(batches) != 6
        or any(len(batch["protocol_ids"]) != 5 for batch in batches)
        or len(development) != 30
        or len(panel_protocol_ids) != 10
        or len(set(development + list(panel_protocol_ids))) != 40
    ):
        raise CapabilityImprovementError(
            "superseded split requires a disjoint 30/10 partition"
        )


def _validate_transfer_annotations(
    panel_protocol_ids: Sequence[str],
    transfer_strata: Sequence[Mapping[str, Any]],
    transfer_annotations: Sequence[Mapping[str, Any]],
) -> None:
    annotated_ids = [item.get("protocol_id") for item in transfer_annotations]
    if annotated_ids != list(panel_protocol_ids):
        raise CapabilityImprovementError(
            "transfer annotations must cover the panel exactly in frozen order"
        )
    expected_strata: dict[str, str] = {}
    for stratum in transfer_strata:
        name = str(stratum.get("name"))
        for protocol_id in stratum.get("protocol_ids", ()):  # schema checks type
            if protocol_id in expected_strata:
                raise CapabilityImprovementError(
                    "transfer strata repeat a protocol annotation target"
                )
            expected_strata[str(protocol_id)] = name
    for item in transfer_annotations:
        protocol_id = str(item.get("protocol_id"))
        if item.get("stratum") != expected_strata.get(protocol_id):
            raise CapabilityImprovementError(
                f"transfer annotation stratum is inconsistent for {protocol_id}"
            )
        rationale = item.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise CapabilityImprovementError(
                f"transfer annotation lacks a rationale for {protocol_id}"
            )


def _deduplicate_findings(
    findings: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    by_key = {
        (item["protocol_id"], item["path"], item["sha256"]): dict(item)
        for item in findings
    }
    return [by_key[key] for key in sorted(by_key)]


def _public_batches(
    batches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "batch_id": batch["batch_id"],
            "phase": batch["phase"],
            "checkpoint_size": batch["checkpoint_size"],
            "protocol_ids": list(batch["protocol_ids"]),
        }
        for batch in batches
    ]


def _artifact_ref(path: Path, digest_field: str, *, relative: str) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "digest": str(value[digest_field]),
    }


def _copy_retained_design(source: Path, destination: Path) -> None:
    retained = (
        "s0_provenance_audit.json",
        "lineage_restart.json",
        "supersession_manifest.json",
    )
    for filename in retained:
        source_path = source / filename
        if source_path.is_file():
            shutil.copy2(source_path, destination / filename)


def _tree_artifact_records(
    root: Path,
    *,
    relative_prefix: Path,
) -> list[dict[str, str]]:
    return [
        {
            "path": (relative_prefix / path.relative_to(root)).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


def _tree_digest_optional(root: Path) -> tuple[str | None, int]:
    if not root.exists():
        return None, 0
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"split-freeze fingerprint tree contains a symlink: {path}"
            )
        if path.is_file():
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return (canonical_digest(records) if records else None, len(records))


@contextmanager
def _split_freeze_lock(root: Path) -> Iterator[None]:
    with experiment_mutation_lock(
        root,
        operation="authorized split freeze",
        authorize_split_journal_recovery=True,
    ):
        yield


def _write_transaction_journal(
    path: Path,
    *,
    root: Path,
    archive_relative: Path,
    stage: Path,
    prior_experiment_digest: str,
    new_experiment_digest: str,
    phase: str,
) -> None:
    payload = {
        "schema_version": "libstruct.libgen_capability_split_freeze_transaction.v1",
        "root": root.as_posix(),
        "archive_relative": archive_relative.as_posix(),
        "stage": stage.as_posix(),
        "prior_experiment_digest": prior_experiment_digest,
        "new_experiment_digest": new_experiment_digest,
        "phase": phase,
    }
    write_json_atomic(
        path,
        with_digest(payload, "journal_digest"),
        mode=0o600,
    )


def _recover_split_transaction(root: Path) -> dict[str, Any] | None:
    journal = root.parent / f".{root.name}.split-freeze-journal.json"
    if not journal.exists():
        return None
    try:
        document = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read split-freeze transaction journal: {error}"
        ) from error
    if not isinstance(document, dict):
        raise CapabilityImprovementError("split-freeze journal must be an object")
    validate_digest(document, "journal_digest")
    required = {
        "schema_version",
        "root",
        "archive_relative",
        "stage",
        "prior_experiment_digest",
        "new_experiment_digest",
        "phase",
        "journal_digest",
    }
    if set(document) != required:
        raise CapabilityImprovementError("split-freeze journal fields differ")
    if (
        document["schema_version"]
        != "libstruct.libgen_capability_split_freeze_transaction.v1"
        or document["root"] != root.as_posix()
        or document["phase"] not in {"prepared", "validated"}
    ):
        raise CapabilityImprovementError("split-freeze journal identity differs")
    archive_relative = Path(str(document["archive_relative"]))
    if (
        archive_relative.is_absolute()
        or ".." in archive_relative.parts
        or archive_relative.parts[:2] != ("history", "superseded")
    ):
        raise CapabilityImprovementError("split-freeze journal archive path is unsafe")
    stage = Path(str(document["stage"])).expanduser().resolve()
    expected_prefix = f".{root.name}.split-freeze-"
    if stage.parent != root.parent or not stage.name.startswith(expected_prefix):
        raise CapabilityImprovementError("split-freeze journal stage path is unsafe")
    archive = root / archive_relative
    if document["phase"] == "validated":
        from .experiment import validate_experiment_manifest

        manifest = validate_experiment_manifest(
            root / "design" / "experiment_manifest.json",
            experiment_root=root,
        )
        if manifest["experiment_digest"] != document["new_experiment_digest"]:
            raise CapabilityImprovementError(
                "validated split-freeze journal points to another active experiment"
            )
        if not archive.is_dir():
            raise CapabilityImprovementError(
                "validated split-freeze journal is missing its archive"
            )
        freeze_tree(archive)
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        _remove_file(journal)
        return {
            "status": "test_split_frozen_recovered",
            "new_experiment_digest": manifest["experiment_digest"],
            "frozen_split_path": (root / ACTIVE_SPLIT_PATH).as_posix(),
            "test_isolation_audit_path": (
                root / ACTIVE_ISOLATION_AUDIT_PATH
            ).as_posix(),
            "checkpoint_reattestation_path": (
                root / ACTIVE_REATTESTATION_PATH
            ).as_posix(),
            "archive_path": archive.as_posix(),
            "harbor_run_started": False,
        }

    _rollback_split_transaction(
        root=root,
        archive=archive,
        archived_design=archive / "design",
        archived_rounds=archive / "rounds",
        old_design=root / "design",
        old_rounds=root / "rounds",
    )
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    _remove_file(journal)
    return None


def _reattach_supersession_reference(root: Path, archive_relative: Path) -> None:
    """Rewrite the prepared manifest after archive paths become live.

    The bytes and digest are unchanged: the prepared reference already names
    the final relative archive path.  This check exists to fail clearly if a
    partially committed transaction lost its supersession record.
    """

    path = root / archive_relative / "split_supersession.json"
    if not path.is_file():
        raise CapabilityImprovementError("split supersession record was not committed")


def _rollback_split_transaction(
    *,
    root: Path,
    archive: Path,
    archived_design: Path,
    archived_rounds: Path,
    old_design: Path,
    old_rounds: Path,
) -> None:
    current_design = root / "design"
    if archived_design.exists():
        if current_design.exists():
            rollback_new = archive / "failed-new-design"
            if rollback_new.exists():
                shutil.rmtree(rollback_new, ignore_errors=True)
            os.replace(current_design, rollback_new)
        os.replace(archived_design, old_design)
    if archived_rounds.exists() and not old_rounds.exists():
        os.replace(archived_rounds, old_rounds)
    elif archived_rounds.exists() and old_rounds.exists():
        shutil.rmtree(old_rounds, ignore_errors=True)
        os.replace(archived_rounds, old_rounds)
    if archive.exists():
        shutil.rmtree(archive, ignore_errors=True)


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
