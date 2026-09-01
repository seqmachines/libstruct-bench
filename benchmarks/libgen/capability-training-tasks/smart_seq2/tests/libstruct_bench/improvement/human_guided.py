from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.libgen.error_analysis import summarize_error_analysis
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    freeze_tree,
    improvement_schema_root,
    load_and_validate,
    reject_private_output_in_repository,
    safe_relative_path,
    thaw_tree,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .experiment import (
    build_batch_packet,
    validate_experiment_manifest,
    validate_final_lock,
    validate_transfer_panel_authorization,
)
from .governance import (
    assert_capability_modification_open,
    validate_transfer_access_policy,
)
from .lineage import (
    ACTIVE_BRANCH,
    BATCH_IDS,
    checkpoint_after_batch,
    checkpoint_before_batch,
)
from .validation import (
    validate_referenced_validation_access_policy,
    validate_required_validation_aggregate,
    validate_validation_aggregate,
)
from .workflow import validate_checkpoint_runtime


HUMAN_MANIFEST_SCHEMA = "human_guided_experiment.schema.json"
HUMAN_REGISTRY_SCHEMA = "human_training_registry.schema.json"
HUMAN_COMPARISON_SCHEMA = "human_protocol_comparison.schema.json"
HUMAN_DRAFT_SCHEMA = "human_protocol_review_proposal_draft.schema.json"
HUMAN_PROPOSAL_SCHEMA = "human_protocol_review_proposal.schema.json"
HUMAN_DECISION_SCHEMA = "human_protocol_review_decision.schema.json"
HUMAN_RECORD_SCHEMA = "human_protocol_review_record.schema.json"
HUMAN_GUIDANCE_SCHEMA = "human_validation_guidance.schema.json"
HUMAN_VERIFIER_REFRESH_SCHEMA = "human_verifier_refresh.schema.json"

HUMAN_MANIFEST_PATH = Path("design/human_guided_experiment.json")
HUMAN_REGISTRY_PATH = Path("design/human_training_registry.json")
_CLOSURE_FILES = frozenset(
    {
        "final_lock.json",
        "transfer_panel_authorization.json",
    }
)
_GROUNDTRUTH_ROLES = {
    "groundtruth_final_lib_struct.json": "groundtruth_t1",
    "groundtruth_oligos.json": "groundtruth_t2",
    "groundtruth_library_generation_workflow.json": "groundtruth_t3",
}
_METRICS = (
    "reward",
    "t2_exact_required_family_recall",
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)
_ELIGIBLE_RECOVERABILITY = frozenset({"recoverable", "not_applicable"})
_NUCLEOTIDE_RUN_RE = re.compile(r"(?i)(?<![A-Za-z])[ACGTN]{16,}(?![A-Za-z])")


def initialize_human_guided_experiment(
    *,
    source_experiment_root: Path,
    output_root: Path,
    experiment_id: str,
    created_at: str,
) -> dict[str, Any]:
    """Create an open human-guided lineage from an immutable completed run.

    The source experiment remains untouched. Governance history, the private
    exemplar identity map, C0, and the C0 validation aggregate are hard-linked
    when possible and copied otherwise. Final-lock and transfer-authorization
    markers are deliberately omitted from the new, still-open lineage.
    """

    source = source_experiment_root.expanduser().resolve()
    target = output_root.expanduser().resolve()
    reject_private_output_in_repository(target)
    if target.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite human-guided experiment: {target}"
        )
    source_manifest_path = source / "design" / "experiment_manifest.json"
    # A completed source may contain its authorized post-lock replay, which is
    # intentionally outside the pre-lock closed-world isolation audit.  Load
    # the hash-valid manifest here and validate the completed checkpoint/lock/
    # authorization chain below; the new closure-free stage is then subjected
    # to the full active-manifest validator before it is committed.
    experiment = load_and_validate(
        source_manifest_path,
        schema_filename="experiment_manifest.schema.json",
        digest_field="experiment_digest",
        label="completed source experiment manifest",
    )
    final_lock_path = source / "design" / "final_lock.json"
    final_lock = validate_final_lock(
        final_lock_path,
        experiment_root=source,
        experiment_manifest=experiment,
    )
    authorization_path = source / "design" / "transfer_panel_authorization.json"
    authorization = validate_transfer_panel_authorization(
        authorization_path,
        experiment_root=source,
        experiment_manifest=experiment,
        final_lock=final_lock,
    )
    c0_checkpoint, _, c0_pack = validate_checkpoint_runtime(
        source / "checkpoints" / "C0"
    )
    validation_policy = validate_referenced_validation_access_policy(
        experiment_root=source,
        experiment_manifest=experiment,
    )
    c0_aggregate_path = source / "validation" / "aggregates" / "C0.json"
    c0_aggregate = validate_validation_aggregate(
        c0_aggregate_path,
        experiment_digest=experiment["experiment_digest"],
        validation_access_policy=validation_policy,
        expected_checkpoint_label="C0",
        expected_pack_digest=c0_pack["pack_digest"],
        expected_checkpoint_root=source / "checkpoints" / "C0",
    )
    timestamp = _timestamp(created_at)

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.human-stage-", dir=target.parent)
    )
    try:
        _clone_tree(
            source / "design",
            stage / "design",
            excluded_names=_CLOSURE_FILES,
        )
        _clone_tree(source / "history", stage / "history")
        _clone_tree(source / "checkpoints" / "C0", stage / "checkpoints" / "C0")
        identity_map = source / "private" / "exemplar_identity_map.json"
        if identity_map.is_file():
            _link_or_copy(identity_map, stage / "private" / identity_map.name)
        _link_or_copy(
            c0_aggregate_path,
            stage / "validation" / "aggregates" / "C0.json",
        )

        cloned = validate_experiment_manifest(
            stage / "design" / "experiment_manifest.json",
            experiment_root=stage,
        )
        if cloned["experiment_digest"] != experiment["experiment_digest"]:
            raise CapabilityImprovementError(
                "cloned execution manifest changed experiment identity"
            )
        registry = build_human_training_registry(
            source_experiment_root=source,
            experiment_manifest=experiment,
        )
        registry_path = stage / HUMAN_REGISTRY_PATH
        write_json_atomic(registry_path, registry, mode=0o444)

        aliases = {
            "C0": "H0",
            "C5": "H5",
            "C10": "H10",
            "C15": "H15",
            "C20": "H20",
            "C25": "H25",
        }
        batches = []
        for batch in experiment["batches"]:
            before = checkpoint_before_batch(batch["batch_id"])
            after = checkpoint_after_batch(batch["batch_id"])
            batches.append(
                {
                    "batch_id": batch["batch_id"],
                    "checkpoint_from": before,
                    "checkpoint_to": after,
                    "display_checkpoint_to": aliases[after],
                    "protocol_ids": list(batch["protocol_ids"]),
                }
            )
        payload = {
            "schema_version": "libstruct.libgen_human_guided_experiment.v1",
            "experiment_id": experiment_id,
            "condition": "offline_human_guided_fixed_c0_training_outputs",
            "created_at": timestamp,
            "source_experiment": {
                "root": source.as_posix(),
                "experiment_digest": experiment["experiment_digest"],
                "manifest_sha256": sha256_file(source_manifest_path),
                "final_lock_digest": final_lock["lock_digest"],
                "final_lock_sha256": sha256_file(final_lock_path),
                "transfer_authorization_digest": authorization["authorization_digest"],
                "transfer_authorization_sha256": sha256_file(authorization_path),
            },
            "execution_experiment": {
                "manifest_path": "design/experiment_manifest.json",
                "experiment_digest": experiment["experiment_digest"],
                "manifest_sha256": sha256_file(
                    stage / "design" / "experiment_manifest.json"
                ),
                "c0_checkpoint_digest": c0_checkpoint["checkpoint_digest"],
                "c0_checkpoint_sha256": sha256_file(
                    stage / "checkpoints" / "C0" / "checkpoint.json"
                ),
                "c0_pack_digest": c0_pack["pack_digest"],
            },
            "baseline_registry": _document_ref(
                registry_path,
                relative_to=stage,
                digest=registry["registry_digest"],
            ),
            "evidence_mode": {
                "training_outputs": "frozen_c0_outputs_for_all_25_training_protocols",
                "later_checkpoint_training_reruns": False,
                "current_pack_checked_during_review": True,
                "public_interpretation": "offline_human_guided_not_online_learning_or_pure_human_causal_ablation",
            },
            "checkpoint_aliases": aliases,
            "batches": batches,
            "validation": {
                "protocol_ids": list(experiment["validation_panel"]["protocol_ids"]),
                "checkpoint_labels": ["C0", "C5", "C10", "C15", "C20", "C25"],
                "feedback_scope": "macro_means_and_counts_only_no_direct_pack_mutation",
                "c0_reuse": {
                    "aggregate_path": "validation/aggregates/C0.json",
                    "aggregate_sha256": sha256_file(
                        stage / "validation" / "aggregates" / "C0.json"
                    ),
                    "aggregate_digest": c0_aggregate["aggregate_digest"],
                },
            },
            "posthoc_transfer": {
                "protocol_ids": list(
                    experiment["frozen_retrospective_transfer_panel"]["protocol_ids"]
                ),
                "classification": "fixed_posthoc_transfer_comparison",
                "previously_unsealed": True,
                "unseen_or_sealed_claim": False,
                "software_visibility": "blocked_from_all_review_and_synthesis_inputs_until_h25_lock",
                "new_replay_labels": ["C25"],
                "comparison_references": [
                    "source_C0",
                    "autonomous_C25",
                    "human_C25",
                ],
            },
            "policies": {
                "review_interface": "codex_chat",
                "protocol_revision_rounds": 1,
                "maximum_proposed_pack_changes_per_batch": 2,
                "maximum_accepted_pack_changes_per_batch": 1,
                "validation_direct_mutation": False,
                "groundtruth_mutation": False,
            },
        }
        human_manifest = with_digest(payload, "manifest_digest")
        _validate_schema(
            human_manifest,
            HUMAN_MANIFEST_SCHEMA,
            "human-guided experiment manifest",
        )
        write_json_atomic(stage / HUMAN_MANIFEST_PATH, human_manifest, mode=0o444)
        if (stage / "design" / "final_lock.json").exists() or (
            stage / "design" / "transfer_panel_authorization.json"
        ).exists():
            raise CapabilityImprovementError(
                "open human-guided stage contains a source closure marker"
            )
        stage.replace(target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    validated, validated_registry = validate_human_guided_experiment(target)
    return {
        "status": "initialized",
        "experiment_root": target.as_posix(),
        "human_experiment_digest": validated["manifest_digest"],
        "execution_experiment_digest": validated["execution_experiment"][
            "experiment_digest"
        ],
        "protocol_count": validated_registry["protocol_count"],
        "valid_prediction_count": validated_registry["valid_prediction_count"],
        "invalid_prediction_count": validated_registry["invalid_prediction_count"],
        "next_action": "prepare the first protocol review",
    }


def build_human_training_registry(
    *,
    source_experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Pin the 25 original C0 predictions, traces, scores, sources, and truth."""

    source = source_experiment_root.expanduser().resolve()
    validate_digest(experiment_manifest, "experiment_digest")
    entries: list[dict[str, Any]] = []
    global_position = 0
    for batch in experiment_manifest["batches"]:
        batch_id = str(batch["batch_id"])
        packet = load_and_validate(
            source / "rounds" / batch_id / ACTIVE_BRANCH / "packet.json",
            schema_filename="batch_packet.schema.json",
            digest_field="packet_digest",
            label=f"source {batch_id} packet",
        )
        packet_artifacts = list(packet["artifacts"])
        for batch_position, protocol_id in enumerate(batch["protocol_ids"], start=1):
            global_position += 1
            values = [
                item for item in packet_artifacts if item["protocol_id"] == protocol_id
            ]
            if batch["phase"] == "prospective":
                c0_reward = _one_packet_artifact(values, "c0_verifier_reward")
                trial_root = (
                    Path(c0_reward["path"]).expanduser().resolve().parent.parent
                )
            else:
                result_ref = _one_packet_artifact(values, "trial_result")
                trial_root = Path(result_ref["path"]).expanduser().resolve().parent
            fixed_paths = {
                "trial_result": trial_root / "result.json",
                "t2_prediction": trial_root
                / "artifacts"
                / "logs"
                / "artifacts"
                / "t2_prediction.json",
                "t3_prediction": trial_root
                / "artifacts"
                / "logs"
                / "artifacts"
                / "t3_prediction.json",
                "trajectory": trial_root / "agent" / "trajectory.json",
                "verifier_reward": trial_root / "verifier" / "reward.json",
                "verifier_details": trial_root / "verifier" / "details.json",
                "verifier_error_analysis": trial_root
                / "verifier"
                / "error_analysis.json",
            }
            for role, path in list(fixed_paths.items()):
                fixed_paths[role] = _resolve_trial_artifact(path, trial_root, role)

            if batch["phase"] == "prospective":
                expected_reward = _one_packet_artifact(values, "c0_verifier_reward")
                expected_analysis = _one_packet_artifact(
                    values, "c0_verifier_error_analysis"
                )
            else:
                expected_reward = _one_packet_artifact(values, "verifier_reward")
                expected_analysis = _one_packet_artifact(
                    values, "verifier_error_analysis"
                )
            _assert_pinned_file(fixed_paths["verifier_reward"], expected_reward)
            _assert_pinned_file(
                fixed_paths["verifier_error_analysis"], expected_analysis
            )

            artifacts: list[dict[str, Any]] = []
            for item in values:
                if item["role"] == "target_source":
                    _assert_pinned_file(Path(item["path"]), item)
                    artifacts.append(_registry_artifact("target_source", item["path"]))
            if batch["phase"] == "retrospective":
                _assert_pinned_file(
                    fixed_paths["trial_result"],
                    _one_packet_artifact(values, "trial_result"),
                )
                _assert_pinned_file(
                    fixed_paths["trajectory"],
                    _one_packet_artifact(values, "trajectory"),
                )
                _assert_pinned_file(
                    fixed_paths["verifier_details"],
                    _one_packet_artifact(values, "verifier_details"),
                )
                for prediction_role in ("t2_prediction", "t3_prediction"):
                    prediction_name = fixed_paths[prediction_role].name
                    matches = [
                        item
                        for item in values
                        if item["role"] == "prediction"
                        and Path(item["path"]).name == prediction_name
                    ]
                    if len(matches) != 1:
                        raise CapabilityImprovementError(
                            f"cannot pin {prediction_role} for {protocol_id}"
                        )
                    _assert_pinned_file(fixed_paths[prediction_role], matches[0])
            for role, path in fixed_paths.items():
                artifacts.append(_registry_artifact(role, path))
            groundtruth = [
                item for item in values if item["role"] == "approved_groundtruth"
            ]
            observed_gt: set[str] = set()
            for item in groundtruth:
                filename = Path(item["path"]).name
                try:
                    role = _GROUNDTRUTH_ROLES[filename]
                except KeyError as error:
                    raise CapabilityImprovementError(
                        f"unexpected ground-truth file for {protocol_id}: {filename}"
                    ) from error
                _assert_pinned_file(Path(item["path"]), item)
                artifacts.append(_registry_artifact(role, item["path"]))
                observed_gt.add(role)
            if observed_gt != set(_GROUNDTRUTH_ROLES.values()):
                raise CapabilityImprovementError(
                    f"{protocol_id} does not have all three ground-truth files"
                )
            details = _json(fixed_paths["verifier_details"], "verifier details")
            prediction_valid = details.get("prediction_valid") is True
            entry_payload = {
                "batch_id": batch_id,
                "batch_position": batch_position,
                "global_position": global_position,
                "protocol_id": protocol_id,
                "trial_root": trial_root.as_posix(),
                "trial_name": trial_root.name,
                "prediction_valid": prediction_valid,
                "artifacts": artifacts,
            }
            entries.append(with_digest(entry_payload, "entry_digest"))

    if [item["global_position"] for item in entries] != list(range(1, 26)):
        raise CapabilityImprovementError("human registry is not an ordered 25-item set")
    payload = {
        "schema_version": "libstruct.libgen_human_training_registry.v1",
        "source_experiment_digest": experiment_manifest["experiment_digest"],
        "protocol_count": len(entries),
        "valid_prediction_count": sum(item["prediction_valid"] for item in entries),
        "invalid_prediction_count": sum(
            not item["prediction_valid"] for item in entries
        ),
        "entries": entries,
    }
    registry = with_digest(payload, "registry_digest")
    _validate_schema(registry, HUMAN_REGISTRY_SCHEMA, "human training registry")
    return registry


def refresh_human_protocol_verifier(
    *,
    experiment_root: Path,
    protocol_id: str,
    rescore_dir: Path,
    rescore_summary_path: Path,
    created_at: str,
) -> dict[str, Any]:
    """Repin one undecided protocol to a versioned deterministic rescore.

    The original Harbor artifacts and the prior human overlay remain unchanged
    in superseded history. Completed reviews for other protocols remain bound
    to their immutable manifest ancestor and exact unchanged registry entry.
    """

    root = experiment_root.expanduser().resolve()
    reject_private_output_in_repository(root)
    assert_capability_modification_open(root)
    human, registry = validate_human_guided_experiment(root)
    entry = _registry_entry(registry, protocol_id)
    next_entry = _next_registry_entry(root, registry)
    prepare_target_review = (
        next_entry is not None and next_entry["protocol_id"] == protocol_id
    )
    review_root = _review_root(root, entry)
    if (review_root / "review-record.json").is_file() or any(
        review_root.glob("decision*.json")
    ):
        raise CapabilityImprovementError(
            f"verifier refresh is forbidden after a decision for {protocol_id}"
        )
    later_checkpoints = sorted(
        path.name
        for path in (root / "checkpoints").iterdir()
        if path.is_dir() and path.name != "C0"
    )
    if later_checkpoints:
        raise CapabilityImprovementError(
            "verifier refresh is forbidden after later checkpoints exist: "
            + ", ".join(later_checkpoints)
        )
    if (root / "rounds").exists() and any((root / "rounds").rglob("*")):
        raise CapabilityImprovementError(
            "verifier refresh is forbidden after batch synthesis has started"
        )

    trial_root = Path(str(entry["trial_root"])).expanduser().resolve()
    version_label = f"libgen-{LIBGEN_BENCHMARK_VERSION}"
    expected_rescore = trial_root / "verifier" / "rescore" / version_label
    resolved_rescore = rescore_dir.expanduser().resolve()
    if resolved_rescore != expected_rescore:
        raise CapabilityImprovementError(
            f"rescore directory must be the trial's {version_label} sidecar: "
            f"{expected_rescore}"
        )
    expected_summaries = {
        trial_root.parent / "rescore" / version_label / "summary.json",
        expected_rescore / "summary.json",
    }
    resolved_summary = rescore_summary_path.expanduser().resolve()
    if resolved_summary not in expected_summaries:
        raise CapabilityImprovementError(
            "rescore summary must be either the immutable trial-local or "
            f"legacy job-level {version_label} summary"
        )

    rescored_paths = {
        "verifier_reward": resolved_rescore / "reward.json",
        "verifier_details": resolved_rescore / "details.json",
        "verifier_error_analysis": resolved_rescore / "error_analysis.json",
    }
    for path in (*rescored_paths.values(), resolved_summary):
        if path.is_symlink() or not path.is_file():
            raise CapabilityImprovementError(f"rescored artifact is missing: {path}")
    reward = _json(rescored_paths["verifier_reward"], "rescored verifier reward")
    if set(reward) != set(_METRICS) or any(
        not isinstance(reward[metric], (int, float))
        or isinstance(reward[metric], bool)
        or not 0.0 <= float(reward[metric]) <= 1.0
        for metric in _METRICS
    ):
        raise CapabilityImprovementError("rescored verifier reward is not canonical")
    details = _json(rescored_paths["verifier_details"], "rescored verifier details")
    if (
        details.get("protocol_id") != protocol_id
        or details.get("benchmark_version") != LIBGEN_BENCHMARK_VERSION
        or (
            details.get("prediction_valid") is True
            and (details.get("scoring") or {}).get("benchmark_version")
            != LIBGEN_BENCHMARK_VERSION
        )
    ):
        raise CapabilityImprovementError(
            "rescored verifier details do not match the protocol and benchmark version"
        )
    analysis = _json(
        rescored_paths["verifier_error_analysis"],
        "rescored verifier error analysis",
    )
    _validate_error_analysis(analysis, "rescored verifier error analysis")
    if (
        analysis.get("protocol_id") != protocol_id
        or analysis.get("trial_id") != entry["trial_name"]
    ):
        raise CapabilityImprovementError(
            "rescored error analysis does not match the frozen trial"
        )
    summary = _json(resolved_summary, "versioned rescore summary")
    matching_summary = [
        item
        for item in summary.get("trials", [])
        if item.get("trial_name") == entry["trial_name"]
        and item.get("protocol_id") == protocol_id
    ]
    if (
        summary.get("benchmark_version") != LIBGEN_BENCHMARK_VERSION
        or len(matching_summary) != 1
        or matching_summary[0].get("output_dir") != resolved_rescore.as_posix()
        or matching_summary[0].get("rescored_metrics") != reward
        or (details.get("prediction_valid") is True)
        != bool(matching_summary[0].get("prediction_valid"))
    ):
        raise CapabilityImprovementError(
            "rescore summary does not bind the expected sidecar outputs"
        )

    current_verifier_paths = {
        role: _entry_artifact_path(entry, role) for role in rescored_paths
    }
    if current_verifier_paths == rescored_paths:
        return {
            "status": "already_refreshed",
            "protocol_id": protocol_id,
            "human_experiment_digest": human["manifest_digest"],
            "registry_digest": registry["registry_digest"],
            "next_action": "prepare or compile the refreshed protocol review",
        }

    new_entry, new_registry, replacement_artifacts = _repin_registry_verifier_artifacts(
        registry=registry,
        entry=entry,
        rescored_paths=rescored_paths,
        prediction_valid=details.get("prediction_valid") is True,
    )
    _validate_schema(new_registry, HUMAN_REGISTRY_SCHEMA, "refreshed human registry")

    timestamp = _timestamp(created_at)
    refresh_id = f"{protocol_id}-{version_label}-{human['manifest_digest'][:12]}"
    archive_relative = (
        Path("human-history/superseded/human-verifier-refresh") / refresh_id
    )
    if (root / archive_relative).exists():
        raise CapabilityImprovementError(
            f"verifier-refresh archive already exists: {root / archive_relative}"
        )

    build_root = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.verifier-refresh-", dir=root.parent)
    )
    build_root.rmdir()
    backup = root.parent / f".{root.name}.verifier-refresh-backup"
    if backup.exists():
        raise CapabilityImprovementError(
            f"stale verifier-refresh backup exists: {backup}"
        )
    try:
        _clone_tree(root, build_root)
        archive = build_root / archive_relative
        archived_manifest_path = archive / "design/human_guided_experiment.json"
        archived_registry_path = archive / "design/human_training_registry.json"
        _link_or_copy(
            build_root / HUMAN_MANIFEST_PATH,
            archived_manifest_path,
        )
        _link_or_copy(
            build_root / HUMAN_REGISTRY_PATH,
            archived_registry_path,
        )
        review_root = _review_root(build_root, entry)
        archived_review = None
        if review_root.is_dir():
            archived_review_root = archive / "human-review"
            _clone_tree(review_root, archived_review_root)
            archived_review = _tree_reference(
                archived_review_root,
                relative_to=build_root,
            )
            shutil.rmtree(review_root)

        registry_path = build_root / HUMAN_REGISTRY_PATH
        write_json_atomic(registry_path, new_registry, mode=0o444)
        refresh_payload = {
            "schema_version": "libstruct.libgen_human_verifier_refresh.v1",
            "refresh_id": refresh_id,
            "recorded_at": timestamp,
            "reason": "canonical_evaluator_defect_corrected_by_versioned_sidecar_rescore",
            "protocol_id": protocol_id,
            "trial_name": entry["trial_name"],
            "benchmark_version": LIBGEN_BENCHMARK_VERSION,
            "prior_human_manifest": _document_ref(
                archived_manifest_path,
                relative_to=build_root,
                digest=human["manifest_digest"],
            ),
            "prior_registry": _document_ref(
                archived_registry_path,
                relative_to=build_root,
                digest=registry["registry_digest"],
            ),
            "prior_entry_digest": entry["entry_digest"],
            "original_verifier_artifacts": [
                copy.deepcopy(_entry_artifact(entry, role)) for role in rescored_paths
            ],
            "rescored_verifier_artifacts": [
                copy.deepcopy(replacement_artifacts[role]) for role in rescored_paths
            ],
            "rescore_summary": _external_artifact(resolved_summary),
            "archived_review": archived_review,
            "new_registry_digest": new_registry["registry_digest"],
            "new_entry_digest": new_entry["entry_digest"],
        }
        refresh = with_digest(refresh_payload, "refresh_digest")
        _validate_schema(
            refresh,
            HUMAN_VERIFIER_REFRESH_SCHEMA,
            "human verifier refresh",
        )
        refresh_path = archive / "refresh.json"
        write_json_atomic(refresh_path, refresh, mode=0o444)
        freeze_tree(archive)

        human_payload = copy.deepcopy(dict(human))
        human_payload.pop("manifest_digest")
        human_payload["baseline_registry"] = _document_ref(
            registry_path,
            relative_to=build_root,
            digest=new_registry["registry_digest"],
        )
        human_payload.setdefault("verifier_refreshes", []).append(
            _document_ref(
                refresh_path,
                relative_to=build_root,
                digest=refresh["refresh_digest"],
            )
        )
        refreshed_human = with_digest(human_payload, "manifest_digest")
        _validate_schema(
            refreshed_human,
            HUMAN_MANIFEST_SCHEMA,
            "refreshed human-guided experiment manifest",
        )
        write_json_atomic(
            build_root / HUMAN_MANIFEST_PATH,
            refreshed_human,
            mode=0o444,
        )
        validate_human_guided_experiment(build_root)
        prepared = (
            prepare_next_human_protocol_review(
                experiment_root=build_root,
                created_at=timestamp,
            )
            if prepare_target_review
            else None
        )

        root.replace(backup)
        try:
            build_root.replace(root)
        except BaseException:
            backup.replace(root)
            raise
        try:
            thaw_tree(backup)
            shutil.rmtree(backup)
        except OSError as error:
            raise CapabilityImprovementError(
                "verifier refresh succeeded but backup cleanup failed: "
                f"{backup}: {error}"
            ) from error
    except BaseException:
        if build_root.exists():
            try:
                thaw_tree(build_root)
            except OSError:
                pass
            shutil.rmtree(build_root, ignore_errors=True)
        raise

    final_human, final_registry = validate_human_guided_experiment(root)
    final_entry = _registry_entry(final_registry, protocol_id)
    base_result = {
        "status": "verifier_refreshed",
        "protocol_id": protocol_id,
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "prior_human_experiment_digest": human["manifest_digest"],
        "human_experiment_digest": final_human["manifest_digest"],
        "prior_registry_digest": registry["registry_digest"],
        "registry_digest": final_registry["registry_digest"],
        "refresh_path": (root / archive_relative / "refresh.json").as_posix(),
        "archived_review_path": (
            (root / archive_relative / "human-review").as_posix()
            if archived_review is not None
            else None
        ),
    }
    if not prepare_target_review:
        return {
            **base_result,
            "comparison_path": None,
            "comparison_digest": None,
            "metrics": {metric: float(reward[metric]) for metric in _METRICS},
            "substantive_observation_count": sum(
                item.get("substantive") is True
                for item in analysis["observations"]
            ),
            "next_action": "refresh the remaining undecided protocols before review",
            "prepared": None,
        }
    final_review_root = _review_root(root, final_entry)
    comparison_path = final_review_root / "comparison.json"
    comparison = _load_human_document(
        comparison_path,
        schema=HUMAN_COMPARISON_SCHEMA,
        digest_field="comparison_digest",
        label="refreshed human protocol comparison",
    )
    prepared = {
        **prepared,
        "comparison_path": comparison_path.as_posix(),
        "review_path": (final_review_root / "review.md").as_posix(),
        "draft_template_path": (
            final_review_root / "proposal-draft.template.json"
        ).as_posix(),
    }
    return {
        **base_result,
        "comparison_path": comparison_path.as_posix(),
        "comparison_digest": comparison["comparison_digest"],
        "metrics": comparison["metrics"],
        "substantive_observation_count": sum(
            item.get("substantive") is True for item in comparison["observations"]
        ),
        "next_action": "author and compile a fresh proposal, then restart section 1",
        "prepared": prepared,
    }


def validate_human_guided_experiment(
    experiment_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = experiment_root.expanduser().resolve()
    human = _load_human_document(
        root / HUMAN_MANIFEST_PATH,
        schema=HUMAN_MANIFEST_SCHEMA,
        digest_field="manifest_digest",
        label="human-guided experiment manifest",
    )
    execution = validate_experiment_manifest(
        root / human["execution_experiment"]["manifest_path"],
        experiment_root=root,
    )
    if (
        execution["experiment_digest"]
        != human["execution_experiment"]["experiment_digest"]
    ):
        raise CapabilityImprovementError(
            "human overlay references another execution experiment"
        )
    if (
        sha256_file(root / "design" / "experiment_manifest.json")
        != human["execution_experiment"]["manifest_sha256"]
    ):
        raise CapabilityImprovementError("execution experiment manifest changed")
    checkpoint, _, pack = validate_checkpoint_runtime(root / "checkpoints" / "C0")
    expected_c0 = {
        "c0_checkpoint_digest": checkpoint["checkpoint_digest"],
        "c0_checkpoint_sha256": sha256_file(
            root / "checkpoints" / "C0" / "checkpoint.json"
        ),
        "c0_pack_digest": pack["pack_digest"],
    }
    for key, value in expected_c0.items():
        if human["execution_experiment"][key] != value:
            raise CapabilityImprovementError(f"human overlay has stale {key}")
    registry_ref = human["baseline_registry"]
    registry_path = root / safe_relative_path(registry_ref["path"])
    registry = _load_human_document(
        registry_path,
        schema=HUMAN_REGISTRY_SCHEMA,
        digest_field="registry_digest",
        label="human training registry",
    )
    _validate_ref(registry_path, registry_ref, registry["registry_digest"])
    if registry["source_experiment_digest"] != execution["experiment_digest"]:
        raise CapabilityImprovementError("human registry belongs to another experiment")
    _validate_registry_order(registry, execution)
    _validate_human_verifier_refreshes(
        root=root,
        human=human,
        registry=registry,
    )
    source = Path(human["source_experiment"]["root"]).expanduser().resolve()
    source_checks = {
        source / "design" / "experiment_manifest.json": human["source_experiment"][
            "manifest_sha256"
        ],
        source / "design" / "final_lock.json": human["source_experiment"][
            "final_lock_sha256"
        ],
        source / "design" / "transfer_panel_authorization.json": human[
            "source_experiment"
        ]["transfer_authorization_sha256"],
    }
    for path, expected_sha in source_checks.items():
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise CapabilityImprovementError(
                f"source experiment provenance changed: {path}"
            )
    c0_ref = human["validation"]["c0_reuse"]
    c0_path = root / c0_ref["aggregate_path"]
    c0 = validate_validation_aggregate(
        c0_path,
        experiment_digest=execution["experiment_digest"],
        expected_checkpoint_label="C0",
        expected_pack_digest=pack["pack_digest"],
        expected_checkpoint_root=root / "checkpoints" / "C0",
    )
    if (
        sha256_file(c0_path) != c0_ref["aggregate_sha256"]
        or c0["aggregate_digest"] != c0_ref["aggregate_digest"]
    ):
        raise CapabilityImprovementError("reused C0 aggregate changed")
    return human, registry


def prepare_next_human_protocol_review(
    *,
    experiment_root: Path,
    protocol_id: str | None = None,
    created_at: str,
) -> dict[str, Any]:
    """Materialize the next frozen C0 comparison for review in Codex chat."""

    root = experiment_root.expanduser().resolve()
    assert_capability_modification_open(root)
    human, registry = validate_human_guided_experiment(root)
    next_entry = _next_registry_entry(root, registry)
    if next_entry is None:
        return {
            "status": "all_protocol_reviews_complete",
            "reviewed_protocol_count": 25,
            "next_action": "finish or validate the H25 checkpoint",
        }
    entry = (
        _registry_entry(registry, protocol_id)
        if protocol_id is not None
        else next_entry
    )
    if _review_is_complete(root, entry):
        raise CapabilityImprovementError(
            f"human protocol review is already complete: {entry['protocol_id']}"
        )
    if entry["batch_id"] != next_entry["batch_id"]:
        raise CapabilityImprovementError(
            "pre-staging is allowed only within the active five-protocol batch"
        )
    parent_label = checkpoint_before_batch(entry["batch_id"])
    parent_root = root / "checkpoints" / parent_label
    if not parent_root.is_dir():
        raise CapabilityImprovementError(
            f"{parent_label} is missing; finish {entry['batch_id']} prerequisites "
            "before reviewing another protocol"
        )
    checkpoint, _, pack = validate_checkpoint_runtime(parent_root)
    validation_policy = validate_referenced_validation_access_policy(
        experiment_root=root,
        experiment_manifest=validate_experiment_manifest(
            root / "design" / "experiment_manifest.json",
            experiment_root=root,
        ),
    )
    validation_aggregate = validate_required_validation_aggregate(
        experiment_root=root,
        experiment_digest=checkpoint["experiment_digest"],
        validation_access_policy=validation_policy,
        batch_id=entry["batch_id"],
        expected_pack_digest=pack["pack_digest"],
    )
    c0_aggregate = validate_validation_aggregate(
        root / "validation" / "aggregates" / "C0.json",
        experiment_digest=checkpoint["experiment_digest"],
        validation_access_policy=validation_policy,
        expected_checkpoint_label="C0",
        expected_checkpoint_root=root / "checkpoints" / "C0",
    )
    guidance_path = root / "human-guidance" / f"{parent_label}.json"
    guidance_ref = None
    if guidance_path.is_file():
        guidance = _load_human_document(
            guidance_path,
            schema=HUMAN_GUIDANCE_SCHEMA,
            digest_field="guidance_digest",
            label="human validation guidance",
        )
        if (
            guidance["checkpoint_label"] != parent_label
            or guidance["aggregate_digest"] != validation_aggregate["aggregate_digest"]
        ):
            raise CapabilityImprovementError(
                f"human validation guidance is stale: {guidance_path}"
            )
        guidance_ref = _document_ref(
            guidance_path,
            relative_to=root,
            digest=guidance["guidance_digest"],
        )
    review_root = _review_root(root, entry)
    comparison_path = review_root / "comparison.json"
    if comparison_path.is_file():
        comparison = _load_human_document(
            comparison_path,
            schema=HUMAN_COMPARISON_SCHEMA,
            digest_field="comparison_digest",
            label="human protocol comparison",
        )
        if comparison["baseline_entry_digest"] != entry["entry_digest"]:
            raise CapabilityImprovementError(
                f"existing comparison is stale: {comparison_path}"
            )
        return _prepared_review_result(review_root, comparison, reused=True)
    if review_root.exists():
        raise CapabilityImprovementError(
            f"review directory exists without a comparison: {review_root}"
        )

    for artifact in entry["artifacts"]:
        _validate_registry_artifact(artifact)
    reward = _json(
        _entry_artifact_path(entry, "verifier_reward"),
        "frozen C0 reward",
    )
    details = _json(
        _entry_artifact_path(entry, "verifier_details"),
        "frozen C0 verifier details",
    )
    raw_analysis = _json(
        _entry_artifact_path(entry, "verifier_error_analysis"),
        "frozen C0 error analysis",
    )
    _validate_schema(
        raw_analysis,
        "../analysis/libgen_error_analysis.schema.json",
        "frozen C0 error analysis",
        schema_root=improvement_schema_root(),
    )
    prediction_valid = bool(entry["prediction_valid"])
    if prediction_valid != (details.get("prediction_valid") is True):
        raise CapabilityImprovementError(
            f"registry prediction-valid flag changed for {entry['protocol_id']}"
        )
    metrics = (
        {metric: float(reward[metric]) for metric in _METRICS}
        if prediction_valid
        else None
    )
    review_id = (
        f"{entry['batch_id']}:{entry['global_position']:02d}:{entry['protocol_id']}"
    )
    payload = {
        "schema_version": "libstruct.libgen_human_protocol_comparison.v1",
        "review_id": review_id,
        "human_experiment_digest": human["manifest_digest"],
        "execution_experiment_digest": checkpoint["experiment_digest"],
        "batch_id": entry["batch_id"],
        "batch_position": entry["batch_position"],
        "global_position": entry["global_position"],
        "protocol_id": entry["protocol_id"],
        "baseline_entry_digest": entry["entry_digest"],
        "parent_checkpoint": parent_label,
        "display_parent_checkpoint": human["checkpoint_aliases"][parent_label],
        "parent_pack_digest": pack["pack_digest"],
        "validation_context": {
            "checkpoint_label": parent_label,
            "display_checkpoint_label": human["checkpoint_aliases"][parent_label],
            "aggregate_digest": validation_aggregate["aggregate_digest"],
            "aggregate_sha256": sha256_file(
                root / "validation" / "aggregates" / f"{parent_label}.json"
            ),
            "macro_means": {
                metric: validation_aggregate["macro_means"][metric]
                for metric in _METRICS
            },
            "delta_from_c0": {
                metric: validation_aggregate["macro_means"][metric]
                - c0_aggregate["macro_means"][metric]
                for metric in _METRICS
            },
            "human_guidance": guidance_ref,
            "feedback_scope": "aggregate_guidance_only_not_scientific_evidence_or_direct_pack_mutation",
        },
        "prediction_valid": prediction_valid,
        "artifacts": copy.deepcopy(entry["artifacts"]),
        "metrics": metrics,
        "scoring": details.get("scoring") if prediction_valid else None,
        "observations": copy.deepcopy(raw_analysis["observations"]),
        "source_recoverability_policy": "score_only_explicit_or_derivable_agent_visible_claims_optional_and_nonrecoverable_claims_neutral",
        "created_at": _timestamp(created_at),
    }
    comparison = with_digest(payload, "comparison_digest")
    _validate_schema(
        comparison,
        HUMAN_COMPARISON_SCHEMA,
        "human protocol comparison",
    )
    review_root.mkdir(parents=True)
    write_json_atomic(comparison_path, comparison, mode=0o444)
    review_text = _render_protocol_review(
        comparison=comparison,
        entry=entry,
        parent_pack_root=parent_root / "pack",
    )
    (review_root / "review.md").write_text(review_text, encoding="utf-8")
    (review_root / "review.md").chmod(0o444)
    template = {
        "schema_version": "libstruct.libgen_human_protocol_review_proposal_draft.v1",
        "protocol_id": entry["protocol_id"],
        "comparison_digest": comparison["comparison_digest"],
        "revision_round": 0,
        "summary": "Replace with a concise evidence-bound assessment.",
        "successful_self_correction": "unclear",
        "root_findings": [],
        "no_change_rationale": "Replace after classifying every substantive discrepancy.",
    }
    write_json_atomic(review_root / "proposal-draft.template.json", template)
    return _prepared_review_result(review_root, comparison, reused=False)


def compile_human_protocol_review_proposal(
    *,
    experiment_root: Path,
    draft_path: Path,
    proposer_model: str,
    proposer_version: str,
    reasoning_effort: str,
    created_at: str,
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    """Bind a Codex-authored draft to one exact comparison and pack."""

    root = experiment_root.expanduser().resolve()
    assert_capability_modification_open(root)
    human, registry = validate_human_guided_experiment(root)
    draft_resolved = draft_path.expanduser().resolve()
    draft = _json(draft_resolved, "human protocol proposal draft")
    _validate_schema(draft, HUMAN_DRAFT_SCHEMA, "human protocol proposal draft")
    entry = _registry_entry(registry, str(draft["protocol_id"]))
    review_root = _review_root(root, entry)
    comparison_path = review_root / "comparison.json"
    comparison = _load_human_document(
        comparison_path,
        schema=HUMAN_COMPARISON_SCHEMA,
        digest_field="comparison_digest",
        label="human protocol comparison",
    )
    if draft["comparison_digest"] != comparison["comparison_digest"]:
        raise CapabilityImprovementError("proposal draft targets a stale comparison")
    revision_round = int(draft["revision_round"])
    prior_proposal_digest: str | None = None
    revision_decision_digest: str | None = None
    if revision_round == 0:
        output_path = review_root / "proposal.json"
    else:
        output_path = review_root / "proposal-r1.json"
        prior = _load_human_document(
            review_root / "proposal.json",
            schema=HUMAN_PROPOSAL_SCHEMA,
            digest_field="proposal_digest",
            label="initial human protocol proposal",
        )
        decision = _load_human_document(
            review_root / "decision.json",
            schema=HUMAN_DECISION_SCHEMA,
            digest_field="decision_digest",
            label="initial human protocol decision",
        )
        if decision["disposition"] != "revision_requested":
            raise CapabilityImprovementError(
                "a revision requires an explicit revision-request decision"
            )
        prior_proposal_digest = prior["proposal_digest"]
        revision_decision_digest = decision["decision_digest"]
    if output_path.exists():
        existing = _load_human_document(
            output_path,
            schema=HUMAN_PROPOSAL_SCHEMA,
            digest_field="proposal_digest",
            label="compiled human protocol proposal",
        )
        if existing["draft_sha256"] != sha256_file(draft_resolved):
            raise CapabilityImprovementError(
                f"compiled proposal already exists for different draft bytes: {output_path}"
            )
        return existing

    _validate_protocol_findings(
        draft=draft,
        comparison=comparison,
        human_manifest=human,
        parent_pack_root=root
        / "checkpoints"
        / comparison["parent_checkpoint"]
        / "pack",
    )
    transcript_sha = None
    if transcript_path is not None:
        transcript = transcript_path.expanduser().resolve()
        if not transcript.is_file():
            raise CapabilityImprovementError(f"transcript is missing: {transcript}")
        transcript_sha = sha256_file(transcript)
    payload = {
        "schema_version": "libstruct.libgen_human_protocol_review_proposal.v1",
        "proposal_id": f"{comparison['review_id']}:r{revision_round}",
        "human_experiment_digest": human["manifest_digest"],
        "review_id": comparison["review_id"],
        "protocol_id": comparison["protocol_id"],
        "comparison_digest": comparison["comparison_digest"],
        "comparison_sha256": sha256_file(comparison_path),
        "draft_sha256": sha256_file(draft_resolved),
        "baseline_entry_digest": comparison["baseline_entry_digest"],
        "parent_pack_digest": comparison["parent_pack_digest"],
        "revision_round": revision_round,
        "revision_of_proposal_digest": prior_proposal_digest,
        "revision_request_decision_digest": revision_decision_digest,
        "summary": draft["summary"],
        "successful_self_correction": draft["successful_self_correction"],
        "root_findings": copy.deepcopy(draft["root_findings"]),
        "no_change_rationale": draft["no_change_rationale"],
        "proposer": {
            "agent": "codex",
            "interface": "codex_chat",
            "model": proposer_model,
            "version": proposer_version,
            "reasoning_effort": reasoning_effort,
            "transcript_sha256": transcript_sha,
        },
        "created_at": _timestamp(created_at),
    }
    proposal = with_digest(payload, "proposal_digest")
    _validate_schema(proposal, HUMAN_PROPOSAL_SCHEMA, "human protocol proposal")
    write_json_atomic(output_path, proposal, mode=0o444)
    return proposal


def record_human_protocol_review_decision(
    *,
    experiment_root: Path,
    proposal_path: Path,
    reviewer_id: str,
    disposition: str,
    rationale: str,
    started_at: str,
    completed_at: str,
    revision_instruction: str | None = None,
) -> dict[str, Any]:
    """Record explicit approve/comment/reject and finalize accepted attribution."""

    root = experiment_root.expanduser().resolve()
    assert_capability_modification_open(root)
    human, registry = validate_human_guided_experiment(root)
    proposal_resolved = proposal_path.expanduser().resolve()
    proposal = _load_human_document(
        proposal_resolved,
        schema=HUMAN_PROPOSAL_SCHEMA,
        digest_field="proposal_digest",
        label="human protocol proposal",
    )
    entry = _registry_entry(registry, proposal["protocol_id"])
    review_root = _review_root(root, entry)
    expected_proposal = (
        review_root / "proposal.json"
        if proposal["revision_round"] == 0
        else review_root / "proposal-r1.json"
    )
    if proposal_resolved != expected_proposal:
        raise CapabilityImprovementError(
            f"proposal must use its canonical review path: {expected_proposal}"
        )
    allowed = {"approve", "reject", "revision_requested", "unresolved"}
    if disposition not in allowed:
        raise CapabilityImprovementError(
            "protocol review disposition must be approve, reject, "
            "revision_requested, or unresolved"
        )
    if disposition == "revision_requested":
        if proposal["revision_round"] != 0:
            raise CapabilityImprovementError(
                "only one protocol-review revision is allowed"
            )
        if not revision_instruction or not revision_instruction.strip():
            raise CapabilityImprovementError(
                "revision_requested requires a non-empty revision instruction"
            )
    elif revision_instruction is not None:
        raise CapabilityImprovementError(
            "revision instruction is allowed only for revision_requested"
        )
    if not reviewer_id.strip() or not rationale.strip():
        raise CapabilityImprovementError("reviewer ID and rationale must be non-empty")
    started = _timestamp(started_at)
    completed = _timestamp(completed_at)
    if _parse_time(completed) < _parse_time(started):
        raise CapabilityImprovementError("protocol review completed before it started")
    decision_path = (
        review_root / "decision.json"
        if proposal["revision_round"] == 0
        else review_root / "decision-r1.json"
    )
    if decision_path.exists():
        existing = _load_human_document(
            decision_path,
            schema=HUMAN_DECISION_SCHEMA,
            digest_field="decision_digest",
            label="human protocol decision",
        )
        if existing["proposal_digest"] != proposal["proposal_digest"]:
            raise CapabilityImprovementError(
                "existing decision targets other proposal bytes"
            )
        if existing["disposition"] == "revision_requested":
            return {
                **existing,
                "status": "revision_requested",
                "next_action": "author and compile proposal-r1.json",
            }
        record_path = review_root / "review-record.json"
        if record_path.is_file():
            record = _load_human_document(
                record_path,
                schema=HUMAN_RECORD_SCHEMA,
                digest_field="record_digest",
                label="human protocol review record",
            )
        else:
            record = _finalize_protocol_review(
                root=root,
                human=human,
                entry=entry,
                proposal_path=proposal_resolved,
                proposal=proposal,
                decision_path=decision_path,
                decision=existing,
            )
        return {
            **existing,
            "status": "review_complete",
            "review_record_path": record_path.as_posix(),
            "review_record_digest": record["record_digest"],
            "next_action": _next_action_after_review(root, entry),
        }
    payload = {
        "schema_version": "libstruct.libgen_human_protocol_review_decision.v1",
        "decision_id": f"{proposal['proposal_id']}:decision",
        "human_experiment_digest": human["manifest_digest"],
        "review_id": proposal["review_id"],
        "protocol_id": proposal["protocol_id"],
        "proposal_digest": proposal["proposal_digest"],
        "proposal_sha256": sha256_file(proposal_resolved),
        "revision_round": proposal["revision_round"],
        "reviewer_id": reviewer_id.strip(),
        "interface": "codex_chat",
        "disposition": disposition,
        "rationale": rationale.strip(),
        "revision_instruction": (
            revision_instruction.strip() if revision_instruction is not None else None
        ),
        "started_at": started,
        "completed_at": completed,
    }
    decision = with_digest(payload, "decision_digest")
    _validate_schema(decision, HUMAN_DECISION_SCHEMA, "human protocol decision")
    write_json_atomic(decision_path, decision, mode=0o444)
    if disposition == "revision_requested":
        return {
            **decision,
            "status": "revision_requested",
            "next_action": "author and compile proposal-r1.json",
        }
    record = _finalize_protocol_review(
        root=root,
        human=human,
        entry=entry,
        proposal_path=proposal_resolved,
        proposal=proposal,
        decision_path=decision_path,
        decision=decision,
    )
    return {
        **decision,
        "status": "review_complete",
        "review_record_path": (review_root / "review-record.json").as_posix(),
        "review_record_digest": record["record_digest"],
        "next_action": _next_action_after_review(root, entry),
    }


def build_human_guided_batch_packet(
    *,
    experiment_root: Path,
    batch_id: str,
) -> tuple[Path, dict[str, Any]]:
    """Build the normal five-protocol learning packet from approved reviews."""

    root = experiment_root.expanduser().resolve()
    assert_capability_modification_open(root)
    human, registry = validate_human_guided_experiment(root)
    experiment = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    batch = _manifest_batch(experiment, batch_id)
    entries = [item for item in registry["entries"] if item["batch_id"] == batch_id]
    if [item["protocol_id"] for item in entries] != list(batch["protocol_ids"]):
        raise CapabilityImprovementError(f"registry order differs for {batch_id}")
    records: dict[str, dict[str, Any]] = {}
    for entry in entries:
        record_path = _review_root(root, entry) / "review-record.json"
        if not record_path.is_file():
            raise CapabilityImprovementError(
                f"review is not final for {entry['protocol_id']}: {record_path}"
            )
        records[entry["protocol_id"]] = _validate_review_record_in_active_lineage(
            root=root,
            human=human,
            entry=entry,
            deep=True,
        )

    parent_label = checkpoint_before_batch(batch_id)
    checkpoint, _, pack = validate_checkpoint_runtime(
        root / "checkpoints" / parent_label
    )
    access_policy_path = (
        root
        / experiment["frozen_retrospective_transfer_panel"]["access_policy"]["path"]
    )
    access_policy = validate_transfer_access_policy(access_policy_path)
    artifacts: list[dict[str, Any]] = []
    terminality: list[dict[str, Any]] = []
    for entry in entries:
        protocol_id = entry["protocol_id"]
        record_path = _review_root(root, entry) / "review-record.json"
        record = records[protocol_id]
        adjudicated_path = root / safe_relative_path(
            record["adjudicated_error_analysis"]["path"]
        )
        _validate_ref(
            adjudicated_path,
            record["adjudicated_error_analysis"],
            record["adjudicated_error_analysis"]["digest"],
        )
        for artifact in entry["artifacts"]:
            role = artifact["role"]
            if role == "verifier_error_analysis":
                continue
            if role == "t2_prediction" or role == "t3_prediction":
                packet_role = "prediction"
            elif role.startswith("groundtruth_"):
                packet_role = "approved_groundtruth"
            else:
                packet_role = role
            artifacts.append(
                _packet_artifact(
                    protocol_id=protocol_id,
                    role=packet_role,
                    path=Path(artifact["path"]),
                    expected_sha=artifact["sha256"],
                )
            )
        artifacts.append(
            _packet_artifact(
                protocol_id=protocol_id,
                role="verifier_error_analysis",
                path=adjudicated_path,
                expected_sha=record["adjudicated_error_analysis"]["sha256"],
            )
        )
        artifacts.append(
            _packet_artifact(
                protocol_id=protocol_id,
                role="discrepancy_ledger",
                path=record_path,
                expected_sha=sha256_file(record_path),
            )
        )
        result_sha = _entry_artifact(entry, "trial_result")["sha256"]
        status = "completed" if entry["prediction_valid"] else "invalid_output"
        terminality.append(
            {
                "protocol_id": protocol_id,
                "branch": ACTIVE_BRANCH,
                "status": status,
                "attempt_count": 1,
                "frozen_output_sha256": result_sha,
            }
        )
        if batch["phase"] == "prospective":
            reward = _entry_artifact(entry, "verifier_reward")
            raw_analysis = _entry_artifact(entry, "verifier_error_analysis")
            artifacts.extend(
                [
                    _packet_artifact(
                        protocol_id=protocol_id,
                        role="c0_verifier_reward",
                        path=Path(reward["path"]),
                        expected_sha=reward["sha256"],
                    ),
                    _packet_artifact(
                        protocol_id=protocol_id,
                        role="c0_verifier_error_analysis",
                        path=Path(raw_analysis["path"]),
                        expected_sha=raw_analysis["sha256"],
                    ),
                ]
            )
            terminality.append(
                {
                    "protocol_id": protocol_id,
                    "branch": "C0",
                    "status": status,
                    "attempt_count": 1,
                    "frozen_output_sha256": result_sha,
                }
            )
    packet = build_batch_packet(
        experiment_manifest=experiment,
        branch=ACTIVE_BRANCH,
        batch_id=batch_id,
        parent_pack_digest=pack["pack_digest"],
        reveal_state="revealed",
        artifacts=artifacts,
        trial_terminality=terminality,
        transfer_access_policy=access_policy,
    )
    packet_path = root / "rounds" / batch_id / ACTIVE_BRANCH / "packet.json"
    if packet_path.is_file():
        existing = load_and_validate(
            packet_path,
            schema_filename="batch_packet.schema.json",
            digest_field="packet_digest",
            label="human-guided batch packet",
        )
        if existing != packet:
            raise CapabilityImprovementError(
                f"existing human-guided packet differs from reviewed inputs: {packet_path}"
            )
        return packet_path, existing
    write_json_atomic(packet_path, packet, mode=0o444)
    return packet_path, packet


def run_human_guided_batch_synthesis(
    *,
    experiment_root: Path,
    batch_id: str,
    workspace_root: Path | None = None,
    codex_executable: str = "codex",
    idle_timeout_seconds: float = 300.0,
    hard_timeout_seconds: float = 7200.0,
    agent_runner: Any = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Synthesize at most two normal pack changes after five final reviews."""

    root = experiment_root.expanduser().resolve()
    packet_path, packet = build_human_guided_batch_packet(
        experiment_root=root,
        batch_id=batch_id,
    )
    from .local_learning import run_local_learning

    result = run_local_learning(
        experiment_root=root,
        batch_id=batch_id,
        branch=ACTIVE_BRANCH,
        source_root=root,
        groundtruth_root=root,
        run_root=None,
        c0_run_root=None,
        round_root=root / "rounds" / batch_id / ACTIVE_BRANCH,
        workspace_root=workspace_root,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        agent_runner=agent_runner,
        progress=progress,
    )
    if int(result.get("change_unit_count", 0)) > 2:
        raise CapabilityImprovementError(
            "human-guided synthesis exceeded two proposed changes"
        )
    return {
        **result,
        "human_condition": "offline_human_guided_fixed_c0_training_outputs",
        "packet_path": packet_path.as_posix(),
        "packet_digest": packet["packet_digest"],
        "next_action": "review the exact-byte pack proposal with the standard human review commands",
    }


def complete_human_guided_batch(
    *,
    experiment_root: Path,
    batch_id: str,
    reviewer_id: str,
    authorize_apply: bool,
    exemplar_max_results: int = 3,
    docker_image: str = "python:3.13-slim",
    timestamp: Any = None,
    synthetic_runner: Any = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Apply an already-final standard pack decision and freeze the checkpoint."""

    root = experiment_root.expanduser().resolve()
    build_human_guided_batch_packet(experiment_root=root, batch_id=batch_id)
    work_root = root / "rounds" / batch_id / ACTIVE_BRANCH
    proposal_path = work_root / "proposal.json"
    if not proposal_path.is_file():
        return {
            "status": "proposal_missing",
            "next_action": "run human batch synthesis",
            "proposal_path": proposal_path.as_posix(),
        }
    decision_path = work_root / "decision.json"
    if not decision_path.is_file():
        return {
            "status": "pack_review_required",
            "proposal_path": proposal_path.as_posix(),
            "decision_path": decision_path.as_posix(),
            "next_action": "use review-start, review-decide, and review-finalize before application",
        }
    from .workflow import validate_capability_decision

    _, decision = validate_capability_decision(
        proposal_path=proposal_path,
        decision_path=decision_path,
        require_final=False,
    )
    if decision["review_state"] == "revision_requested":
        revised_proposal = work_root / "proposal-r1.json"
        revised_decision = work_root / "decision-r1.json"
        if not revised_proposal.is_file() or not revised_decision.is_file():
            return {
                "status": "pack_revision_required",
                "next_action": "complete the bounded pack revision and fresh exact-byte review",
            }
        proposal_path = revised_proposal
        decision_path = revised_decision
        _, decision = validate_capability_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            require_final=False,
        )
    if decision["review_state"] != "final":
        return {
            "status": "pack_review_incomplete",
            "review_state": decision["review_state"],
            "next_action": "finalize the standard exact-byte pack decision",
        }
    if decision["reviewer"]["reviewer_id"] != reviewer_id:
        raise CapabilityImprovementError(
            "reviewer ID differs from the final exact-byte pack decision"
        )
    if not authorize_apply:
        return {
            "status": "ready_to_apply",
            "proposal_path": proposal_path.as_posix(),
            "decision_path": decision_path.as_posix(),
            "next_action": "rerun with explicit apply authorization",
        }
    from .local_completion import run_capability_completion

    result = run_capability_completion(
        branch=ACTIVE_BRANCH,
        review_mode="human",
        experiment_root=root,
        batch_id=batch_id,
        groundtruth_root=_infer_groundtruth_root(
            validate_human_guided_experiment(root)[1]
        ),
        authorize_apply=True,
        exemplar_max_results=exemplar_max_results,
        reviewer_id=reviewer_id,
        round_root=work_root,
        docker_image=docker_image,
        timestamp=timestamp,
        synthetic_runner=synthetic_runner,
        progress=progress,
        input_function=_unexpected_input,
    )
    return {
        **result,
        "display_checkpoint_id": {
            "C5": "H5",
            "C10": "H10",
            "C15": "H15",
            "C20": "H20",
            "C25": "H25",
        }.get(result.get("checkpoint_id")),
    }


def human_guided_status(experiment_root: Path) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    human, registry = validate_human_guided_experiment(root)
    batch_status: list[dict[str, Any]] = []
    total_reviewed = 0
    for batch_id in BATCH_IDS:
        entries = [item for item in registry["entries"] if item["batch_id"] == batch_id]
        reviewed = sum(_review_is_complete(root, item) for item in entries)
        total_reviewed += reviewed
        after = checkpoint_after_batch(batch_id)
        validation_path = root / "validation" / "aggregates" / f"{after}.json"
        batch_status.append(
            {
                "batch_id": batch_id,
                "reviewed_protocols": reviewed,
                "protocol_count": 5,
                "packet_ready": (
                    root / "rounds" / batch_id / ACTIVE_BRANCH / "packet.json"
                ).is_file(),
                "proposal_ready": (
                    root / "rounds" / batch_id / ACTIVE_BRANCH / "proposal.json"
                ).is_file(),
                "checkpoint": after,
                "display_checkpoint": human["checkpoint_aliases"][after],
                "checkpoint_ready": (root / "checkpoints" / after).is_dir(),
                "validation_ready": validation_path.is_file(),
            }
        )
    next_entry = _next_registry_entry(root, registry)
    invalid = [
        item["protocol_id"]
        for item in registry["entries"]
        if not item["prediction_valid"]
    ]
    return {
        "status": "complete" if total_reviewed == 25 else "in_progress",
        "human_experiment_digest": human["manifest_digest"],
        "reviewed_protocol_count": total_reviewed,
        "protocol_count": 25,
        "next_protocol": next_entry["protocol_id"] if next_entry else None,
        "next_batch": next_entry["batch_id"] if next_entry else None,
        "invalid_frozen_c0_predictions": invalid,
        "batches": batch_status,
        "validation_curve": _validation_curve(root, human),
        "next_action": _status_next_action(root, registry, next_entry),
    }


def record_human_validation_guidance(
    *,
    experiment_root: Path,
    checkpoint_label: str,
    codex_summary: str,
    human_note: str,
    reviewer_id: str,
    created_at: str,
) -> tuple[Path, dict[str, Any]]:
    """Record aggregate-only interpretation; never mutate the capability pack."""

    root = experiment_root.expanduser().resolve()
    human, _ = validate_human_guided_experiment(root)
    if checkpoint_label not in human["checkpoint_aliases"]:
        raise CapabilityImprovementError(f"unknown checkpoint: {checkpoint_label}")
    _assert_no_protected_transfer_text(
        [codex_summary, human_note], human["posthoc_transfer"]["protocol_ids"]
    )
    aggregate_path = root / "validation" / "aggregates" / f"{checkpoint_label}.json"
    aggregate = validate_validation_aggregate(
        aggregate_path,
        experiment_digest=human["execution_experiment"]["experiment_digest"],
        expected_checkpoint_label=checkpoint_label,
        expected_checkpoint_root=root / "checkpoints" / checkpoint_label,
    )
    labels = list(human["checkpoint_aliases"])
    index = labels.index(checkpoint_label)
    c0 = validate_validation_aggregate(
        root / "validation" / "aggregates" / "C0.json",
        experiment_digest=human["execution_experiment"]["experiment_digest"],
        expected_checkpoint_label="C0",
        expected_checkpoint_root=root / "checkpoints" / "C0",
    )
    previous = None
    previous_label = None
    if index > 0:
        previous_label = labels[index - 1]
        previous = validate_validation_aggregate(
            root / "validation" / "aggregates" / f"{previous_label}.json",
            experiment_digest=human["execution_experiment"]["experiment_digest"],
            expected_checkpoint_label=previous_label,
            expected_checkpoint_root=root / "checkpoints" / previous_label,
        )
    delta_c0 = {
        metric: aggregate["macro_means"][metric] - c0["macro_means"][metric]
        for metric in _METRICS
    }
    delta_previous = (
        {
            metric: aggregate["macro_means"][metric] - previous["macro_means"][metric]
            for metric in _METRICS
        }
        if previous is not None
        else None
    )
    target_batch = (
        BATCH_IDS[index]
        if checkpoint_label != "C25" and index < len(BATCH_IDS)
        else None
    )
    payload = {
        "schema_version": "libstruct.libgen_human_validation_guidance.v1",
        "human_experiment_digest": human["manifest_digest"],
        "checkpoint_label": checkpoint_label,
        "display_checkpoint_label": human["checkpoint_aliases"][checkpoint_label],
        "aggregate_digest": aggregate["aggregate_digest"],
        "aggregate_sha256": sha256_file(aggregate_path),
        "previous_checkpoint_label": previous_label,
        "macro_means": {
            metric: aggregate["macro_means"][metric] for metric in _METRICS
        },
        "delta_from_previous": delta_previous,
        "delta_from_c0": delta_c0,
        "guidance_target_batch": target_batch,
        "codex_summary": codex_summary.strip(),
        "human_note": human_note.strip(),
        "reviewer_id": reviewer_id.strip(),
        "created_at": _timestamp(created_at),
    }
    guidance = with_digest(payload, "guidance_digest")
    _validate_schema(guidance, HUMAN_GUIDANCE_SCHEMA, "human validation guidance")
    output_path = root / "human-guidance" / f"{checkpoint_label}.json"
    if output_path.is_file():
        existing = _load_human_document(
            output_path,
            schema=HUMAN_GUIDANCE_SCHEMA,
            digest_field="guidance_digest",
            label="human validation guidance",
        )
        if existing != guidance:
            raise CapabilityImprovementError(
                f"validation guidance is already frozen: {output_path}"
            )
        return output_path, existing
    write_json_atomic(output_path, guidance, mode=0o444)
    return output_path, guidance


def _finalize_protocol_review(
    *,
    root: Path,
    human: Mapping[str, Any],
    entry: Mapping[str, Any],
    proposal_path: Path,
    proposal: Mapping[str, Any],
    decision_path: Path,
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    review_root = _review_root(root, entry)
    comparison_path = review_root / "comparison.json"
    comparison = _load_human_document(
        comparison_path,
        schema=HUMAN_COMPARISON_SCHEMA,
        digest_field="comparison_digest",
        label="human protocol comparison",
    )
    source_analysis_path = _entry_artifact_path(entry, "verifier_error_analysis")
    analysis = copy.deepcopy(_json(source_analysis_path, "frozen C0 error analysis"))
    findings = {
        observation_id: finding
        for finding in proposal["root_findings"]
        for observation_id in finding["observation_ids"]
    }
    approved = decision["disposition"] == "approve"
    for observation in analysis["observations"]:
        if observation.get("substantive") is not True:
            continue
        finding = findings.get(observation["error_id"])
        if approved and finding is not None:
            observation["benchmark_validity"] = finding["benchmark_validity"]
            observation["attribution"] = finding["attribution"]
            observation["process_cause"] = finding["process_cause"]
            observation["process_evidence"] = copy.deepcopy(finding["process_evidence"])
            observation["adjudication_notes"] = (
                f"{finding['finding_id']}: {finding['diagnosis']}"
            )
        else:
            observation["benchmark_validity"] = "unresolved"
            observation["attribution"] = "unresolved"
            observation["process_cause"] = "unresolved"
            observation["process_evidence"] = []
            observation["adjudication_notes"] = (
                "Codex proposal was not approved; the substantive mismatch is "
                "reviewed but remains neutral for capability learning."
            )
        observation["benchmark_validity_candidate"] = None
        observation["candidate_reason"] = None
        observation["adjudication_status"] = "complete"
        observation["adjudicated_by"] = decision["reviewer_id"]
        observation["adjudicated_at"] = decision["completed_at"]
    process_review = analysis["process_review"]
    accepted_causes = sorted(
        {
            finding["process_cause"]
            for finding in proposal["root_findings"]
            if approved and finding["process_cause"] != "unresolved"
        }
    )
    accepted_evidence = [
        copy.deepcopy(evidence)
        for finding in proposal["root_findings"]
        if approved
        for evidence in finding["process_evidence"]
    ]
    process_review["review_status"] = "reviewed"
    process_review["categories"] = accepted_causes
    process_review["successful_self_correction"] = proposal[
        "successful_self_correction"
    ]
    process_review["evidence"] = _deduplicate_evidence(accepted_evidence)
    process_review["events"] = [
        event
        for event in process_review.get("events", [])
        if approved and event.get("process_cause") in accepted_causes
    ]
    process_review["notes"] = (
        "Process labels are based only on observable trajectory evidence accepted "
        "during the human review."
        if approved
        else "No Codex process attribution was accepted for capability learning."
    )
    process_review["reviewed_by"] = decision["reviewer_id"]
    process_review["reviewed_at"] = decision["completed_at"]
    analysis["review_status"] = "complete"
    analysis["notes"] = (
        "Human-reviewed overlay of the immutable C0 error analysis. Canonical "
        "ground truth and the original verifier artifacts were not changed."
    )
    analysis["summary"] = summarize_error_analysis(analysis)
    _validate_error_analysis(analysis, "human-adjudicated error analysis")
    adjudicated_path = review_root / "adjudicated-error-analysis.json"
    write_json_atomic(adjudicated_path, analysis, mode=0o444)

    eligible_findings = [
        finding
        for finding in proposal["root_findings"]
        if approved
        and finding["benchmark_validity"] == "valid"
        and finding["attribution"] == "agent"
    ]
    benchmark_findings = [
        finding
        for finding in proposal["root_findings"]
        if approved
        and (
            finding["benchmark_validity"] != "valid"
            or finding["attribution"] in {"benchmark", "mixed"}
        )
    ]
    adjudicated_digest = canonical_digest(analysis)
    record_payload = {
        "schema_version": "libstruct.libgen_human_protocol_review_record.v1",
        "record_id": f"{proposal['review_id']}:record",
        "human_experiment_digest": human["manifest_digest"],
        "review_id": proposal["review_id"],
        "batch_id": entry["batch_id"],
        "batch_position": entry["batch_position"],
        "global_position": entry["global_position"],
        "protocol_id": entry["protocol_id"],
        "comparison": _document_ref(
            comparison_path,
            relative_to=root,
            digest=comparison["comparison_digest"],
        ),
        "proposal": _document_ref(
            proposal_path,
            relative_to=root,
            digest=proposal["proposal_digest"],
        ),
        "decision": _document_ref(
            decision_path,
            relative_to=root,
            digest=decision["decision_digest"],
        ),
        "adjudicated_error_analysis": _document_ref(
            adjudicated_path,
            relative_to=root,
            digest=adjudicated_digest,
        ),
        "eligible_root_finding_count": len(eligible_findings),
        "benchmark_issue_count": len(benchmark_findings),
        "review_seconds": (
            _parse_time(decision["completed_at"]) - _parse_time(decision["started_at"])
        ).total_seconds(),
        "created_at": decision["completed_at"],
    }
    record = with_digest(record_payload, "record_digest")
    _validate_schema(record, HUMAN_RECORD_SCHEMA, "human protocol review record")
    record_path = review_root / "review-record.json"
    write_json_atomic(record_path, record, mode=0o444)
    return record


def _validate_protocol_findings(
    *,
    draft: Mapping[str, Any],
    comparison: Mapping[str, Any],
    human_manifest: Mapping[str, Any],
    parent_pack_root: Path,
) -> None:
    substantive = {
        item["error_id"]: item
        for item in comparison["observations"]
        if item.get("substantive") is True
    }
    findings = list(draft["root_findings"])
    finding_ids = [item["finding_id"] for item in findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise CapabilityImprovementError("protocol proposal repeats a finding ID")
    covered: list[str] = [
        observation_id
        for finding in findings
        for observation_id in finding["observation_ids"]
    ]
    if len(covered) != len(set(covered)):
        raise CapabilityImprovementError(
            "one metric discrepancy appears in more than one root finding"
        )
    if set(covered) != set(substantive):
        missing = sorted(set(substantive) - set(covered))
        extra = sorted(set(covered) - set(substantive))
        raise CapabilityImprovementError(
            "root findings must classify every substantive discrepancy exactly once; "
            f"missing={missing}, extra={extra}"
        )
    if findings and draft["no_change_rationale"] is not None:
        raise CapabilityImprovementError(
            "no-change rationale must be null when root findings are proposed"
        )
    if not findings and draft["no_change_rationale"] is None:
        raise CapabilityImprovementError(
            "a proposal without findings requires a no-change rationale"
        )
    trajectory_paths = {
        Path(item["path"]).expanduser().resolve()
        for item in comparison["artifacts"]
        if item["role"] == "trajectory"
    }
    protected_ids = human_manifest["posthoc_transfer"]["protocol_ids"]
    if draft["successful_self_correction"] == "observed" and not any(
        event.get("self_correction_observed") is True
        for item in comparison["artifacts"]
        if item["role"] == "verifier_error_analysis"
        for event in _json(Path(item["path"]), "frozen C0 error analysis")[
            "process_review"
        ]["events"]
    ):
        raise CapabilityImprovementError(
            "successful self-correction was marked observed without a trace event"
        )
    for finding in findings:
        observations = [substantive[item] for item in finding["observation_ids"]]
        categories = {item["category"] for item in observations}
        if categories != {finding["category"]}:
            raise CapabilityImprovementError(
                f"{finding['finding_id']} category does not match its observations"
            )
        eligible = (
            finding["benchmark_validity"] == "valid"
            and finding["attribution"] == "agent"
        )
        if eligible and any(
            item.get("claim_recoverability") not in _ELIGIBLE_RECOVERABILITY
            for item in observations
        ):
            raise CapabilityImprovementError(
                f"{finding['finding_id']} treats a neutral or unresolved source claim "
                "as an agent-learning error"
            )
        evidence = finding["process_evidence"]
        if finding["process_cause"] == "unresolved" and evidence:
            raise CapabilityImprovementError(
                f"{finding['finding_id']} supplies process evidence but leaves cause unresolved"
            )
        if finding["process_cause"] != "unresolved" and not evidence:
            raise CapabilityImprovementError(
                f"{finding['finding_id']} assigns process cause without trajectory evidence"
            )
        for item in evidence:
            evidence_path = Path(item["artifact_path"]).expanduser()
            if evidence_path.is_absolute():
                matches = evidence_path.resolve() in trajectory_paths or (
                    evidence_path.as_posix()
                    in {
                        "/logs/agent/trajectory.json",
                        "/logs/agent_trajectory.json",
                    }
                )
            else:
                matches = "trajectory" in evidence_path.name.lower()
            if not matches:
                raise CapabilityImprovementError(
                    f"{finding['finding_id']} process evidence is not tied to a trajectory"
                )
        generalization_text = [
            finding["generalized_failure_pattern"],
            finding["proposed_remedy"],
            *finding["applicability"],
            *finding["exclusions"],
        ]
        if any(_NUCLEOTIDE_RUN_RE.search(value) for value in generalization_text):
            raise CapabilityImprovementError(
                f"{finding['finding_id']} embeds a target sequence in a generalized lesson"
            )
        _assert_no_protected_transfer_text(generalization_text, protected_ids)
    validate_capability_pack(parent_pack_root)


def _render_protocol_review(
    *,
    comparison: Mapping[str, Any],
    entry: Mapping[str, Any],
    parent_pack_root: Path,
) -> str:
    metrics = comparison["metrics"]
    lines = [
        f"# Human-guided review: {comparison['protocol_id']}",
        "",
        f"Batch: {comparison['batch_id']} ({comparison['batch_position']}/5); "
        f"overall review {comparison['global_position']}/25",
        f"Frozen prediction valid: {'yes' if comparison['prediction_valid'] else 'no'}",
        f"Current capability pack: `{parent_pack_root}` "
        f"({comparison['display_parent_checkpoint']})",
        "",
        "The prediction is a frozen C0 output. Review it against the pinned ground "
        "truth and agent-visible sources; do not rerun it or change canonical labels.",
        "",
        "## Prior validation aggregate",
        "",
        "This aggregate is directional guidance only; it is not scientific evidence "
        "for any protocol finding and cannot directly change the pack.",
        "",
    ]
    validation = comparison["validation_context"]
    for metric in _METRICS:
        lines.append(
            f"- {metric}: {validation['macro_means'][metric]:.4f} "
            f"(delta from H0 {validation['delta_from_c0'][metric]:+.4f})"
        )
    if validation["human_guidance"] is not None:
        lines.append(
            f"- Human/Codex aggregate note: `{validation['human_guidance']['path']}`"
        )
    lines.extend(
        [
            "",
            "## Scores",
            "",
        ]
    )
    if metrics is None:
        lines.append(
            "No scientific score: linked prediction failed deterministic validation."
        )
    else:
        lines.extend(f"- {metric}: {metrics[metric]:.4f}" for metric in _METRICS)
    lines.extend(["", "## Deterministic discrepancies", ""])
    substantive = [
        item for item in comparison["observations"] if item.get("substantive") is True
    ]
    if not substantive:
        lines.append("No substantive discrepancy was detected.")
    else:
        lines.extend(
            [
                "| ID | Task | Category | Recoverability | Summary |",
                "|---|---|---|---|---|",
            ]
        )
        for item in substantive:
            summary = str(item["summary"]).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {item['error_id']} | {item['task']} | {item['category']} | "
                f"{item['claim_recoverability']} | {summary} |"
            )
    lines.extend(["", "## Pinned artifacts", ""])
    for artifact in entry["artifacts"]:
        lines.append(f"- {artifact['role']}: `{artifact['path']}`")
    lines.extend(
        [
            "",
            "## Review contract",
            "",
            "Classify each substantive discrepancy exactly once. A disagreement is "
            "eligible for capability learning only when it is benchmark-valid, "
            "agent-attributed, and recoverable from the visible source bundle. "
            "Externally completed, ambiguous, unsupported, benchmark, evaluator, "
            "and infrastructure issues remain neutral. Assign a process cause only "
            "when the trajectory contains observable evidence. Consolidate duplicate "
            "metric symptoms into one root finding, and check the current pack before "
            "proposing a generalized remedy.",
            "",
            "The human may approve, reject, mark unresolved, or request one revision.",
            "",
        ]
    )
    return "\n".join(lines)


def _prepared_review_result(
    review_root: Path,
    comparison: Mapping[str, Any],
    *,
    reused: bool,
) -> dict[str, Any]:
    return {
        "status": "review_ready",
        "reused": reused,
        "batch_id": comparison["batch_id"],
        "protocol_id": comparison["protocol_id"],
        "global_position": comparison["global_position"],
        "comparison_path": (review_root / "comparison.json").as_posix(),
        "review_path": (review_root / "review.md").as_posix(),
        "draft_template_path": (
            review_root / "proposal-draft.template.json"
        ).as_posix(),
        "comparison_digest": comparison["comparison_digest"],
        "next_action": "Codex reviews the pinned artifacts and authors proposal-draft.json",
    }


def _review_root(root: Path, entry: Mapping[str, Any]) -> Path:
    return (
        root
        / "human-reviews"
        / str(entry["batch_id"])
        / f"{int(entry['batch_position']):02d}-{entry['protocol_id']}"
    )


def _next_registry_entry(
    root: Path,
    registry: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    for entry in registry["entries"]:
        if not _review_is_complete(root, entry):
            return entry
    return None


def _registry_entry(
    registry: Mapping[str, Any],
    protocol_id: str,
) -> Mapping[str, Any]:
    values = [
        item for item in registry["entries"] if item["protocol_id"] == protocol_id
    ]
    if len(values) != 1:
        raise CapabilityImprovementError(
            f"unknown human training protocol: {protocol_id}"
        )
    return values[0]


def _manifest_batch(
    experiment: Mapping[str, Any],
    batch_id: str,
) -> Mapping[str, Any]:
    values = [item for item in experiment["batches"] if item["batch_id"] == batch_id]
    if len(values) != 1:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}")
    return values[0]


def _next_action_after_review(root: Path, entry: Mapping[str, Any]) -> str:
    if int(entry["batch_position"]) < 5:
        return "prepare the next protocol review"
    packet = root / "rounds" / str(entry["batch_id"]) / ACTIVE_BRANCH / "packet.json"
    return (
        "run five-protocol pack synthesis"
        if not packet.is_file()
        else "review the synthesized exact-byte pack proposal"
    )


def _status_next_action(
    root: Path,
    registry: Mapping[str, Any],
    next_entry: Mapping[str, Any] | None,
) -> str:
    if next_entry is None:
        return "finish H25 validation and the fixed posthoc transfer comparison"
    parent = checkpoint_before_batch(str(next_entry["batch_id"]))
    if not (root / "checkpoints" / parent).is_dir():
        previous = BATCH_IDS[BATCH_IDS.index(str(next_entry["batch_id"])) - 1]
        return f"finish and freeze {previous} before continuing protocol review"
    completed_in_batch = sum(
        _review_is_complete(root, item)
        for item in registry["entries"]
        if item["batch_id"] == next_entry["batch_id"]
    )
    if completed_in_batch == 5:
        return f"synthesize pack changes for {next_entry['batch_id']}"
    return f"prepare review for {next_entry['protocol_id']}"


def _validation_curve(
    root: Path,
    human: Mapping[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    c0_means: Mapping[str, float] | None = None
    for label, display in human["checkpoint_aliases"].items():
        path = root / "validation" / "aggregates" / f"{label}.json"
        checkpoint_root = root / "checkpoints" / label
        if not path.is_file() or not checkpoint_root.is_dir():
            continue
        aggregate = validate_validation_aggregate(
            path,
            experiment_digest=human["execution_experiment"]["experiment_digest"],
            expected_checkpoint_label=label,
            expected_checkpoint_root=checkpoint_root,
        )
        if c0_means is None:
            c0_means = aggregate["macro_means"]
        result.append(
            {
                "checkpoint": label,
                "display_checkpoint": display,
                "macro_means": {
                    metric: aggregate["macro_means"][metric] for metric in _METRICS
                },
                "delta_from_c0": {
                    metric: aggregate["macro_means"][metric] - c0_means[metric]
                    for metric in _METRICS
                },
                "scored_trials": aggregate["scored_trials"],
                "unscored_trials": aggregate["unscored_trials"],
            }
        )
    return result


def _clone_tree(
    source: Path,
    destination: Path,
    *,
    excluded_names: frozenset[str] = frozenset(),
) -> None:
    if not source.is_dir():
        raise CapabilityImprovementError(f"source tree is missing: {source}")

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & set(excluded_names)

    try:
        shutil.copytree(
            source,
            destination,
            copy_function=_link_or_copy,
            ignore=ignore if excluded_names else None,
        )
    except OSError as error:
        raise CapabilityImprovementError(
            f"cannot clone immutable experiment tree {source}: {error}"
        ) from error
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise CapabilityImprovementError(f"cloned tree contains a symlink: {path}")


def _link_or_copy(source: str | Path, destination: str | Path) -> str:
    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.is_symlink() or not source_path.is_file():
        raise CapabilityImprovementError(
            f"immutable clone accepts only regular files: {source_path}"
        )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source_path, destination_path)
    except OSError:
        shutil.copy2(source_path, destination_path)
    return destination_path.as_posix()


def _registry_artifact(role: str, unresolved: str | Path) -> dict[str, Any]:
    path = Path(unresolved).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise CapabilityImprovementError(f"registry artifact is missing: {path}")
    return {
        "role": role,
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _repin_registry_verifier_artifacts(
    *,
    registry: Mapping[str, Any],
    entry: Mapping[str, Any],
    rescored_paths: Mapping[str, Path],
    prediction_valid: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    required_roles = {
        "verifier_reward",
        "verifier_details",
        "verifier_error_analysis",
    }
    if set(rescored_paths) != required_roles:
        raise CapabilityImprovementError(
            "verifier repin requires reward, details, and error-analysis sidecars"
        )
    replacement_artifacts = {
        role: _registry_artifact(role, path) for role, path in rescored_paths.items()
    }
    new_entry_payload = copy.deepcopy(dict(entry))
    new_entry_payload.pop("entry_digest")
    new_entry_payload["prediction_valid"] = prediction_valid
    new_entry_payload["artifacts"] = [
        replacement_artifacts.get(str(artifact["role"]), copy.deepcopy(artifact))
        for artifact in entry["artifacts"]
    ]
    observed_roles = {
        artifact["role"]
        for artifact in new_entry_payload["artifacts"]
        if artifact["role"] in required_roles
    }
    if observed_roles != required_roles:
        raise CapabilityImprovementError(
            "human registry entry does not contain all verifier artifact roles"
        )
    new_entry = with_digest(new_entry_payload, "entry_digest")
    new_registry_payload = copy.deepcopy(dict(registry))
    new_registry_payload.pop("registry_digest")
    new_registry_payload["entries"] = [
        new_entry
        if item["protocol_id"] == entry["protocol_id"]
        else copy.deepcopy(item)
        for item in registry["entries"]
    ]
    new_registry_payload["valid_prediction_count"] = sum(
        item["prediction_valid"] for item in new_registry_payload["entries"]
    )
    new_registry_payload["invalid_prediction_count"] = (
        len(new_registry_payload["entries"])
        - new_registry_payload["valid_prediction_count"]
    )
    new_registry = with_digest(new_registry_payload, "registry_digest")
    return new_entry, new_registry, replacement_artifacts


def _external_artifact(unresolved: str | Path) -> dict[str, Any]:
    path = Path(unresolved).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise CapabilityImprovementError(f"external artifact is missing: {path}")
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _tree_reference(root: Path, *, relative_to: Path) -> dict[str, Any]:
    resolved = root.expanduser().resolve()
    base = relative_to.expanduser().resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise CapabilityImprovementError(f"archived tree is missing: {resolved}")
    files: list[dict[str, Any]] = []
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"archived tree contains a symlink: {path}"
            )
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(resolved).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    try:
        relative = resolved.relative_to(base).as_posix()
    except ValueError as error:
        raise CapabilityImprovementError(
            f"archived tree is outside its experiment root: {resolved}"
        ) from error
    return {
        "path": relative,
        "file_count": len(files),
        "tree_digest": canonical_digest(files),
    }


def _resolve_trial_artifact(path: Path, trial_root: Path, role: str) -> Path:
    if path.is_file() and not path.is_symlink():
        return path.resolve()
    candidates: list[Path] = []
    if role == "trajectory":
        candidates.append(trial_root / "artifacts" / "agent_trajectory.json")
    relative = path.relative_to(trial_root)
    candidates.extend(sorted(trial_root.glob(f".archived-*/{relative.as_posix()}")))
    candidates.extend(sorted(trial_root.glob(f"archived-*/{relative.as_posix()}")))
    matches = [
        item.resolve()
        for item in candidates
        if item.is_file() and not item.is_symlink()
    ]
    if len(matches) != 1:
        raise CapabilityImprovementError(
            f"cannot resolve exactly one {role} artifact under {trial_root}"
        )
    return matches[0]


def _one_packet_artifact(
    artifacts: Sequence[Mapping[str, Any]],
    role: str,
) -> Mapping[str, Any]:
    values = [item for item in artifacts if item["role"] == role]
    if len(values) != 1:
        raise CapabilityImprovementError(
            f"expected one {role} artifact, found {len(values)}"
        )
    return values[0]


def _assert_pinned_file(path: Path, reference: Mapping[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise CapabilityImprovementError(f"pinned artifact is missing: {resolved}")
    if sha256_file(resolved) != reference["sha256"]:
        raise CapabilityImprovementError(f"pinned artifact hash changed: {resolved}")


def _validate_registry_artifact(artifact: Mapping[str, Any]) -> None:
    path = Path(str(artifact["path"])).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise CapabilityImprovementError(f"frozen registry artifact is missing: {path}")
    if (
        sha256_file(path) != artifact["sha256"]
        or path.stat().st_size != artifact["size_bytes"]
    ):
        raise CapabilityImprovementError(f"frozen registry artifact changed: {path}")


def _entry_artifact(
    entry: Mapping[str, Any],
    role: str,
) -> Mapping[str, Any]:
    values = [item for item in entry["artifacts"] if item["role"] == role]
    if len(values) != 1:
        raise CapabilityImprovementError(
            f"{entry['protocol_id']} has {len(values)} artifacts for role {role}"
        )
    return values[0]


def _entry_artifact_path(entry: Mapping[str, Any], role: str) -> Path:
    return Path(str(_entry_artifact(entry, role)["path"])).expanduser().resolve()


def _packet_artifact(
    *,
    protocol_id: str,
    role: str,
    path: Path,
    expected_sha: str,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise CapabilityImprovementError(f"packet artifact is missing: {resolved}")
    if sha256_file(resolved) != expected_sha:
        raise CapabilityImprovementError(f"packet artifact hash changed: {resolved}")
    return {
        "protocol_id": protocol_id,
        "role": role,
        "path": resolved.as_posix(),
        "sha256": expected_sha,
        "visibility": "agent_after_reveal",
    }


def _document_ref(
    path: Path,
    *,
    relative_to: Path,
    digest: str,
) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    root = relative_to.expanduser().resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise CapabilityImprovementError(
            f"document is outside its experiment root: {resolved}"
        ) from error
    return {"path": relative, "sha256": sha256_file(resolved), "digest": digest}


def _validate_ref(path: Path, reference: Mapping[str, Any], digest: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise CapabilityImprovementError(f"referenced document is missing: {path}")
    if sha256_file(path) != reference["sha256"] or reference["digest"] != digest:
        raise CapabilityImprovementError(f"referenced document changed: {path}")


def _review_is_complete(root: Path, entry: Mapping[str, Any]) -> bool:
    path = _review_root(root, entry) / "review-record.json"
    if not path.is_file():
        return False
    _validate_review_record(root=root, entry=entry, deep=False)
    return True


def _validate_review_record(
    *,
    root: Path,
    entry: Mapping[str, Any],
    expected_human_digest: str | None = None,
    deep: bool,
) -> dict[str, Any]:
    record_path = _review_root(root, entry) / "review-record.json"
    record = _load_human_document(
        record_path,
        schema=HUMAN_RECORD_SCHEMA,
        digest_field="record_digest",
        label="human protocol review record",
    )
    expected = {
        "batch_id": entry["batch_id"],
        "batch_position": entry["batch_position"],
        "global_position": entry["global_position"],
        "protocol_id": entry["protocol_id"],
    }
    if expected_human_digest is not None:
        expected["human_experiment_digest"] = expected_human_digest
    for field, value in expected.items():
        if record[field] != value:
            raise CapabilityImprovementError(
                f"human review record has stale {field}: {record_path}"
            )
    if not deep:
        return record
    specifications = (
        ("comparison", HUMAN_COMPARISON_SCHEMA, "comparison_digest"),
        ("proposal", HUMAN_PROPOSAL_SCHEMA, "proposal_digest"),
        ("decision", HUMAN_DECISION_SCHEMA, "decision_digest"),
    )
    for field, schema, digest_field in specifications:
        reference = record[field]
        path = root / safe_relative_path(reference["path"])
        document = _load_human_document(
            path,
            schema=schema,
            digest_field=digest_field,
            label=f"human review {field}",
        )
        _validate_ref(path, reference, document[digest_field])
        if document["protocol_id"] != entry["protocol_id"]:
            raise CapabilityImprovementError(
                f"human review {field} belongs to another protocol"
            )
    analysis_ref = record["adjudicated_error_analysis"]
    analysis_path = root / safe_relative_path(analysis_ref["path"])
    analysis = _json(analysis_path, "human-adjudicated error analysis")
    _validate_error_analysis(analysis, "human-adjudicated error analysis")
    _validate_ref(analysis_path, analysis_ref, canonical_digest(analysis))
    if analysis["protocol_id"] != entry["protocol_id"]:
        raise CapabilityImprovementError(
            "human-adjudicated error analysis belongs to another protocol"
        )
    return record


def _validate_review_record_in_active_lineage(
    *,
    root: Path,
    human: Mapping[str, Any],
    entry: Mapping[str, Any],
    deep: bool,
) -> dict[str, Any]:
    """Accept a finalized review only while its exact registry entry survives.

    A deterministic verifier refresh for a later, undecided protocol changes
    the active human-manifest digest. Earlier human decisions remain immutable,
    so bind them to the archived manifest/registry lineage instead of rewriting
    their hashes. Any refresh of the reviewed protocol changes its entry digest
    and therefore invalidates this compatibility check.
    """

    record = _validate_review_record(root=root, entry=entry, deep=deep)
    record_human_digest = str(record["human_experiment_digest"])
    if record_human_digest == human["manifest_digest"]:
        return record

    for reference in human.get("verifier_refreshes", []):
        refresh_path = root / safe_relative_path(str(reference["path"]))
        refresh = _load_human_document(
            refresh_path,
            schema=HUMAN_VERIFIER_REFRESH_SCHEMA,
            digest_field="refresh_digest",
            label="human verifier refresh",
        )
        prior_manifest_path = root / safe_relative_path(
            str(refresh["prior_human_manifest"]["path"])
        )
        prior_manifest = _load_human_document(
            prior_manifest_path,
            schema=HUMAN_MANIFEST_SCHEMA,
            digest_field="manifest_digest",
            label="superseded human-guided experiment manifest",
        )
        if prior_manifest["manifest_digest"] != record_human_digest:
            continue
        prior_registry_path = root / safe_relative_path(
            str(refresh["prior_registry"]["path"])
        )
        prior_registry = _load_human_document(
            prior_registry_path,
            schema=HUMAN_REGISTRY_SCHEMA,
            digest_field="registry_digest",
            label="superseded human training registry",
        )
        prior_entry = _registry_entry(prior_registry, str(entry["protocol_id"]))
        if prior_entry["entry_digest"] != entry["entry_digest"]:
            raise CapabilityImprovementError(
                "completed human review predates a verifier refresh of its "
                f"own registry entry: {entry['protocol_id']}"
            )
        return record
    raise CapabilityImprovementError(
        "completed human review belongs to an unknown human-manifest ancestor: "
        f"{entry['protocol_id']}"
    )


def _load_human_document(
    path: Path,
    *,
    schema: str,
    digest_field: str,
    label: str,
) -> dict[str, Any]:
    document = _json(path, label)
    _validate_schema(document, schema, label)
    validate_digest(document, digest_field)
    return document


def _validate_schema(
    document: dict[str, Any],
    schema: str,
    label: str,
    *,
    schema_root: Path | None = None,
) -> None:
    root = schema_root or improvement_schema_root()
    try:
        validate_document(document, root / schema, label=label)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _validate_error_analysis(document: dict[str, Any], label: str) -> None:
    schema = (
        improvement_schema_root().parent
        / "analysis"
        / "libgen_error_analysis.schema.json"
    )
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        return load_json_object(path, label=label)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _timestamp(value: str) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_registry_order(
    registry: Mapping[str, Any],
    experiment: Mapping[str, Any],
) -> None:
    expected = [
        (batch["batch_id"], position, global_position, protocol_id)
        for global_position, (batch, position, protocol_id) in enumerate(
            (
                (batch, position, protocol_id)
                for batch in experiment["batches"]
                for position, protocol_id in enumerate(batch["protocol_ids"], start=1)
            ),
            start=1,
        )
    ]
    observed = [
        (
            entry["batch_id"],
            entry["batch_position"],
            entry["global_position"],
            entry["protocol_id"],
        )
        for entry in registry["entries"]
    ]
    if observed != expected:
        raise CapabilityImprovementError(
            "human training registry differs from frozen batch order"
        )
    for entry in registry["entries"]:
        validate_digest(entry, "entry_digest")
        for artifact in entry["artifacts"]:
            _validate_registry_artifact(artifact)
    if registry["valid_prediction_count"] + registry["invalid_prediction_count"] != 25:
        raise CapabilityImprovementError(
            "human registry validity counts do not sum to 25"
        )


def _validate_human_verifier_refreshes(
    *,
    root: Path,
    human: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> None:
    references = list(human.get("verifier_refreshes", []))
    if not references:
        return
    previous_registry_digest: str | None = None
    for refresh_index, reference in enumerate(references):
        refresh_path = root / safe_relative_path(str(reference["path"]))
        refresh = _load_human_document(
            refresh_path,
            schema=HUMAN_VERIFIER_REFRESH_SCHEMA,
            digest_field="refresh_digest",
            label="human verifier refresh",
        )
        _validate_ref(refresh_path, reference, refresh["refresh_digest"])
        protocol_id = str(refresh["protocol_id"])

        prior_manifest_path = root / safe_relative_path(
            str(refresh["prior_human_manifest"]["path"])
        )
        prior_manifest = _load_human_document(
            prior_manifest_path,
            schema=HUMAN_MANIFEST_SCHEMA,
            digest_field="manifest_digest",
            label="superseded human-guided experiment manifest",
        )
        _validate_ref(
            prior_manifest_path,
            refresh["prior_human_manifest"],
            prior_manifest["manifest_digest"],
        )
        prior_registry_path = root / safe_relative_path(
            str(refresh["prior_registry"]["path"])
        )
        prior_registry = _load_human_document(
            prior_registry_path,
            schema=HUMAN_REGISTRY_SCHEMA,
            digest_field="registry_digest",
            label="superseded human training registry",
        )
        _validate_ref(
            prior_registry_path,
            refresh["prior_registry"],
            prior_registry["registry_digest"],
        )
        if (
            previous_registry_digest is not None
            and prior_registry["registry_digest"] != previous_registry_digest
        ):
            raise CapabilityImprovementError(
                "human verifier refresh registry lineage is discontinuous"
            )
        prior_entry = _registry_entry(prior_registry, protocol_id)
        if prior_entry["entry_digest"] != refresh["prior_entry_digest"]:
            raise CapabilityImprovementError(
                f"human verifier refresh has stale prior entry: {protocol_id}"
            )
        for artifact in (
            *refresh["original_verifier_artifacts"],
            *refresh["rescored_verifier_artifacts"],
        ):
            _validate_registry_artifact(artifact)
        _validate_external_artifact(refresh["rescore_summary"])
        if refresh["archived_review"] is not None:
            _validate_tree_reference(root, refresh["archived_review"])

        # A protocol may be rescored again before its first human decision when
        # a later benchmark-version repair supersedes an earlier sidecar. Bind
        # each refresh to the next registry in the immutable lineage, rather
        # than assuming every intermediate entry remains in the active tip.
        target_registry = registry
        if refresh_index + 1 < len(references):
            next_reference = references[refresh_index + 1]
            next_refresh_path = root / safe_relative_path(str(next_reference["path"]))
            next_refresh = _load_human_document(
                next_refresh_path,
                schema=HUMAN_VERIFIER_REFRESH_SCHEMA,
                digest_field="refresh_digest",
                label="next human verifier refresh",
            )
            _validate_ref(
                next_refresh_path,
                next_reference,
                next_refresh["refresh_digest"],
            )
            next_registry_path = root / safe_relative_path(
                str(next_refresh["prior_registry"]["path"])
            )
            target_registry = _load_human_document(
                next_registry_path,
                schema=HUMAN_REGISTRY_SCHEMA,
                digest_field="registry_digest",
                label="next superseded human training registry",
            )
            _validate_ref(
                next_registry_path,
                next_refresh["prior_registry"],
                target_registry["registry_digest"],
            )
        if target_registry["registry_digest"] != refresh["new_registry_digest"]:
            raise CapabilityImprovementError(
                "human verifier refresh new registry does not reach the next "
                f"lineage point: {protocol_id}"
            )
        refreshed_entry = _registry_entry(target_registry, protocol_id)
        if refreshed_entry["entry_digest"] != refresh["new_entry_digest"]:
            raise CapabilityImprovementError(
                f"human verifier refresh has stale current entry: {protocol_id}"
            )
        rescored_by_role = {
            item["role"]: item for item in refresh["rescored_verifier_artifacts"]
        }
        for role, expected in rescored_by_role.items():
            if _entry_artifact(refreshed_entry, role) != expected:
                raise CapabilityImprovementError(
                    f"human registry does not use refreshed {role}: {protocol_id}"
                )
        previous_registry_digest = str(refresh["new_registry_digest"])
    if previous_registry_digest != registry["registry_digest"]:
        raise CapabilityImprovementError(
            "human verifier refresh chain does not reach the active registry"
        )


def _validate_external_artifact(artifact: Mapping[str, Any]) -> None:
    path = Path(str(artifact["path"])).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise CapabilityImprovementError(f"external artifact is missing: {path}")
    if (
        sha256_file(path) != artifact["sha256"]
        or path.stat().st_size != artifact["size_bytes"]
    ):
        raise CapabilityImprovementError(f"external artifact changed: {path}")


def _validate_tree_reference(root: Path, reference: Mapping[str, Any]) -> None:
    path = root / safe_relative_path(str(reference["path"]))
    observed = _tree_reference(path, relative_to=root)
    if observed != reference:
        raise CapabilityImprovementError(f"archived tree changed: {path}")


def _deduplicate_evidence(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    observed: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        digest = canonical_digest(value)
        if digest in observed:
            continue
        observed.add(digest)
        result.append(copy.deepcopy(dict(value)))
    return result


def _infer_groundtruth_root(registry: Mapping[str, Any]) -> Path:
    roots = {
        Path(item["path"]).expanduser().resolve().parents[1]
        for entry in registry["entries"]
        for item in entry["artifacts"]
        if item["role"].startswith("groundtruth_")
    }
    if len(roots) != 1:
        raise CapabilityImprovementError(
            "cannot infer one private ground-truth root from the frozen registry"
        )
    return next(iter(roots))


def _assert_no_protected_transfer_text(
    values: Sequence[str],
    protected_protocol_ids: Sequence[str],
) -> None:
    text = "\n".join(values).lower()
    hits = sorted(
        protocol_id
        for protocol_id in protected_protocol_ids
        if re.search(
            rf"(?<![a-z0-9]){re.escape(protocol_id.lower())}(?![a-z0-9])",
            text,
        )
    )
    if hits:
        raise CapabilityImprovementError(
            "human-guided learning text references the blocked posthoc transfer panel: "
            + ", ".join(hits)
        )


def _unexpected_input(prompt: str) -> str:
    raise CapabilityImprovementError(
        "final exact-byte decision unexpectedly requested interactive input: " + prompt
    )
