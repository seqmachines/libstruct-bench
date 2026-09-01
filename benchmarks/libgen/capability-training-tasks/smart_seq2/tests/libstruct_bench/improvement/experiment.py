from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_groundtruth_bundle,
)
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION

from .artifacts import (
    CapabilityImprovementError,
    artifact_record,
    canonical_digest,
    copy_capability_pack,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    reject_private_output_in_repository,
    safe_relative_path,
    trees_byte_identical,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .split_design import (
    FINAL_DEVELOPMENT_BATCHES,
    FINAL_TRANSFER_ANNOTATIONS,
    FINAL_TRANSFER_PANEL,
    FINAL_TRANSFER_PURPOSE,
    FINAL_TRANSFER_STRATA,
    INITIAL_DEVELOPMENT_BATCHES,
    INITIAL_TRANSFER_PANEL,
    FIXED_VALIDATION_PANEL,
    SUPERSEDED_DEVELOPMENT_BATCHES,
)
from .lineage import (
    ACTIVE_BRANCH,
    CHECKPOINT_LABELS,
    ENDPOINT_LABELS,
    REPLAY_LABELS,
    require_active_branch,
)


EXPERIMENT_SCHEMA_VERSION = "libstruct.libgen_capability_experiment.v1"
BATCH_PACKET_SCHEMA_VERSION = "libstruct.libgen_capability_batch_packet.v1"
FINAL_LOCK_SCHEMA_VERSION = "libstruct.libgen_capability_final_lock.v1"
CHECKPOINT_REATTESTATION_SCHEMA_VERSION = (
    "libstruct.libgen_checkpoint_split_reattestation.v1"
)
TRANSFER_PANEL_AUTHORIZATION_SCHEMA_VERSION = (
    "libstruct.libgen_transfer_panel_authorization.v1"
)
LEAKAGE_POLICY_SCHEMA_VERSION = "libstruct.libgen_capability_leakage_policy.v1"
NUCLEOTIDE_RUN_RE = re.compile(r"(?<![A-Za-z])[ACGTRYSWKMBDHVN]{8,}(?![A-Za-z])", re.I)
GROUNDTRUTH_FILENAMES = (
    "groundtruth_final_lib_struct.json",
    "groundtruth_oligos.json",
    "groundtruth_library_generation_workflow.json",
)

FIXED_BATCHES = FINAL_DEVELOPMENT_BATCHES
FROZEN_RETROSPECTIVE_TRANSFER_PANEL = FINAL_TRANSFER_PANEL
INITIAL_BATCHES = INITIAL_DEVELOPMENT_BATCHES
INITIAL_RETROSPECTIVE_TRANSFER_PANEL = INITIAL_TRANSFER_PANEL
TRANSFER_STRATA = FINAL_TRANSFER_STRATA
TRANSFER_ANNOTATIONS = FINAL_TRANSFER_ANNOTATIONS
TRANSFER_PURPOSE = FINAL_TRANSFER_PURPOSE
EXPECTED_CHECKPOINTS = CHECKPOINT_LABELS
ORIGINAL_PROTOCOLS: tuple[str, ...] = tuple(
    protocol_id
    for batch in FIXED_BATCHES
    if batch["phase"] == "retrospective"
    for protocol_id in batch["protocol_ids"]
)


def validate_fixed_partition(
    batches: Sequence[Mapping[str, Any]] = FIXED_BATCHES,
    validation_panel: Sequence[str] = FIXED_VALIDATION_PANEL,
    transfer_panel: Sequence[str] = FROZEN_RETROSPECTIVE_TRANSFER_PANEL,
) -> None:
    if len(batches) != 5:
        raise CapabilityImprovementError("experiment requires five training batches")
    actual_batches = []
    for item in batches:
        actual_batches.append(
            {
                "batch_id": item.get("batch_id"),
                "phase": item.get("phase"),
                "checkpoint_size": item.get("checkpoint_size"),
                "protocol_ids": tuple(item.get("protocol_ids", ())),
            }
        )
    if tuple(actual_batches) != FIXED_BATCHES:
        raise CapabilityImprovementError(
            "batch membership or order differs from the frozen experiment design"
        )
    if tuple(transfer_panel) != FROZEN_RETROSPECTIVE_TRANSFER_PANEL:
        raise CapabilityImprovementError(
            "transfer-panel membership or order differs from the frozen design"
        )
    if tuple(validation_panel) != FIXED_VALIDATION_PANEL:
        raise CapabilityImprovementError(
            "validation-panel membership or order differs from the frozen design"
        )
    development = [
        protocol_id for batch in batches for protocol_id in batch["protocol_ids"]
    ]
    all_protocols = development + list(validation_panel) + list(transfer_panel)
    if (
        len(development) != 25
        or len(validation_panel) != 5
        or len(transfer_panel) != 10
    ):
        raise CapabilityImprovementError(
            "experiment must use 25 training, 5 validation, and 10 final-test protocols"
        )
    if len(set(all_protocols)) != 40:
        raise CapabilityImprovementError(
            "development and transfer-panel protocols must be disjoint and unique"
        )


def validate_initial_partition(
    batches: Sequence[Mapping[str, Any]] = INITIAL_BATCHES,
    transfer_panel: Sequence[str] = INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
) -> None:
    actual_batches = tuple(
        {
            "batch_id": item.get("batch_id"),
            "phase": item.get("phase"),
            "checkpoint_size": item.get("checkpoint_size"),
            "protocol_ids": tuple(item.get("protocol_ids", ())),
        }
        for item in batches
    )
    if actual_batches != INITIAL_BATCHES or tuple(transfer_panel) != (
        INITIAL_RETROSPECTIVE_TRANSFER_PANEL
    ):
        raise CapabilityImprovementError(
            "pre-freeze partition differs from the original v1 design"
        )
    development = [
        protocol_id for batch in batches for protocol_id in batch["protocol_ids"]
    ]
    if (
        len(development) != 30
        or len(transfer_panel) != 10
        or len(set(development + list(transfer_panel))) != 40
    ):
        raise CapabilityImprovementError(
            "pre-freeze development and transfer protocols must be a disjoint 30/10 partition"
        )


def build_cumulative_experiment_manifest(
    *,
    experiment_root: Path,
    capability_pack_root: Path,
    private_groundtruth_root: Path,
    c0_provenance_audit_path: Path,
    transfer_access_policy_path: Path,
    validation_access_policy_path: Path,
    validation_isolation_audit_path: Path,
    exemplar_projection_policy_path: Path,
    exemplar_memory_adoption_path: Path,
    frozen_split_path: Path,
    test_isolation_audit_path: Path,
    transfer_panel_commitment_path: Path,
    supersession_manifest_path: Path,
    single_branch_migration_path: Path,
    created_at: str,
    agent_version: str,
    experiment_id: str = "libgen-capability-improvement-v1",
    benchmark_version: str = LIBGEN_BENCHMARK_VERSION,
) -> dict[str, Any]:
    """Build the active cumulative manifest before the C0 record exists.

    All governance artifacts must already be staged under ``experiment_root``.
    The caller writes this manifest, freezes C0 with its digest, and then calls
    :func:`validate_experiment_manifest` on the complete staged root.
    """

    root = experiment_root.expanduser().resolve()
    validate_fixed_partition()
    pack = validate_capability_pack(capability_pack_root)
    truth_inventory = _groundtruth_inventory(private_groundtruth_root)
    prospective_entries = [
        truth_inventory[protocol_id]
        for batch in FIXED_BATCHES
        if batch["phase"] == "prospective"
        for protocol_id in batch["protocol_ids"]
    ]

    provenance = load_and_validate(
        c0_provenance_audit_path,
        schema_filename="s0_provenance_audit.schema.json",
        digest_field="audit_digest",
        label="C0 source-pack provenance audit",
    )
    if provenance["pack_digest"] != pack["pack_digest"]:
        raise CapabilityImprovementError(
            "C0 provenance audit covers another source pack"
        )
    from .governance import validate_transfer_access_policy
    from .single_branch_migration import MIGRATION_SCHEMA_VERSION
    from .split_freeze import (
        validate_frozen_split,
        validate_transfer_panel_commitment,
    )
    from .validation import (
        validation_panel_commitment_digest,
        validate_validation_access_policy,
    )

    transfer_policy = validate_transfer_access_policy(transfer_access_policy_path)
    validation_policy = validate_validation_access_policy(validation_access_policy_path)
    frozen_split = validate_frozen_split(frozen_split_path)
    transfer_commitment = validate_transfer_panel_commitment(
        transfer_panel_commitment_path
    )
    test_isolation = load_and_validate(
        test_isolation_audit_path,
        schema_filename="test_isolation_audit.schema.json",
        digest_field="audit_digest",
        label="final-test isolation audit",
    )
    validation_isolation = load_and_validate(
        validation_isolation_audit_path,
        schema_filename="validation_isolation_audit.schema.json",
        digest_field="audit_digest",
        label="validation isolation audit",
    )
    exemplar_policy = load_and_validate(
        exemplar_projection_policy_path,
        schema_filename="exemplar_projection_policy.schema.json",
        digest_field="policy_digest",
        label="approved-training exemplar projection policy",
    )
    exemplar_adoption = load_and_validate(
        exemplar_memory_adoption_path,
        schema_filename="exemplar_memory_adoption.schema.json",
        digest_field="adoption_digest",
        label="exemplar-memory adoption record",
    )
    if (
        exemplar_adoption["policy_digest"] != exemplar_policy["policy_digest"]
        or exemplar_adoption["identity_map_digest"]
        != exemplar_policy["identity_map"]["map_digest"]
        or exemplar_adoption["identity_map_sha256"]
        != exemplar_policy["identity_map"]["map_sha256"]
        or exemplar_adoption["identity_map_public_commitment_sha256"]
        != exemplar_policy["identity_map"]["public_commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "exemplar-memory adoption and projection policy differ"
        )
    load_and_validate(
        supersession_manifest_path,
        schema_filename="supersession_manifest.schema.json",
        digest_field="supersession_digest",
        label="original experiment supersession",
    )
    migration = load_and_validate(
        single_branch_migration_path,
        schema_filename="single_branch_migration.schema.json",
        digest_field="migration_digest",
        label="single-branch migration",
    )
    if migration["schema_version"] != MIGRATION_SCHEMA_VERSION:
        raise CapabilityImprovementError("unknown single-branch migration schema")

    validation_commitment = validation_panel_commitment_digest()
    panel_commitment = transfer_commitment["commitment_sha256"]
    if (
        validation_policy["validation_panel_commitment_sha256"] != validation_commitment
        or validation_isolation["validation_panel_commitment_sha256"]
        != validation_commitment
        or migration["validation_panel_commitment_sha256"] != validation_commitment
    ):
        raise CapabilityImprovementError(
            "validation governance artifacts bind different panels"
        )
    if (
        transfer_policy["panel_commitment_sha256"] != panel_commitment
        or migration["final_test_panel_commitment_sha256"] != panel_commitment
        or frozen_split["final_test_panel"]["commitment_sha256"] != panel_commitment
        or test_isolation["protocol_ids"] != list(FROZEN_RETROSPECTIVE_TRANSFER_PANEL)
    ):
        raise CapabilityImprovementError(
            "final-test governance artifacts bind different panels"
        )

    def reference(path: Path, digest_field: str) -> dict[str, str]:
        resolved = path.expanduser().resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as error:
            raise CapabilityImprovementError(
                f"governance artifact is outside the experiment root: {resolved}"
            ) from error
        document = json.loads(resolved.read_text(encoding="utf-8"))
        return {
            "path": relative,
            "sha256": sha256_file(resolved),
            "digest": str(document[digest_field]),
        }

    batches = []
    for batch in FIXED_BATCHES:
        batches.append(
            {
                "batch_id": batch["batch_id"],
                "phase": batch["phase"],
                "checkpoint_size": batch["checkpoint_size"],
                "protocol_ids": list(batch["protocol_ids"]),
                "groundtruth_commitment_sha256": (
                    _batch_groundtruth_commitment(batch, truth_inventory)
                    if batch["phase"] == "prospective"
                    else None
                ),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "created_at": normalized_timestamp(created_at),
        "benchmark_version": benchmark_version,
        "anchor": {
            "agent": "codex",
            "harness": "native",
            "model": "gpt-5.6-sol",
            "agent_version": agent_version,
            "reasoning_effort": "max",
            "concurrency": 1,
        },
        "initial_pack": {
            "pack_digest": pack["pack_digest"],
            "manifest_sha256": sha256_file(capability_pack_root / "manifest.json"),
            "references": {"C0": "checkpoints/C0/pack"},
            "provenance_audit": reference(c0_provenance_audit_path, "audit_digest"),
        },
        "memory_model": {
            "procedural": {
                "checkpoint_path_template": "checkpoints/{checkpoint_id}/pack",
                "contents": [
                    "instructions",
                    "deterministic_scripts",
                    "synthetic_tests",
                ],
                "cumulative": True,
                "maximum_proposed_per_batch": 2,
                "maximum_accepted_per_batch": 1,
                "synthetic_regression_required": True,
            },
            "exemplar": {
                "checkpoint_path_template": "checkpoints/{checkpoint_id}/memory",
                "classification": ("prediction_shaped_approved_training_exemplars"),
                "cumulative": True,
                "policy": reference(exemplar_projection_policy_path, "policy_digest"),
                "adoption": reference(exemplar_memory_adoption_path, "adoption_digest"),
                "identity_map_public_commitment_sha256": exemplar_policy[
                    "identity_map"
                ]["public_commitment_sha256"],
                "checkpoint_exemplar_counts": {
                    "C0": 0,
                    "C5": 5,
                    "C10": 10,
                    "C15": 15,
                    "C20": 20,
                    "C25": 25,
                },
                "agent_access": (
                    "deterministic_query_returns_at_most_three_relevant_subgraphs"
                ),
            },
            "public_statement": (
                "Raw GT and audit records are not exposed. Approved training GT "
                "is projected into prediction-shaped exemplars and retained as "
                "cumulative memory."
            ),
        },
        "batches": batches,
        "prospective_groundtruth": {
            "set_id": "libgen-prospective-development-v1",
            "protocol_count": 10,
            "commitment_sha256": canonical_digest(
                {
                    "set_id": "libgen-prospective-development-v1",
                    "entries": prospective_entries,
                }
            ),
            "current_schema_valid_count": sum(
                item["linked_validation"] == "pass" for item in prospective_entries
            ),
            "compatibility_status": _compatibility_status(prospective_entries),
            "agent_visibility": (
                "none_until_c0_and_current_cumulative_runs_are_terminal"
            ),
        },
        "validation_panel": {
            "set_id": "fixed-validation-panel-v1",
            "classification": "fixed_validation_panel",
            "protocol_count": 5,
            "protocol_ids": list(FIXED_VALIDATION_PANEL),
            "commitment_sha256": validation_commitment,
            "evaluation_checkpoints": list(CHECKPOINT_LABELS),
            "learning_visibility": (
                "five_protocol_macro_aggregate_only_no_example_memory"
            ),
            "access_policy": reference(validation_access_policy_path, "policy_digest"),
        },
        "frozen_retrospective_transfer_panel": {
            "set_id": "frozen-retrospective-transfer-panel-v1",
            "classification": "frozen_retrospective_transfer_panel",
            "protocol_count": 10,
            "protocol_ids": list(FROZEN_RETROSPECTIVE_TRANSFER_PANEL),
            "transfer_strata": [
                {"name": name, "protocol_ids": list(protocol_ids)}
                for name, protocol_ids in TRANSFER_STRATA.items()
            ],
            "transfer_annotations": [dict(item) for item in TRANSFER_ANNOTATIONS],
            "purposes": list(TRANSFER_PURPOSE),
            "commitment_sha256": panel_commitment,
            "selection_timing": (
                "preserved_from_superseded_30_10_split_before_clean_C0_restart"
            ),
            "capability_updates_before_selection": 2,
            "selected_before_any_capability_update": False,
            "selected_after_baseline_inspection": True,
            "selection_basis": (
                "protocol_identity_and_predeclared_transfer_structure_"
                "without_test_groundtruth_or_scores"
            ),
            "test_scores_inspected": False,
            "unseen_or_sealed_claim": True,
            "improvement_visibility": (
                "blocked_for_improvement_worker_independent_critic_"
                "and_human_review_console"
            ),
            "endpoint_labels": list(ENDPOINT_LABELS),
            "baseline_mode": "post_lock_c0_replay",
            "access_policy": reference(transfer_access_policy_path, "policy_digest"),
        },
        "transfer_panel_commitment": reference(
            transfer_panel_commitment_path, "commitment_manifest_digest"
        ),
        "frozen_split": reference(frozen_split_path, "split_manifest_digest"),
        "test_isolation_audit": reference(test_isolation_audit_path, "audit_digest"),
        "validation_isolation_audit": reference(
            validation_isolation_audit_path, "audit_digest"
        ),
        "single_branch_migration": reference(
            single_branch_migration_path, "migration_digest"
        ),
        "supersession": reference(supersession_manifest_path, "supersession_digest"),
        "policies": {
            "revision_rounds": 1,
            "editable_scope": "neutral_core_only",
            "review_modes": ["independent_codex", "human"],
            "active_branch": ACTIVE_BRANCH,
            "primary_outcome": "t3_molecular_transition_f1",
            "checkpoint_labels": list(CHECKPOINT_LABELS),
            "endpoint_labels": list(ENDPOINT_LABELS),
            "infrastructure_retries": 2,
            "semantic_retries": 0,
        },
    }
    result = with_digest(payload, "experiment_digest")
    validate_document(
        result,
        improvement_schema_root() / "experiment_manifest.schema.json",
        label="cumulative capability experiment manifest",
    )
    return result


def initialize_superseded_experiment(
    *,
    output_root: Path,
    capability_pack_root: Path,
    retrospective_run_root: Path,
    private_groundtruth_root: Path,
    s0_provenance_audit: Mapping[str, Any],
    transfer_access_policy: Mapping[str, Any],
    baseline_registry: Mapping[str, Any],
    supersession_manifest: Mapping[str, Any],
    created_at: str,
    agent_version: str,
    experiment_id: str = "libgen-capability-improvement-v1",
    benchmark_version: str = LIBGEN_BENCHMARK_VERSION,
) -> dict[str, Any]:
    """Reconstruct the retired pre-migration experiment for history tooling only."""

    output_root = output_root.expanduser().resolve()
    reject_private_output_in_repository(output_root)
    validate_initial_partition()
    pack = validate_capability_pack(capability_pack_root)
    baseline = _retrospective_baseline(retrospective_run_root)
    new_truth = _groundtruth_inventory(private_groundtruth_root)
    prospective_entries = [
        new_truth[protocol_id]
        for batch in FIXED_BATCHES
        if batch["phase"] == "prospective"
        for protocol_id in batch["protocol_ids"]
    ]
    prospective_commitment = canonical_digest(
        {
            "set_id": "libgen-prospective-development-v1",
            "entries": prospective_entries,
        }
    )
    panel_commitment = canonical_digest(
        {
            "set_id": "frozen-retrospective-transfer-panel-v1",
            "protocol_ids": list(INITIAL_RETROSPECTIVE_TRANSFER_PANEL),
        }
    )
    _validate_initial_governance(
        pack_digest=pack["pack_digest"],
        panel_commitment_sha256=panel_commitment,
        s0_provenance_audit=s0_provenance_audit,
        transfer_access_policy=transfer_access_policy,
        baseline_registry=baseline_registry,
        supersession_manifest=supersession_manifest,
    )
    if output_root.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite experiment root: {output_root}"
        )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.building-", dir=output_root.parent
        )
    )
    try:
        pack_root = temporary / "packs"
        references: dict[str, str] = {}
        for label in ("S0", "A0", "H0"):
            destination = pack_root / label
            copy_capability_pack(capability_pack_root, destination, freeze=True)
            references[label] = destination.relative_to(temporary).as_posix()
        if not (
            trees_byte_identical(pack_root / "S0", pack_root / "A0")
            and trees_byte_identical(pack_root / "S0", pack_root / "H0")
        ):
            raise CapabilityImprovementError("S0, A0, and H0 are not byte-identical")
        manifest_sha = sha256_file(pack_root / "S0" / "manifest.json")
        design_root = temporary / "design"
        governance_files = {
            "s0_provenance_audit.json": dict(s0_provenance_audit),
            "transfer_access_policy.json": dict(transfer_access_policy),
            "baseline_registry.json": dict(baseline_registry),
            "supersession_manifest.json": dict(supersession_manifest),
        }
        for filename, document in governance_files.items():
            write_json_atomic(design_root / filename, document, mode=0o400)

        def governance_ref(filename: str, digest_field: str) -> dict[str, str]:
            document = governance_files[filename]
            return {
                "path": f"design/{filename}",
                "sha256": sha256_file(design_root / filename),
                "digest": str(document[digest_field]),
            }

        payload: dict[str, Any] = {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "created_at": normalized_timestamp(created_at),
            "benchmark_version": benchmark_version,
            "anchor": {
                "agent": "codex",
                "harness": "native",
                "model": "gpt-5.6-sol",
                "agent_version": agent_version,
                "reasoning_effort": "max",
                "concurrency": 1,
            },
            "initial_pack": {
                "pack_digest": pack["pack_digest"],
                "manifest_sha256": manifest_sha,
                "references": references,
                "provenance_audit": governance_ref(
                    "s0_provenance_audit.json", "audit_digest"
                ),
            },
            "retrospective_development_baseline": baseline,
            "batches": [
                {
                    "batch_id": batch["batch_id"],
                    "phase": batch["phase"],
                    "checkpoint_size": batch["checkpoint_size"],
                    "protocol_ids": list(batch["protocol_ids"]),
                    "groundtruth_commitment_sha256": (
                        _batch_groundtruth_commitment(batch, new_truth)
                        if batch["phase"] == "prospective"
                        else None
                    ),
                }
                for batch in INITIAL_BATCHES
            ],
            "prospective_groundtruth": {
                "set_id": "libgen-prospective-development-v1",
                "protocol_count": 20,
                "commitment_sha256": prospective_commitment,
                "current_schema_valid_count": sum(
                    item["linked_validation"] == "pass" for item in prospective_entries
                ),
                "compatibility_status": _compatibility_status(prospective_entries),
                "agent_visibility": "none_until_s0_and_both_active_branches_terminal",
            },
            "frozen_retrospective_transfer_panel": {
                "set_id": "frozen-retrospective-transfer-panel-v1",
                "classification": "frozen_retrospective_transfer_panel",
                "protocol_count": 10,
                "protocol_ids": list(INITIAL_RETROSPECTIVE_TRANSFER_PANEL),
                "commitment_sha256": panel_commitment,
                "selection_timing": (
                    "after_baseline_inspection_before_A5_H5_and_any_later_update"
                ),
                "capability_updates_before_selection": 0,
                "selected_before_any_capability_update": True,
                "selected_after_baseline_inspection": True,
                "unseen_or_sealed_claim": False,
                "improvement_visibility": (
                    "blocked_for_worker_critic_and_human_review_console"
                ),
                "endpoint_labels": list(ENDPOINT_LABELS),
                "access_policy": governance_ref(
                    "transfer_access_policy.json", "policy_digest"
                ),
                "baseline_registry": governance_ref(
                    "baseline_registry.json", "registry_digest"
                ),
            },
            "supersession": governance_ref(
                "supersession_manifest.json", "supersession_digest"
            ),
            "policies": {
                "revision_rounds": 1,
                "editable_scope": "neutral_core_only",
                "autonomous_review": "independent_self_review",
                "human_review": "resumable_human_decisions",
                "primary_outcome": "t3_molecular_transition_f1",
                "checkpoint_labels": list(CHECKPOINT_LABELS),
                "endpoint_labels": list(ENDPOINT_LABELS),
                "infrastructure_retries": 2,
                "semantic_retries": 0,
            },
        }
        manifest = with_digest(payload, "experiment_digest")
        write_json_atomic(design_root / "experiment_manifest.json", manifest)
        write_json_atomic(
            design_root / "transfer_panel_commitment.json",
            {
                "set_id": "frozen-retrospective-transfer-panel-v1",
                "classification": "frozen_retrospective_transfer_panel",
                "protocol_count": 10,
                "protocol_ids": list(INITIAL_RETROSPECTIVE_TRANSFER_PANEL),
                "commitment_sha256": panel_commitment,
                "selection_timing": (
                    "after_baseline_inspection_before_A5_H5_and_any_later_update"
                ),
                "capability_updates_before_selection": 0,
                "selected_before_any_capability_update": True,
                "selected_after_baseline_inspection": True,
                "unseen_or_sealed_claim": False,
            },
            mode=0o444,
        )
        validate_experiment_manifest(
            design_root / "experiment_manifest.json",
            experiment_root=temporary,
        )
        temporary.replace(output_root)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_experiment_manifest(
    path: Path,
    *,
    experiment_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the only active experiment shape: cumulative 25/5/10.

    Pre-migration A/H manifests are deliberately rejected here.  The migration
    transaction may inspect those immutable bytes only through
    :func:`validate_superseded_experiment_manifest`.
    """

    document = load_and_validate(
        path,
        schema_filename="experiment_manifest.schema.json",
        digest_field="experiment_digest",
        label="active cumulative capability experiment manifest",
    )
    root = (
        experiment_root.expanduser().resolve()
        if experiment_root is not None
        else path.expanduser().resolve().parents[1]
    )
    _validate_cumulative_experiment_manifest(document, root=root)
    return document


def validate_superseded_experiment_manifest(
    path: Path,
    *,
    experiment_root: Path | None = None,
) -> dict[str, Any]:
    """Validate an immutable predecessor manifest for migration only.

    This validator intentionally does not use the active schema.  It accepts
    only the two exact historical 30/10 partitions and requires the original
    S0/A0/H0 pack aliases to remain byte-identical.  It must never be used by
    learning, review, checkpointing, locking, or replay code.
    """

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read superseded experiment manifest {path}: {error}"
        ) from error
    if not isinstance(document, dict):
        raise CapabilityImprovementError(
            "superseded experiment manifest must be a JSON object"
        )
    validate_digest(document, "experiment_digest")
    if "validation_panel" in document:
        raise CapabilityImprovementError(
            "active cumulative manifests are not superseded-history inputs"
        )
    batches = tuple(
        {
            "batch_id": item.get("batch_id"),
            "phase": item.get("phase"),
            "checkpoint_size": item.get("checkpoint_size"),
            "protocol_ids": tuple(item.get("protocol_ids", ())),
        }
        for item in document.get("batches", ())
        if isinstance(item, Mapping)
    )
    panel = document.get("frozen_retrospective_transfer_panel")
    if not isinstance(panel, Mapping):
        raise CapabilityImprovementError(
            "superseded experiment lacks its frozen transfer panel"
        )
    if batches == INITIAL_DEVELOPMENT_BATCHES:
        expected_panel = INITIAL_RETROSPECTIVE_TRANSFER_PANEL
    elif batches == SUPERSEDED_DEVELOPMENT_BATCHES:
        expected_panel = FROZEN_RETROSPECTIVE_TRANSFER_PANEL
    else:
        raise CapabilityImprovementError(
            "superseded experiment differs from both exact historical 30/10 designs"
        )
    if tuple(panel.get("protocol_ids", ())) != expected_panel:
        raise CapabilityImprovementError(
            "superseded experiment transfer panel differs from its historical design"
        )
    refs = document.get("initial_pack", {}).get("references", {})
    if set(refs) != {"S0", "A0", "H0"}:
        raise CapabilityImprovementError(
            "superseded experiment must preserve the exact S0/A0/H0 aliases"
        )
    root = (
        experiment_root.expanduser().resolve()
        if experiment_root is not None
        else path.expanduser().resolve().parents[1]
    )
    pack_roots = [root / str(refs[label]) for label in ("S0", "A0", "H0")]
    packs = [validate_capability_pack(pack_root) for pack_root in pack_roots]
    expected_pack_digest = document.get("initial_pack", {}).get("pack_digest")
    if {item["pack_digest"] for item in packs} != {expected_pack_digest}:
        raise CapabilityImprovementError(
            "superseded initial pack aliases have different digests"
        )
    if not (
        trees_byte_identical(pack_roots[0], pack_roots[1])
        and trees_byte_identical(pack_roots[0], pack_roots[2])
    ):
        raise CapabilityImprovementError(
            "superseded S0, A0, and H0 aliases are not byte-identical"
        )
    return document


def _validate_cumulative_experiment_manifest(
    document: Mapping[str, Any],
    *,
    root: Path,
) -> None:
    panel = document["frozen_retrospective_transfer_panel"]
    validation_panel = document["validation_panel"]
    validate_fixed_partition(
        document["batches"],
        validation_panel["protocol_ids"],
        panel["protocol_ids"],
    )
    for batch in document["batches"]:
        commitment = batch["groundtruth_commitment_sha256"]
        if batch["phase"] == "retrospective" and commitment is not None:
            raise CapabilityImprovementError(
                "retrospective batch unexpectedly contains a private truth "
                f"commitment: {batch['batch_id']}"
            )
        if batch["phase"] == "prospective" and commitment is None:
            raise CapabilityImprovementError(
                "prospective batch lacks a frozen truth commitment: "
                f"{batch['batch_id']}"
            )
    truth = document["prospective_groundtruth"]
    expected_status = (
        "current_schema_valid"
        if truth["current_schema_valid_count"] == truth["protocol_count"]
        else "hash_pinned_schema_migration_required"
    )
    if truth["compatibility_status"] != expected_status:
        raise CapabilityImprovementError(
            "prospective ground-truth compatibility status is inconsistent"
        )
    if truth["agent_visibility"] != (
        "none_until_c0_and_current_cumulative_runs_are_terminal"
    ):
        raise CapabilityImprovementError(
            "prospective ground-truth visibility boundary was weakened"
        )
    policies = document["policies"]
    if (
        policies["active_branch"] != ACTIVE_BRANCH
        or tuple(policies["review_modes"]) != ("independent_codex", "human")
        or tuple(policies["checkpoint_labels"]) != CHECKPOINT_LABELS
        or tuple(policies["endpoint_labels"]) != ENDPOINT_LABELS
    ):
        raise CapabilityImprovementError(
            "active branch, review modes, or checkpoint labels differ from the "
            "single cumulative design"
        )
    if (
        tuple(validation_panel["protocol_ids"]) != FIXED_VALIDATION_PANEL
        or tuple(validation_panel["evaluation_checkpoints"]) != CHECKPOINT_LABELS
    ):
        raise CapabilityImprovementError(
            "validation panel differs from the fixed cumulative design"
        )
    if (
        tuple(panel["endpoint_labels"]) != ENDPOINT_LABELS
        or panel["baseline_mode"] != "post_lock_c0_replay"
    ):
        raise CapabilityImprovementError(
            "final-test panel must use C25 as its predefined endpoint and C0 "
            "as its post-lock replay baseline"
        )
    memory_model = document["memory_model"]
    if (
        memory_model["procedural"]["maximum_proposed_per_batch"] != 2
        or memory_model["procedural"]["maximum_accepted_per_batch"] != 1
        or memory_model["exemplar"]["checkpoint_exemplar_counts"]
        != {"C0": 0, "C5": 5, "C10": 10, "C15": 15, "C20": 20, "C25": 25}
    ):
        raise CapabilityImprovementError(
            "procedural and exemplar memory budgets differ from the frozen design"
        )

    from .governance import (
        validate_transfer_access_policy,
        validate_transfer_policy_panel_binding,
    )
    from .exemplar_governance import (
        validate_exemplar_memory_adoption,
        validate_exemplar_projection_policy,
    )
    from .single_branch_migration import validate_single_branch_migration
    from .split_freeze import (
        build_test_isolation_audit,
        validate_frozen_split,
        validate_test_isolation_audit,
        validate_transfer_panel_commitment,
    )
    from .validation import (
        build_validation_isolation_audit,
        validation_panel_commitment_digest,
        validate_validation_access_policy,
        validate_validation_isolation_audit,
    )
    from .workflow import validate_checkpoint_runtime

    provenance = _load_cumulative_governance_reference(
        root,
        document["initial_pack"]["provenance_audit"],
        schema_filename="s0_provenance_audit.schema.json",
        digest_field="audit_digest",
        label="C0 source-pack provenance audit",
    )
    transfer_policy_ref = panel["access_policy"]
    transfer_policy_path = root / safe_relative_path(transfer_policy_ref["path"])
    _verify_reference_bytes(transfer_policy_path, transfer_policy_ref)
    transfer_policy = validate_transfer_access_policy(transfer_policy_path)
    if transfer_policy["policy_digest"] != transfer_policy_ref["digest"]:
        raise CapabilityImprovementError(
            "transfer access-policy digest differs from the experiment reference"
        )
    validate_transfer_policy_panel_binding(
        panel_protocol_ids=panel["protocol_ids"], policy=transfer_policy
    )

    validation_policy_ref = validation_panel["access_policy"]
    validation_policy_path = root / safe_relative_path(validation_policy_ref["path"])
    _verify_reference_bytes(validation_policy_path, validation_policy_ref)
    validation_policy = validate_validation_access_policy(validation_policy_path)
    if validation_policy["policy_digest"] != validation_policy_ref["digest"]:
        raise CapabilityImprovementError(
            "validation access-policy digest differs from the experiment reference"
        )
    expected_validation_commitment = validation_panel_commitment_digest()
    if (
        validation_panel["commitment_sha256"] != expected_validation_commitment
        or validation_policy["validation_panel_commitment_sha256"]
        != expected_validation_commitment
    ):
        raise CapabilityImprovementError(
            "validation manifest and access policy bind different panels"
        )

    exemplar_policy_ref = memory_model["exemplar"]["policy"]
    exemplar_policy_path = root / safe_relative_path(exemplar_policy_ref["path"])
    _verify_reference_bytes(exemplar_policy_path, exemplar_policy_ref)
    exemplar_policy = validate_exemplar_projection_policy(
        exemplar_policy_path,
        experiment_root=root,
        frozen_split_path=(root / safe_relative_path(document["frozen_split"]["path"])),
    )
    exemplar_adoption_ref = memory_model["exemplar"]["adoption"]
    exemplar_adoption_path = root / safe_relative_path(exemplar_adoption_ref["path"])
    _verify_reference_bytes(exemplar_adoption_path, exemplar_adoption_ref)
    exemplar_adoption = validate_exemplar_memory_adoption(
        exemplar_adoption_path,
        experiment_root=root,
        projection_policy=exemplar_policy,
        c0_memory_root=root / "checkpoints" / "C0" / "memory",
        c0_pack_root=root / "checkpoints" / "C0" / "pack",
    )
    if (
        exemplar_policy["policy_digest"] != exemplar_policy_ref["digest"]
        or exemplar_adoption["adoption_digest"] != exemplar_adoption_ref["digest"]
        or exemplar_adoption["policy_digest"] != exemplar_policy["policy_digest"]
        or exemplar_adoption["identity_map_digest"]
        != exemplar_policy["identity_map"]["map_digest"]
        or exemplar_adoption["identity_map_sha256"]
        != exemplar_policy["identity_map"]["map_sha256"]
        or exemplar_adoption["identity_map_public_commitment_sha256"]
        != exemplar_policy["identity_map"]["public_commitment_sha256"]
        or memory_model["exemplar"]["identity_map_public_commitment_sha256"]
        != exemplar_policy["identity_map"]["public_commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "active exemplar policy, adoption, identity map, and manifest differ"
        )

    frozen_split_ref = document["frozen_split"]
    frozen_split_path = root / safe_relative_path(frozen_split_ref["path"])
    _verify_reference_bytes(frozen_split_path, frozen_split_ref)
    frozen_split = validate_frozen_split(frozen_split_path)
    if frozen_split["split_manifest_digest"] != frozen_split_ref["digest"]:
        raise CapabilityImprovementError(
            "frozen-split digest differs from the experiment reference"
        )
    if (
        frozen_split["development_batches"]
        != [_checkpoint_batch_signature(item) for item in document["batches"]]
        or frozen_split["validation_panel"]["protocol_ids"]
        != validation_panel["protocol_ids"]
        or frozen_split["final_test_panel"]["protocol_ids"] != panel["protocol_ids"]
    ):
        raise CapabilityImprovementError(
            "frozen split differs from the active experiment partition"
        )

    commitment_ref = document["transfer_panel_commitment"]
    commitment_path = root / safe_relative_path(commitment_ref["path"])
    _verify_reference_bytes(commitment_path, commitment_ref)
    commitment = validate_transfer_panel_commitment(commitment_path)
    if (
        commitment["commitment_manifest_digest"] != commitment_ref["digest"]
        or commitment["commitment_sha256"] != panel["commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "transfer-panel commitment differs from the active experiment"
        )

    test_audit_ref = document["test_isolation_audit"]
    test_audit_path = root / safe_relative_path(test_audit_ref["path"])
    _verify_reference_bytes(test_audit_path, test_audit_ref)
    test_audit = validate_test_isolation_audit(test_audit_path)
    if (
        test_audit["audit_digest"] != test_audit_ref["digest"]
        or test_audit["learning_isolation"] != "pass"
        or test_audit["development_access_isolation"] != "pass"
    ):
        raise CapabilityImprovementError(
            "active experiment requires a passing final-test isolation audit"
        )
    recomputed_test_audit = build_test_isolation_audit(
        experiment_root=root,
        active_batches=document["batches"],
        audited_at=test_audit["audited_at"],
        transfer_access_policy=transfer_policy,
    )
    if recomputed_test_audit != test_audit:
        raise CapabilityImprovementError(
            "final-test isolation audit does not match active closed-world artifacts"
        )

    validation_audit_ref = document["validation_isolation_audit"]
    validation_audit_path = root / safe_relative_path(validation_audit_ref["path"])
    _verify_reference_bytes(validation_audit_path, validation_audit_ref)
    validation_audit = validate_validation_isolation_audit(
        validation_audit_path,
        validation_access_policy=validation_policy,
    )
    if (
        validation_audit["audit_digest"] != validation_audit_ref["digest"]
        or validation_audit["learning_isolation"] != "pass"
    ):
        raise CapabilityImprovementError(
            "active experiment requires a passing validation-isolation audit"
        )
    recomputed_validation_audit = build_validation_isolation_audit(
        experiment_root=root,
        validation_access_policy=validation_policy,
        audited_at=validation_audit["audited_at"],
    )
    if recomputed_validation_audit != validation_audit:
        raise CapabilityImprovementError(
            "validation-isolation audit does not match active closed-world artifacts"
        )

    migration_ref = document["single_branch_migration"]
    migration_path = root / safe_relative_path(migration_ref["path"])
    _verify_reference_bytes(migration_path, migration_ref)
    migration = validate_single_branch_migration(migration_path, experiment_root=root)
    if (
        migration["migration_digest"] != migration_ref["digest"]
        or migration["new_split_digest"] != frozen_split["split_digest"]
        or migration["validation_access_policy_digest"]
        != validation_policy["policy_digest"]
        or migration["validation_isolation_audit_digest"]
        != validation_audit["audit_digest"]
        or migration["final_test_panel_commitment_sha256"] != panel["commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "single-branch migration is detached from active governance"
        )

    supersession = _load_cumulative_governance_reference(
        root,
        document["supersession"],
        schema_filename="supersession_manifest.schema.json",
        digest_field="supersession_digest",
        label="original experiment supersession",
    )
    if supersession["supersession_digest"] != migration["prior_supersession_digest"]:
        raise CapabilityImprovementError(
            "supersession and single-branch migration bind different "
            "historical lineage records"
        )
    c0_relative = document["initial_pack"]["references"]["C0"]
    if c0_relative != "checkpoints/C0/pack":
        raise CapabilityImprovementError("C0 pack reference is not canonical")
    c0_root = root / "checkpoints" / "C0"
    checkpoint, _, pack = validate_checkpoint_runtime(c0_root)
    if (
        checkpoint["experiment_digest"] != document["experiment_digest"]
        or checkpoint["pack_digest"] != document["initial_pack"]["pack_digest"]
        or checkpoint["pack_manifest_sha256"]
        != document["initial_pack"]["manifest_sha256"]
        or pack["pack_digest"] != provenance["pack_digest"]
    ):
        raise CapabilityImprovementError(
            "C0 checkpoint, source provenance, and experiment initial pack differ"
        )


def _verify_reference_bytes(path: Path, reference: Mapping[str, Any]) -> None:
    if sha256_file(path) != reference["sha256"]:
        raise CapabilityImprovementError(
            f"experiment governance artifact changed: {reference['path']}"
        )


def _load_cumulative_governance_reference(
    root: Path,
    reference: Mapping[str, Any],
    *,
    schema_filename: str,
    digest_field: str,
    label: str,
) -> dict[str, Any]:
    artifact_path = root / safe_relative_path(reference["path"])
    _verify_reference_bytes(artifact_path, reference)
    artifact = load_and_validate(
        artifact_path,
        schema_filename=schema_filename,
        digest_field=digest_field,
        label=label,
    )
    if artifact[digest_field] != reference["digest"]:
        raise CapabilityImprovementError(
            f"experiment governance digest changed: {reference['path']}"
        )
    return artifact


def _validate_retired_experiment_manifest_implementation(
    path: Path,
    *,
    experiment_root: Path | None = None,
) -> dict[str, Any]:
    document = load_and_validate(
        path,
        schema_filename="experiment_manifest.schema.json",
        digest_field="experiment_digest",
        label="capability experiment manifest",
    )
    panel = document["frozen_retrospective_transfer_panel"]
    if "baseline_registry" in panel:
        validate_initial_partition(document["batches"], panel["protocol_ids"])
    else:
        validate_fixed_partition(document["batches"], panel["protocol_ids"])
    for batch in document["batches"]:
        commitment = batch["groundtruth_commitment_sha256"]
        if batch["phase"] == "retrospective" and commitment is not None:
            raise CapabilityImprovementError(
                f"retrospective batch unexpectedly contains a private truth commitment: {batch['batch_id']}"
            )
        if batch["phase"] == "prospective" and commitment is None:
            raise CapabilityImprovementError(
                f"prospective batch lacks a frozen truth commitment: {batch['batch_id']}"
            )
    truth = document["prospective_groundtruth"]
    expected_status = (
        "current_schema_valid"
        if truth["current_schema_valid_count"] == 20
        else "hash_pinned_schema_migration_required"
    )
    if truth["compatibility_status"] != expected_status:
        raise CapabilityImprovementError(
            "prospective ground-truth compatibility status is inconsistent"
        )
    if tuple(document["policies"]["checkpoint_labels"]) != CHECKPOINT_LABELS:
        raise CapabilityImprovementError(
            "checkpoint labels differ from the frozen design"
        )
    if tuple(document["policies"]["endpoint_labels"]) != ENDPOINT_LABELS:
        raise CapabilityImprovementError(
            "endpoint labels differ from the frozen design"
        )
    root = experiment_root or path.expanduser().resolve().parents[1]
    refs = document["initial_pack"]["references"]
    packs = [root / refs[label] for label in ("S0", "A0", "H0")]
    manifests = [validate_capability_pack(pack) for pack in packs]
    if {item["pack_digest"] for item in manifests} != {
        document["initial_pack"]["pack_digest"]
    }:
        raise CapabilityImprovementError(
            "initial pack references have different digests"
        )
    if not (
        trees_byte_identical(packs[0], packs[1])
        and trees_byte_identical(packs[0], packs[2])
    ):
        raise CapabilityImprovementError("S0, A0, and H0 are not byte-identical")
    governance_references: list[tuple[Mapping[str, Any], str, str]] = [
        (
            document["initial_pack"]["provenance_audit"],
            "audit_digest",
            "s0_provenance_audit.schema.json",
        ),
        (panel["access_policy"], "policy_digest", "transfer_access_policy.schema.json"),
        (
            document["supersession"],
            "supersession_digest",
            "supersession_manifest.schema.json",
        ),
    ]
    if "baseline_registry" in panel:
        governance_references.append(
            (
                panel["baseline_registry"],
                "registry_digest",
                "baseline_registry.schema.json",
            )
        )
    for field, digest_field, schema in governance_references:
        artifact_path = root / field["path"]
        if sha256_file(artifact_path) != field["sha256"]:
            raise CapabilityImprovementError(
                f"experiment governance artifact changed: {field['path']}"
            )
        value = load_and_validate(
            artifact_path,
            schema_filename=schema,
            digest_field=digest_field,
            label="capability experiment governance artifact",
        )
        if value[digest_field] != field["digest"]:
            raise CapabilityImprovementError(
                f"experiment governance digest changed: {field['path']}"
            )
    policy = load_and_validate(
        root / panel["access_policy"]["path"],
        schema_filename="transfer_access_policy.schema.json",
        digest_field="policy_digest",
        label="transfer-panel access policy",
    )
    if policy["panel_commitment_sha256"] != panel["commitment_sha256"]:
        raise CapabilityImprovementError("transfer access policy covers another panel")
    from .governance import validate_transfer_policy_panel_binding

    validate_transfer_policy_panel_binding(
        panel_protocol_ids=panel["protocol_ids"],
        policy=policy,
    )
    if "baseline_registry" in panel:
        registry = load_and_validate(
            root / panel["baseline_registry"]["path"],
            schema_filename="baseline_registry.schema.json",
            digest_field="registry_digest",
            label="baseline registry",
        )
        if registry["panel_commitment_sha256"] != panel["commitment_sha256"]:
            raise CapabilityImprovementError("baseline registry covers another panel")
    else:
        if panel.get("baseline_mode") != "post_lock_s0_replay":
            raise CapabilityImprovementError(
                "baseline-free transfer panel must use post-lock S0 replay"
            )
        expected_strata = [
            {"name": name, "protocol_ids": list(protocol_ids)}
            for name, protocol_ids in TRANSFER_STRATA.items()
        ]
        if panel.get("transfer_strata") != expected_strata:
            raise CapabilityImprovementError(
                "transfer strata differ from the frozen experiment design"
            )
        expected_annotations = [dict(item) for item in TRANSFER_ANNOTATIONS]
        if panel.get("transfer_annotations") != expected_annotations:
            raise CapabilityImprovementError(
                "transfer annotations differ from the frozen experiment design"
            )
        if panel.get("purposes") != list(TRANSFER_PURPOSE):
            raise CapabilityImprovementError(
                "transfer purposes differ from the frozen experiment design"
            )
        commitment_reference = document.get("transfer_panel_commitment")
        if not isinstance(commitment_reference, Mapping):
            raise CapabilityImprovementError(
                "active experiment lacks transfer_panel_commitment governance reference"
            )
        commitment_path = root / commitment_reference["path"]
        if sha256_file(commitment_path) != commitment_reference["sha256"]:
            raise CapabilityImprovementError(
                "experiment governance artifact changed: "
                f"{commitment_reference['path']}"
            )
        from .split_freeze import validate_transfer_panel_commitment

        transfer_commitment = validate_transfer_panel_commitment(commitment_path)
        if (
            transfer_commitment["commitment_manifest_digest"]
            != commitment_reference["digest"]
        ):
            raise CapabilityImprovementError(
                f"experiment governance digest changed: {commitment_reference['path']}"
            )
        panel_commitment_semantic = {
            "set_id": panel["set_id"],
            "protocol_ids": panel["protocol_ids"],
            "transfer_strata": panel["transfer_strata"],
            "transfer_annotations": panel["transfer_annotations"],
            "purposes": panel["purposes"],
        }
        if canonical_digest(panel_commitment_semantic) != panel["commitment_sha256"]:
            raise CapabilityImprovementError(
                "transfer-panel commitment does not bind the frozen annotations"
            )
        for field_name in (
            "set_id",
            "protocol_ids",
            "transfer_strata",
            "transfer_annotations",
            "purposes",
            "commitment_sha256",
        ):
            if transfer_commitment[field_name] != panel[field_name]:
                raise CapabilityImprovementError(
                    "standalone transfer-panel commitment differs from the active "
                    f"experiment: {field_name}"
                )
        for field_name, digest_field, schema_name in (
            ("frozen_split", "split_manifest_digest", "frozen_split.schema.json"),
            (
                "test_isolation_audit",
                "audit_digest",
                "test_isolation_audit.schema.json",
            ),
            (
                "checkpoint_reattestation",
                "reattestation_digest",
                "checkpoint_reattestation.schema.json",
            ),
            (
                "split_supersession",
                "supersession_digest",
                "split_supersession.schema.json",
            ),
        ):
            reference = document.get(field_name)
            if not isinstance(reference, Mapping):
                raise CapabilityImprovementError(
                    f"active experiment lacks {field_name} governance reference"
                )
            artifact_path = root / reference["path"]
            if sha256_file(artifact_path) != reference["sha256"]:
                raise CapabilityImprovementError(
                    f"experiment governance artifact changed: {reference['path']}"
                )
            artifact = load_and_validate(
                artifact_path,
                schema_filename=schema_name,
                digest_field=digest_field,
                label="capability experiment split governance artifact",
            )
            if artifact[digest_field] != reference["digest"]:
                raise CapabilityImprovementError(
                    f"experiment governance digest changed: {reference['path']}"
                )
        reattestation = validate_checkpoint_reattestation(
            root / document["checkpoint_reattestation"]["path"],
            experiment_manifest=document,
            experiment_root=root,
        )
        from .split_freeze import build_test_isolation_audit, validate_frozen_split

        frozen_split = validate_frozen_split(root / document["frozen_split"]["path"])
        if frozen_split["status"] != "active":
            raise CapabilityImprovementError(
                "the active experiment references a non-active frozen split"
            )
        isolation_audit = load_and_validate(
            root / document["test_isolation_audit"]["path"],
            schema_filename="test_isolation_audit.schema.json",
            digest_field="audit_digest",
            label="test-isolation audit",
        )
        recomputed_isolation = build_test_isolation_audit(
            experiment_root=root,
            active_batches=document["batches"],
            audited_at=isolation_audit["audited_at"],
            transfer_access_policy=policy,
        )
        if recomputed_isolation != isolation_audit:
            raise CapabilityImprovementError(
                "test-isolation audit does not match the active closed-world artifacts"
            )
        split_supersession = load_and_validate(
            root / document["split_supersession"]["path"],
            schema_filename="split_supersession.schema.json",
            digest_field="supersession_digest",
            label="split supersession",
        )
        validate_split_governance_links(
            experiment_manifest=document,
            frozen_split=frozen_split,
            isolation_audit=isolation_audit,
            checkpoint_reattestation=reattestation,
            split_supersession=split_supersession,
        )
        validate_split_archive_lineage(
            experiment_root=root,
            experiment_manifest=document,
            split_supersession=split_supersession,
            checkpoint_reattestation=reattestation,
        )
    restart_path = root / "design" / "lineage_restart.json"
    restart_archive = (
        root / "history" / "superseded" / "pre-deterministic-rewrite" / "root"
    )
    restart_reference = document.get("lineage_restart")
    if restart_reference is None and (
        restart_path.exists() or restart_archive.exists()
    ):
        raise CapabilityImprovementError(
            "lineage restart content is detached from the experiment manifest"
        )
    if restart_reference is not None:
        from .revision import validate_lineage_restart_history

        validate_lineage_restart_history(
            root,
            active_manifest=document,
        )
    return document


def validate_split_governance_links(
    *,
    experiment_manifest: Mapping[str, Any],
    frozen_split: Mapping[str, Any],
    isolation_audit: Mapping[str, Any],
    checkpoint_reattestation: Mapping[str, Any],
    split_supersession: Mapping[str, Any],
) -> None:
    """Reject independently valid split-governance artifacts that do not agree."""

    manifest_batches = [
        _checkpoint_batch_signature(item) for item in experiment_manifest["batches"]
    ]
    panel = experiment_manifest["frozen_retrospective_transfer_panel"]
    split_panel = frozen_split["final_test_panel"]
    if frozen_split["development_batches"] != manifest_batches:
        raise CapabilityImprovementError(
            "frozen split development batches differ from the active experiment"
        )
    if (
        split_panel["panel_id"] != panel["set_id"]
        or split_panel["protocol_ids"] != panel["protocol_ids"]
        or split_panel["transfer_strata"] != panel["transfer_strata"]
        or split_panel["transfer_annotations"] != panel["transfer_annotations"]
        or split_panel["purposes"] != panel["purposes"]
    ):
        raise CapabilityImprovementError(
            "frozen split test panel differs from the active experiment"
        )
    if isolation_audit["protocol_ids"] != panel["protocol_ids"]:
        raise CapabilityImprovementError(
            "test-isolation audit covers another frozen test panel"
        )
    expected = {
        "prior_experiment_digest": checkpoint_reattestation["prior_experiment_digest"],
        "old_split_digest": checkpoint_reattestation["prior_split_digest"],
        "new_split_digest": frozen_split["split_digest"],
        "test_isolation_audit_digest": isolation_audit["audit_digest"],
        "checkpoint_reattestation_digest": checkpoint_reattestation[
            "reattestation_digest"
        ],
    }
    for field, value in expected.items():
        if split_supersession[field] != value:
            raise CapabilityImprovementError(f"split supersession has stale {field}")
    if (
        checkpoint_reattestation["active_split"]["digest"]
        != frozen_split["split_manifest_digest"]
        or checkpoint_reattestation["active_split"]["split_digest"]
        != frozen_split["split_digest"]
        or checkpoint_reattestation["test_isolation_audit"]["digest"]
        != isolation_audit["audit_digest"]
    ):
        raise CapabilityImprovementError(
            "checkpoint re-attestation is detached from active split governance"
        )


def validate_split_archive_lineage(
    *,
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
    split_supersession: Mapping[str, Any],
    checkpoint_reattestation: Mapping[str, Any],
) -> None:
    """Verify every byte claimed by the superseded split archive."""

    from .split_freeze import validate_superseded_frozen_split

    root = experiment_root.expanduser().resolve()
    archive_relative = safe_relative_path(split_supersession["archive_root"])
    expected_supersession_path = archive_relative / "split_supersession.json"
    reference = experiment_manifest.get("split_supersession")
    if (
        not isinstance(reference, Mapping)
        or safe_relative_path(str(reference.get("path", "")))
        != expected_supersession_path
    ):
        raise CapabilityImprovementError(
            "split supersession reference differs from its archive root"
        )
    archive = root / archive_relative
    if archive.is_symlink() or not archive.is_dir():
        raise CapabilityImprovementError(
            f"split supersession archive is missing or unsafe: {archive}"
        )

    expected_design: dict[str, str] = {}
    design_prefix = archive_relative / "design"
    for item in split_supersession["archived_design_artifacts"]:
        relative = safe_relative_path(item["path"])
        if not relative.is_relative_to(design_prefix):
            raise CapabilityImprovementError(
                "archived design artifact lies outside the superseded design tree"
            )
        key = relative.as_posix()
        if key in expected_design:
            raise CapabilityImprovementError(
                f"split supersession repeats archived design artifact: {key}"
            )
        expected_design[key] = item["sha256"]
    actual_design = _archived_file_inventory(
        root / design_prefix,
        experiment_root=root,
    )
    if set(actual_design) != set(expected_design):
        raise CapabilityImprovementError(
            "archived design artifact inventory differs from split supersession"
        )
    for relative, expected_sha256 in expected_design.items():
        if actual_design[relative]["sha256"] != expected_sha256:
            raise CapabilityImprovementError(
                f"archived design artifact changed: {relative}"
            )

    prior_manifest_path = root / design_prefix / "experiment_manifest.json"
    try:
        prior_manifest = json.loads(prior_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read archived prior experiment manifest: {error}"
        ) from error
    if not isinstance(prior_manifest, dict):
        raise CapabilityImprovementError(
            "archived prior experiment manifest must be an object"
        )
    validate_digest(prior_manifest, "experiment_digest")
    prior_digest = prior_manifest["experiment_digest"]
    if (
        prior_digest != split_supersession["prior_experiment_digest"]
        or prior_digest != checkpoint_reattestation["prior_experiment_digest"]
    ):
        raise CapabilityImprovementError(
            "archived prior experiment digest differs from split lineage"
        )
    prior_panel = prior_manifest.get("frozen_retrospective_transfer_panel")
    if (
        not isinstance(prior_panel, Mapping)
        or prior_panel.get("commitment_sha256")
        != split_supersession["prior_panel_commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "archived prior panel commitment differs from split supersession"
        )

    old_split_path = archive / "frozen_split.json"
    if old_split_path.is_symlink():
        raise CapabilityImprovementError("archived frozen split may not be a symlink")
    old_split = validate_superseded_frozen_split(old_split_path)
    if old_split["status"] != "superseded":
        raise CapabilityImprovementError(
            "archived frozen split must have superseded status"
        )
    if (
        old_split["split_digest"] != split_supersession["old_split_digest"]
        or old_split["split_digest"] != checkpoint_reattestation["prior_split_digest"]
    ):
        raise CapabilityImprovementError(
            "archived frozen split digest differs from split lineage"
        )
    prior_batches = [
        _checkpoint_batch_signature(item) for item in prior_manifest.get("batches", ())
    ]
    if old_split["development_batches"] != prior_batches:
        raise CapabilityImprovementError(
            "archived frozen split batches differ from the prior experiment"
        )
    if old_split["final_test_panel"]["protocol_ids"] != list(
        prior_panel.get("protocol_ids", ())
    ):
        raise CapabilityImprovementError(
            "archived frozen split panel differs from the prior experiment"
        )

    rounds = _archived_file_inventory(
        archive / "rounds",
        experiment_root=archive / "rounds",
        allow_missing=True,
    )
    round_records = [rounds[key] for key in sorted(rounds)]
    round_digest = canonical_digest(round_records) if round_records else None
    if (
        len(round_records) != split_supersession["archived_round_file_count"]
        or round_digest != split_supersession["archived_round_tree_digest"]
    ):
        raise CapabilityImprovementError(
            "archived round tree differs from split supersession"
        )


def _archived_file_inventory(
    tree: Path,
    *,
    experiment_root: Path,
    allow_missing: bool = False,
) -> dict[str, dict[str, Any]]:
    if tree.is_symlink():
        raise CapabilityImprovementError(f"archived tree may not be a symlink: {tree}")
    if not tree.exists():
        if allow_missing:
            return {}
        raise CapabilityImprovementError(f"archived tree is missing: {tree}")
    if not tree.is_dir():
        raise CapabilityImprovementError(f"archived tree is not a directory: {tree}")
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(tree.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"archived tree contains a symlink: {path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise CapabilityImprovementError(
                f"archived tree contains a non-regular entry: {path}"
            )
        relative = path.relative_to(experiment_root).as_posix()
        records[relative] = {
            "path": relative,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return records


def build_batch_packet(
    *,
    experiment_manifest: Mapping[str, Any],
    branch: str,
    batch_id: str,
    parent_pack_digest: str,
    reveal_state: str,
    artifacts: Sequence[Mapping[str, Any]],
    trial_terminality: Sequence[Mapping[str, Any]],
    transfer_access_policy: Mapping[str, Any],
) -> dict[str, Any]:
    validate_digest(experiment_manifest, "experiment_digest")
    batch = _batch(experiment_manifest, batch_id)
    if batch["phase"] == "prospective":
        assert_final_split_frozen(experiment_manifest)
    require_active_branch(branch)
    if batch["phase"] == "retrospective" and reveal_state != "revealed":
        raise CapabilityImprovementError("retrospective packets are already revealed")
    if reveal_state not in {"concealed", "revealed"}:
        raise CapabilityImprovementError("invalid batch reveal state")
    artifact_values = [dict(item) for item in artifacts]
    panel = experiment_manifest["frozen_retrospective_transfer_panel"]
    if transfer_access_policy.get("policy_digest") != panel["access_policy"]["digest"]:
        raise CapabilityImprovementError(
            "batch packet uses a stale transfer access policy"
        )
    from .governance import assert_transfer_panel_isolation

    assert_transfer_panel_isolation(
        protocol_ids=batch["protocol_ids"],
        artifacts=artifact_values,
        policy=transfer_access_policy,
    )
    _validate_packet_artifacts(batch["protocol_ids"], artifact_values)
    if reveal_state == "concealed" and any(
        item.get("role")
        in {
            "approved_groundtruth",
            "verifier_details",
            "verifier_error_analysis",
            "c0_verifier_reward",
            "c0_verifier_error_analysis",
            "discrepancy_ledger",
        }
        for item in artifact_values
    ):
        raise CapabilityImprovementError(
            "concealed packet exposes post-evaluation artifacts"
        )
    terminal_values = [dict(item) for item in trial_terminality]
    if batch["phase"] == "prospective" and reveal_state == "revealed":
        _validate_prospective_terminal_gate(batch["protocol_ids"], terminal_values)
        _validate_revealed_groundtruth_commitment(batch, artifact_values)
    from .learning_ledger import build_learning_ledger

    learning_ledger = build_learning_ledger(
        batch_id=batch_id,
        protocol_ids=batch["protocol_ids"],
        artifacts=artifact_values,
    )
    if reveal_state == "revealed" and learning_ledger is None:
        raise CapabilityImprovementError(
            "revealed capability packet requires verifier error analysis for "
            "deterministic learning-ledger construction"
        )
    if reveal_state == "concealed" and learning_ledger is not None:
        raise CapabilityImprovementError(
            "concealed capability packet cannot contain a learning ledger"
        )
    payload: dict[str, Any] = {
        "schema_version": BATCH_PACKET_SCHEMA_VERSION,
        "packet_id": f"{batch_id}:{branch}:{reveal_state}",
        "experiment_digest": experiment_manifest["experiment_digest"],
        "batch_id": batch_id,
        "branch": branch,
        "phase": batch["phase"],
        "parent_pack_digest": parent_pack_digest,
        "protocol_ids": list(batch["protocol_ids"]),
        "reveal_state": reveal_state,
        "eligibility_status": "eligible_for_improvement",
        "transfer_access_policy_digest": transfer_access_policy["policy_digest"],
        "artifacts": artifact_values,
        "trial_terminality": terminal_values,
        "learning_ledger": learning_ledger,
    }
    packet = with_digest(payload, "packet_digest")
    validate_document(
        packet,
        improvement_schema_root() / "batch_packet.schema.json",
        label="capability batch packet",
    )
    return packet


def build_checkpoint_reattestation(
    *,
    prior_experiment_manifest: Mapping[str, Any],
    active_batches: Sequence[Mapping[str, Any]],
    prior_split_digest: str,
    frozen_split_path: Path,
    test_isolation_audit_path: Path,
    checkpoint_paths: Sequence[Path],
    experiment_root: Path,
    created_at: str,
) -> dict[str, Any]:
    """Pin unchanged A5/A10 bytes across the one authorized split update.

    The document intentionally does not contain the active experiment digest:
    the active experiment manifest contains the re-attestation reference, so
    embedding that digest here would create a digest cycle.  The active split
    digest and unchanged B1/B2 records supply the scientific lineage binding;
    the final lock supplies the active experiment binding.
    """

    validate_digest(prior_experiment_manifest, "experiment_digest")
    if not re.fullmatch(r"[a-f0-9]{64}", prior_split_digest):
        raise CapabilityImprovementError("prior split digest is not a sha256")

    root = experiment_root.expanduser().resolve()
    split_path = frozen_split_path.expanduser().resolve()
    isolation_path = test_isolation_audit_path.expanduser().resolve()
    from .split_freeze import validate_frozen_split

    split = validate_frozen_split(split_path)
    if split["status"] != "active":
        raise CapabilityImprovementError(
            "checkpoint re-attestation requires an active frozen split"
        )
    isolation = load_and_validate(
        isolation_path,
        schema_filename="test_isolation_audit.schema.json",
        digest_field="audit_digest",
        label="test-isolation audit",
    )
    active_batch_signatures = [
        _checkpoint_batch_signature(item) for item in active_batches
    ]
    if split["development_batches"] != active_batch_signatures:
        raise CapabilityImprovementError(
            "frozen split development batches differ from active batches"
        )
    if isolation["protocol_ids"] != split["final_test_panel"]["protocol_ids"]:
        raise CapabilityImprovementError(
            "test-isolation audit covers another frozen test panel"
        )
    if (
        isolation.get("learning_isolation") != "pass"
        or isolation.get("development_access_isolation") != "pass"
    ):
        raise CapabilityImprovementError(
            "checkpoint re-attestation requires a passing test-isolation audit"
        )

    expected_artifact_paths = {
        split_path: "design/frozen_split.json",
        isolation_path: "design/test_isolation_audit.json",
    }
    for artifact_path, expected_relative in expected_artifact_paths.items():
        expected = Path(expected_relative)
        # During the atomic split transaction these two files live in a
        # same-filesystem staging tree.  Their bytes are pinned here and the
        # document records only their eventual canonical active paths.
        if (
            artifact_path.name != expected.name
            or artifact_path.parent.name != expected.parent.name
        ):
            raise CapabilityImprovementError(
                f"re-attestation artifact must use {expected_relative}: {artifact_path}"
            )

    prior_by_id = {
        item["batch_id"]: _checkpoint_batch_signature(item)
        for item in prior_experiment_manifest["batches"]
    }
    active_by_id = {
        item["batch_id"]: _checkpoint_batch_signature(item) for item in active_batches
    }
    unchanged_batches: list[dict[str, Any]] = []
    for batch_id in ("B1", "B2"):
        prior = prior_by_id.get(batch_id)
        active = active_by_id.get(batch_id)
        if prior is None or active is None or prior != active:
            raise CapabilityImprovementError(
                f"checkpoint re-attestation requires unchanged {batch_id}"
            )
        digest = canonical_digest(prior)
        unchanged_batches.append(
            {
                "batch_id": batch_id,
                "protocol_ids": list(prior["protocol_ids"]),
                "prior_batch_digest": digest,
                "active_batch_digest": digest,
            }
        )

    records: dict[str, dict[str, Any]] = {}
    for unresolved in checkpoint_paths:
        path = unresolved.expanduser().resolve()
        record = _validated_reattestation_checkpoint(
            path,
            experiment_root=root,
            prior_experiment_digest=prior_experiment_manifest["experiment_digest"],
        )
        checkpoint_id = record["checkpoint_id"]
        if checkpoint_id not in {"A5", "A10"}:
            raise CapabilityImprovementError(
                "split re-attestation is limited to A5 and A10"
            )
        if checkpoint_id in records:
            raise CapabilityImprovementError(
                "duplicate checkpoint in split re-attestation"
            )
        expected_path = root / "checkpoints" / checkpoint_id / "checkpoint.json"
        if path != expected_path:
            raise CapabilityImprovementError(
                f"checkpoint re-attestation requires canonical path: {expected_path}"
            )
        records[checkpoint_id] = record
    if set(records) != {"A5", "A10"}:
        raise CapabilityImprovementError(
            "checkpoint re-attestation requires exactly A5 and A10"
        )

    payload: dict[str, Any] = {
        "schema_version": CHECKPOINT_REATTESTATION_SCHEMA_VERSION,
        "reattestation_id": "A5-A10:final-split-reattestation",
        "prior_experiment_digest": prior_experiment_manifest["experiment_digest"],
        "prior_split_digest": prior_split_digest,
        "active_split": {
            "path": "design/frozen_split.json",
            "sha256": sha256_file(split_path),
            "digest": split["split_manifest_digest"],
            "split_digest": split["split_digest"],
        },
        "test_isolation_audit": {
            "path": "design/test_isolation_audit.json",
            "sha256": sha256_file(isolation_path),
            "digest": isolation["audit_digest"],
        },
        "unchanged_batches": unchanged_batches,
        "checkpoint_records": [records[item] for item in ("A5", "A10")],
        "attestations": {
            "checkpoint_bytes_unchanged": True,
            "development_batches_unchanged": True,
            "test_evidence_absent_from_checkpoint_lineage": True,
            "scope_limited_to_pre_split_checkpoints": True,
        },
        "created_at": normalized_timestamp(created_at),
    }
    result = with_digest(payload, "reattestation_digest")
    validate_document(
        result,
        improvement_schema_root() / "checkpoint_reattestation.schema.json",
        label="checkpoint split re-attestation",
    )
    return result


def validate_checkpoint_reattestation(
    path: Path,
    *,
    experiment_manifest: Mapping[str, Any],
    experiment_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the active split binding, isolation audit, and B1/B2 scope."""

    document = load_and_validate(
        path,
        schema_filename="checkpoint_reattestation.schema.json",
        digest_field="reattestation_digest",
        label="checkpoint split re-attestation",
    )
    root = (
        experiment_root.expanduser().resolve()
        if experiment_root is not None
        else path.expanduser().resolve().parents[1]
    )
    reference = experiment_manifest.get("checkpoint_reattestation")
    if not isinstance(reference, Mapping):
        raise CapabilityImprovementError(
            "active experiment does not reference checkpoint re-attestation"
        )
    _validate_governance_reference(
        reference,
        expected_path="design/checkpoint_reattestation.json",
        artifact_path=path.expanduser().resolve(),
        digest=document["reattestation_digest"],
        label="checkpoint re-attestation",
    )

    split_reference = experiment_manifest.get("frozen_split")
    isolation_reference = experiment_manifest.get("test_isolation_audit")
    if not isinstance(split_reference, Mapping) or not isinstance(
        isolation_reference, Mapping
    ):
        raise CapabilityImprovementError(
            "active experiment lacks split or isolation governance references"
        )
    if document["active_split"] != {
        **dict(split_reference),
        "split_digest": document["active_split"]["split_digest"],
    }:
        raise CapabilityImprovementError(
            "checkpoint re-attestation covers another active split artifact"
        )
    if document["test_isolation_audit"] != dict(isolation_reference):
        raise CapabilityImprovementError(
            "checkpoint re-attestation covers another isolation audit"
        )

    split_path = root / document["active_split"]["path"]
    from .split_freeze import validate_frozen_split

    split = validate_frozen_split(split_path)
    if split["status"] != "active":
        raise CapabilityImprovementError(
            "checkpoint re-attestation references a non-active frozen split"
        )
    if (
        sha256_file(split_path) != document["active_split"]["sha256"]
        or split["split_manifest_digest"] != document["active_split"]["digest"]
        or split["split_digest"] != document["active_split"]["split_digest"]
    ):
        raise CapabilityImprovementError(
            "checkpoint re-attestation has a stale active split"
        )
    manifest_batches = [
        _checkpoint_batch_signature(item) for item in experiment_manifest["batches"]
    ]
    if split["development_batches"] != manifest_batches:
        raise CapabilityImprovementError(
            "frozen split development batches differ from the active experiment"
        )
    panel = experiment_manifest["frozen_retrospective_transfer_panel"]
    split_panel = split["final_test_panel"]
    if (
        split_panel["panel_id"] != panel["set_id"]
        or split_panel["protocol_ids"] != panel["protocol_ids"]
        or split_panel["transfer_strata"] != panel["transfer_strata"]
        or split_panel["transfer_annotations"] != panel["transfer_annotations"]
        or split_panel["purposes"] != panel["purposes"]
    ):
        raise CapabilityImprovementError(
            "frozen split test panel differs from the active experiment"
        )
    isolation_path = root / document["test_isolation_audit"]["path"]
    isolation = load_and_validate(
        isolation_path,
        schema_filename="test_isolation_audit.schema.json",
        digest_field="audit_digest",
        label="test-isolation audit",
    )
    if (
        sha256_file(isolation_path) != document["test_isolation_audit"]["sha256"]
        or isolation["audit_digest"] != document["test_isolation_audit"]["digest"]
        or isolation["learning_isolation"] != "pass"
        or isolation["development_access_isolation"] != "pass"
    ):
        raise CapabilityImprovementError(
            "checkpoint re-attestation has a stale isolation audit"
        )
    if isolation["protocol_ids"] != panel["protocol_ids"]:
        raise CapabilityImprovementError(
            "test-isolation audit covers another frozen test panel"
        )

    active_by_id = {
        item["batch_id"]: _checkpoint_batch_signature(item)
        for item in experiment_manifest["batches"]
    }
    batch_records = {item["batch_id"]: item for item in document["unchanged_batches"]}
    if set(batch_records) != {"B1", "B2"}:
        raise CapabilityImprovementError(
            "checkpoint re-attestation must cover exactly B1 and B2"
        )
    for batch_id in ("B1", "B2"):
        signature = active_by_id.get(batch_id)
        record = batch_records[batch_id]
        digest = canonical_digest(signature) if signature is not None else None
        if (
            signature is None
            or record["protocol_ids"] != list(signature["protocol_ids"])
            or record["prior_batch_digest"] != digest
            or record["active_batch_digest"] != digest
        ):
            raise CapabilityImprovementError(
                f"checkpoint re-attestation has stale {batch_id} membership"
            )
    records = {item["checkpoint_id"]: item for item in document["checkpoint_records"]}
    if set(records) != {"A5", "A10"}:
        raise CapabilityImprovementError(
            "checkpoint re-attestation must pin exactly A5 and A10"
        )
    if {item["original_experiment_digest"] for item in records.values()} != {
        document["prior_experiment_digest"]
    }:
        raise CapabilityImprovementError(
            "checkpoint re-attestation mixes prior experiment lineages"
        )
    for record in records.values():
        actual = _validated_reattestation_checkpoint(
            root / record["path"],
            experiment_root=root,
            prior_experiment_digest=document["prior_experiment_digest"],
        )
        if actual != record:
            raise CapabilityImprovementError(
                f"checkpoint runtime or pack changed after re-attestation: {record['checkpoint_id']}"
            )
    return document


def _checkpoint_batch_signature(batch: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "batch_id": batch.get("batch_id"),
        "phase": batch.get("phase"),
        "checkpoint_size": batch.get("checkpoint_size"),
        "protocol_ids": list(batch.get("protocol_ids", ())),
    }


def _validated_reattestation_checkpoint(
    path: Path,
    *,
    experiment_root: Path,
    prior_experiment_digest: str,
) -> dict[str, Any]:
    from .workflow import validate_checkpoint_runtime

    resolved = path.expanduser().resolve()
    root = experiment_root.expanduser().resolve()
    checkpoint, runtime, pack = validate_checkpoint_runtime(resolved.parent)
    checkpoint_id = checkpoint["checkpoint_id"]
    expected_path = root / "checkpoints" / checkpoint_id / "checkpoint.json"
    if resolved != expected_path:
        raise CapabilityImprovementError(
            f"checkpoint re-attestation requires canonical path: {expected_path}"
        )
    count = int(checkpoint_id[1:])
    expected_batch = f"B{count // 5}"
    if (
        checkpoint["experiment_digest"] != prior_experiment_digest
        or checkpoint["branch"] != "autonomous"
        or checkpoint["protocol_count"] != count
        or checkpoint["batch_id"] != expected_batch
    ):
        raise CapabilityImprovementError(
            f"checkpoint {checkpoint_id} has stale pre-split lineage"
        )
    runtime_path = resolved.parent / "runtime.json"
    manifest_path = resolved.parent / "pack" / "manifest.json"
    return {
        "checkpoint_id": checkpoint_id,
        "path": resolved.relative_to(root).as_posix(),
        "sha256": sha256_file(resolved),
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "original_experiment_digest": checkpoint["experiment_digest"],
        "batch_id": checkpoint["batch_id"],
        "protocol_count": checkpoint["protocol_count"],
        "runtime_sha256": sha256_file(runtime_path),
        "runtime_digest": runtime["runtime_digest"],
        "pack_digest": checkpoint["pack_digest"],
        "pack_manifest_sha256": sha256_file(manifest_path),
        "pack_tree_digest": _verified_pack_tree_digest(pack),
    }


def _verified_pack_tree_digest(pack: Mapping[str, Any]) -> str:
    return canonical_digest(
        {
            "pack_digest": pack["pack_digest"],
            "files": pack["files"],
        }
    )


def _validate_governance_reference(
    reference: Mapping[str, Any],
    *,
    expected_path: str,
    artifact_path: Path,
    digest: str,
    label: str,
) -> None:
    if (
        reference.get("path") != expected_path
        or reference.get("sha256") != sha256_file(artifact_path)
        or reference.get("digest") != digest
    ):
        raise CapabilityImprovementError(f"active {label} reference is stale")


def build_final_lock(
    *,
    experiment_manifest: Mapping[str, Any],
    checkpoint_paths: Sequence[Path],
    created_at: str,
) -> dict[str, Any]:
    validate_digest(experiment_manifest, "experiment_digest")
    assert_final_split_frozen(experiment_manifest)
    resolved_paths = [path.expanduser().resolve() for path in checkpoint_paths]
    if any(
        path.name != "checkpoint.json" or path.parent.parent.name != "checkpoints"
        for path in resolved_paths
    ):
        raise CapabilityImprovementError(
            "final lock inputs must be canonical checkpoint.json files"
        )
    checkpoint_roots = {path.parents[2] for path in resolved_paths}
    if len(checkpoint_roots) != 1:
        raise CapabilityImprovementError(
            "final-lock checkpoints must share one experiment root"
        )
    experiment_root = next(iter(checkpoint_roots))
    from .workflow import validate_checkpoint_runtime

    records: dict[str, dict[str, Any]] = {}
    for resolved_path in resolved_paths:
        checkpoint, _, _ = validate_checkpoint_runtime(resolved_path.parent)
        expected_path = (
            experiment_root
            / "checkpoints"
            / checkpoint["checkpoint_id"]
            / "checkpoint.json"
        )
        if resolved_path != expected_path:
            raise CapabilityImprovementError(
                "final lock input is not the canonical checkpoint marker: "
                f"{resolved_path}"
            )
        if checkpoint["checkpoint_id"] in records:
            raise CapabilityImprovementError("duplicate checkpoint in final lock")
        if checkpoint["experiment_digest"] != experiment_manifest["experiment_digest"]:
            raise CapabilityImprovementError(
                f"checkpoint belongs to another experiment: {checkpoint['checkpoint_id']}"
            )
        records[checkpoint["checkpoint_id"]] = {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_sha256": sha256_file(resolved_path),
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "pack_digest": checkpoint["pack_digest"],
        }
    if set(records) != set(EXPECTED_CHECKPOINTS):
        raise CapabilityImprovementError(
            "final lock requires exactly " + ", ".join(EXPECTED_CHECKPOINTS)
        )
    validation_reference = experiment_manifest["validation_panel"]["access_policy"]
    from .validation import (
        validate_complete_validation_curve,
        validate_validation_access_policy,
    )

    validation_policy = validate_validation_access_policy(
        experiment_root / validation_reference["path"],
    )
    validation_curve = validate_complete_validation_curve(
        experiment_root=experiment_root,
        experiment_digest=experiment_manifest["experiment_digest"],
        validation_access_policy=validation_policy,
    )
    for aggregate_record in validation_curve["aggregate_records"]:
        checkpoint_record = records[aggregate_record["checkpoint_label"]]
        if aggregate_record["pack_digest"] != checkpoint_record["pack_digest"]:
            raise CapabilityImprovementError(
                "validation aggregate references another checkpoint pack: "
                f"{aggregate_record['checkpoint_label']}"
            )
    payload: dict[str, Any] = {
        "schema_version": FINAL_LOCK_SCHEMA_VERSION,
        "lock_id": experiment_manifest["experiment_id"] + ":final-lock",
        "experiment_digest": experiment_manifest["experiment_digest"],
        "checkpoint_records": [records[item] for item in EXPECTED_CHECKPOINTS],
        "validation_curve": validation_curve,
        "checkpoint_labels": list(CHECKPOINT_LABELS),
        "endpoint_labels": list(ENDPOINT_LABELS),
        "transfer_panel_commitment_sha256": experiment_manifest[
            "frozen_retrospective_transfer_panel"
        ]["commitment_sha256"],
        "baseline_mode": "post_lock_c0_replay",
        "primary_outcome": "t3_molecular_transition_f1",
        "checkpoint_modification_closed": True,
        "created_at": normalized_timestamp(created_at),
    }
    result = with_digest(payload, "lock_digest")
    validate_document(
        result,
        improvement_schema_root() / "final_lock.schema.json",
        label="capability final lock",
    )
    return result


def validate_final_lock(
    path: Path,
    *,
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    expected_path = root / "design" / "final_lock.json"
    if resolved_path != expected_path:
        raise CapabilityImprovementError(
            f"final lock must be the canonical experiment marker: {expected_path}"
        )
    lock = load_and_validate(
        resolved_path,
        schema_filename="final_lock.schema.json",
        digest_field="lock_digest",
        label="capability final lock",
    )
    assert_final_split_frozen(experiment_manifest)
    if lock["experiment_digest"] != experiment_manifest["experiment_digest"]:
        raise CapabilityImprovementError("final lock belongs to another experiment")
    panel = experiment_manifest["frozen_retrospective_transfer_panel"]
    if lock["transfer_panel_commitment_sha256"] != panel["commitment_sha256"]:
        raise CapabilityImprovementError("final lock covers another transfer panel")
    if lock["baseline_mode"] != _panel_baseline_mode(panel):
        raise CapabilityImprovementError("final lock uses another baseline mode")
    if tuple(lock["checkpoint_labels"]) != CHECKPOINT_LABELS:
        raise CapabilityImprovementError("final lock changes checkpoint labels")
    if tuple(lock["endpoint_labels"]) != ENDPOINT_LABELS:
        raise CapabilityImprovementError("final lock changes endpoint labels")
    from .workflow import validate_checkpoint_runtime

    observed: set[str] = set()
    for record in lock["checkpoint_records"]:
        checkpoint_id = record["checkpoint_id"]
        checkpoint_path = root / "checkpoints" / checkpoint_id / "checkpoint.json"
        checkpoint, _, _ = validate_checkpoint_runtime(checkpoint_path.parent)
        expected = {
            "checkpoint_id": checkpoint_id,
            "checkpoint_sha256": sha256_file(checkpoint_path),
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "pack_digest": checkpoint["pack_digest"],
        }
        if (
            record != expected
            or checkpoint["experiment_digest"] != lock["experiment_digest"]
        ):
            raise CapabilityImprovementError(
                f"final lock has a stale checkpoint record: {checkpoint_id}"
            )
        observed.add(checkpoint_id)
    if observed != set(EXPECTED_CHECKPOINTS):
        raise CapabilityImprovementError(
            "final lock does not contain the exact cumulative checkpoint chain"
        )
    from .validation import (
        validate_complete_validation_curve,
        validate_validation_access_policy,
    )

    validation_reference = experiment_manifest["validation_panel"]["access_policy"]
    validation_policy = validate_validation_access_policy(
        root / validation_reference["path"],
    )
    current_curve = validate_complete_validation_curve(
        experiment_root=root,
        experiment_digest=experiment_manifest["experiment_digest"],
        validation_access_policy=validation_policy,
    )
    if lock["validation_curve"] != current_curve:
        raise CapabilityImprovementError(
            "final lock has a stale or incomplete validation curve"
        )
    pack_digests = {
        item["checkpoint_id"]: item["pack_digest"]
        for item in lock["checkpoint_records"]
    }
    if any(
        record["pack_digest"] != pack_digests[record["checkpoint_label"]]
        for record in current_curve["aggregate_records"]
    ):
        raise CapabilityImprovementError(
            "final lock validation curve references another checkpoint pack"
        )
    return lock


def assert_final_split_frozen(experiment_manifest: Mapping[str, Any]) -> None:
    """Fail closed unless the active 25/5/10 split is fully frozen."""

    validate_digest(experiment_manifest, "experiment_digest")
    panel = experiment_manifest.get("frozen_retrospective_transfer_panel")
    expected_batches = [_checkpoint_batch_signature(batch) for batch in FIXED_BATCHES]
    observed_batches = [
        _checkpoint_batch_signature(batch)
        for batch in experiment_manifest.get("batches", ())
        if isinstance(batch, Mapping)
    ]
    required_references = (
        "transfer_panel_commitment",
        "frozen_split",
        "test_isolation_audit",
        "validation_isolation_audit",
        "single_branch_migration",
    )
    validation = experiment_manifest.get("validation_panel")
    if (
        not isinstance(panel, Mapping)
        or panel.get("baseline_mode") != "post_lock_c0_replay"
        or tuple(panel.get("protocol_ids", ())) != FROZEN_RETROSPECTIVE_TRANSFER_PANEL
        or panel.get("transfer_annotations")
        != [dict(item) for item in TRANSFER_ANNOTATIONS]
        or not isinstance(validation, Mapping)
        or tuple(validation.get("protocol_ids", ())) != FIXED_VALIDATION_PANEL
        or tuple(validation.get("evaluation_checkpoints", ())) != CHECKPOINT_LABELS
        or observed_batches != expected_batches
        or any(
            not isinstance(experiment_manifest.get(field), Mapping)
            for field in required_references
        )
    ):
        raise CapabilityImprovementError(
            "the 25/5/10 development split must be frozen before B4-B5 "
            "access or final lock/replay"
        )


def build_transfer_panel_authorization(
    *,
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
    final_lock_path: Path,
    authorized_by: str,
    authorized_at: str,
) -> dict[str, Any]:
    """Authorize the fixed-panel learning-curve replay after checkpoint freeze."""

    lock = validate_final_lock(
        final_lock_path,
        experiment_root=experiment_root,
        experiment_manifest=experiment_manifest,
    )
    payload: dict[str, Any] = {
        "schema_version": TRANSFER_PANEL_AUTHORIZATION_SCHEMA_VERSION,
        "experiment_digest": experiment_manifest["experiment_digest"],
        "lock_digest": lock["lock_digest"],
        "transfer_panel_commitment_sha256": experiment_manifest[
            "frozen_retrospective_transfer_panel"
        ]["commitment_sha256"],
        "baseline_mode": "post_lock_c0_replay",
        "protocol_ids": list(
            experiment_manifest["frozen_retrospective_transfer_panel"]["protocol_ids"]
        ),
        "replay_labels": list(REPLAY_LABELS),
        "endpoint_labels": list(ENDPOINT_LABELS),
        "authorized_by": authorized_by,
        "authorized_at": normalized_timestamp(authorized_at),
        "checkpoint_modification_closed": True,
    }
    result = with_digest(payload, "authorization_digest")
    validate_document(
        result,
        improvement_schema_root() / "transfer_panel_authorization.schema.json",
        label="capability transfer-panel authorization",
    )
    return result


def validate_transfer_panel_authorization(
    path: Path,
    *,
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
    final_lock: Mapping[str, Any],
) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    expected_path = root / "design" / "transfer_panel_authorization.json"
    if resolved_path != expected_path:
        raise CapabilityImprovementError(
            "transfer-panel authorization must be the canonical experiment "
            f"marker: {expected_path}"
        )
    authorization = load_and_validate(
        resolved_path,
        schema_filename="transfer_panel_authorization.schema.json",
        digest_field="authorization_digest",
        label="capability transfer-panel authorization",
    )
    panel = experiment_manifest["frozen_retrospective_transfer_panel"]
    expected = {
        "experiment_digest": experiment_manifest["experiment_digest"],
        "lock_digest": final_lock["lock_digest"],
        "transfer_panel_commitment_sha256": panel["commitment_sha256"],
        "baseline_mode": _panel_baseline_mode(panel),
        "protocol_ids": list(panel["protocol_ids"]),
        "replay_labels": list(REPLAY_LABELS),
        "endpoint_labels": list(ENDPOINT_LABELS),
    }
    for key, value in expected.items():
        if authorization[key] != value:
            raise CapabilityImprovementError(
                f"transfer-panel authorization has stale {key}"
            )
    if authorization["checkpoint_modification_closed"] is not True:
        raise CapabilityImprovementError(
            "transfer-panel authorization did not close checkpoint modification"
        )
    return authorization


def _panel_baseline_mode(panel: Mapping[str, Any]) -> str:
    # C0 is evaluated in the same post-lock replay as every learned checkpoint.
    return "post_lock_c0_replay"


def build_cumulative_leakage_policy(
    *,
    experiment_manifest: Mapping[str, Any],
    private_groundtruth_root: Path,
    through_batch: str,
) -> dict[str, Any]:
    """Build an orchestrator-only leakage denylist from revealed development truth."""

    validate_digest(experiment_manifest, "experiment_digest")
    batches = list(experiment_manifest["batches"])
    matching = [
        index for index, item in enumerate(batches) if item["batch_id"] == through_batch
    ]
    if len(matching) != 1:
        raise CapabilityImprovementError(
            f"unknown leakage-policy batch: {through_batch}"
        )
    protocol_ids = [
        protocol_id
        for batch in batches[: matching[0] + 1]
        for protocol_id in batch["protocol_ids"]
    ]
    root = private_groundtruth_root.expanduser().resolve()
    terms: set[str] = set(protocol_ids)
    sequences: set[str] = set()
    scaffolds: set[str] = set()
    artifacts: list[dict[str, Any]] = []
    for protocol_id in protocol_ids:
        documents: dict[str, dict[str, Any]] = {}
        for task, filename in zip(("T1", "T2", "T3"), GROUNDTRUTH_FILENAMES):
            path = root / protocol_id / filename
            if not path.is_file():
                raise CapabilityImprovementError(
                    f"leakage policy ground truth is missing {protocol_id}/{filename}"
                )
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CapabilityImprovementError(
                    f"cannot read leakage-policy input {protocol_id}/{filename}: {error}"
                ) from error
            if not isinstance(document, dict):
                raise CapabilityImprovementError(
                    f"leakage-policy input must be an object: {protocol_id}/{filename}"
                )
            documents[task] = document
            artifacts.append(
                {
                    "protocol_id": protocol_id,
                    "filename": filename,
                    "sha256": sha256_file(path),
                }
            )
            for value in _iter_strings(document):
                for match in NUCLEOTIDE_RUN_RE.finditer(value):
                    sequences.add(match.group(0).upper())
                if "[" in value and "]" in value and len(value) >= 8:
                    scaffolds.add(value.strip().lower())
        for document in documents.values():
            protocol_name = document.get("protocol_name")
            if isinstance(protocol_name, str) and protocol_name.strip():
                terms.add(protocol_name.strip().lower())
    payload: dict[str, Any] = {
        "schema_version": LEAKAGE_POLICY_SCHEMA_VERSION,
        "experiment_digest": experiment_manifest["experiment_digest"],
        "through_batch": through_batch,
        "protocol_ids": protocol_ids,
        "groundtruth_artifacts": artifacts,
        "forbidden_terms": sorted(item.lower() for item in terms),
        "forbidden_sequences": sorted(sequences),
        "forbidden_scaffolds": sorted(scaffolds),
        "allowed_synthetic_paths": [
            "synthetic_tests/valid/t2.json",
            "synthetic_tests/valid/t3.json",
            "synthetic_tests/valid/evidence_ledger.json",
            "synthetic_tests/valid/work_record.json",
            "synthetic_tests/boundary/excluded_inventory_work_record.json",
            "synthetic_tests/invalid/empty_t2.json",
            "synthetic_tests/invalid/malformed_work_record.json",
            "synthetic_tests/invalid/product_conservation_t3.json",
            "synthetic_tests/invalid/typed_edges_t3.json",
            "synthetic_tests/invalid/strand_pairing_t3.json",
            "synthetic_tests/invalid/unreachable_terminal_t3.json",
            "synthetic_tests/invalid/unsupported_ledger.json",
        ],
        "agent_visibility": "none_orchestrator_only",
    }
    policy = with_digest(payload, "policy_digest")
    validate_document(
        policy,
        improvement_schema_root() / "leakage_policy.schema.json",
        label="capability leakage policy",
    )
    return policy


def validate_cumulative_leakage_policy(
    path: Path,
    *,
    experiment_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = load_and_validate(
        path,
        schema_filename="leakage_policy.schema.json",
        digest_field="policy_digest",
        label="capability leakage policy",
    )
    if (
        experiment_manifest is not None
        and policy["experiment_digest"] != experiment_manifest["experiment_digest"]
    ):
        raise CapabilityImprovementError("leakage policy belongs to another experiment")
    return policy


def _retrospective_baseline(root: Path) -> dict[str, Any]:
    resolved = root.expanduser().resolve()
    required = [resolved / name for name in ("config.json", "lock.json", "result.json")]
    for path in required:
        if not path.is_file():
            raise CapabilityImprovementError(
                f"retrospective baseline is missing {path.name}"
            )
    expected = {
        protocol_id
        for batch in FIXED_BATCHES
        if batch["phase"] == "retrospective"
        for protocol_id in batch["protocol_ids"]
    }
    trials: dict[str, dict[str, Any]] = {}
    for result_path in sorted(resolved.glob("*/result.json")):
        value = json.loads(result_path.read_text(encoding="utf-8"))
        task_path = value.get("task_id", {}).get("path")
        protocol_id = Path(task_path).name if isinstance(task_path, str) else None
        if protocol_id not in expected:
            continue
        if protocol_id in trials:
            raise CapabilityImprovementError(f"baseline repeats protocol {protocol_id}")
        if value.get("exception_info") is not None or not value.get(
            "verifier_result", {}
        ).get("rewards"):
            raise CapabilityImprovementError(
                f"baseline trial is not valid: {protocol_id}"
            )
        checksum = value.get("task_checksum")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise CapabilityImprovementError(
                f"baseline trial lacks task checksum: {protocol_id}"
            )
        trials[protocol_id] = {
            "protocol_id": protocol_id,
            "trial_name": value["trial_name"],
            "result_sha256": sha256_file(result_path),
            "task_checksum": checksum,
        }
    if set(trials) != expected:
        raise CapabilityImprovementError(
            "retrospective baseline trial mismatch; missing="
            + ",".join(sorted(expected - set(trials)))
        )
    return {
        "job_root": resolved.as_posix(),
        "artifacts": [artifact_record(path) for path in required],
        "development_trial_count": len(expected),
        "valid_development_trial_count": len(expected),
        "trial_records": [trials[item] for item in sorted(trials)],
        "agent_visibility": "development_trial_artifacts_only_via_eligible_packet",
    }


def _groundtruth_inventory(root: Path) -> dict[str, dict[str, Any]]:
    resolved = root.expanduser().resolve()
    protocol_ids = {
        protocol_id
        for batch in FIXED_BATCHES
        if batch["phase"] == "prospective"
        for protocol_id in batch["protocol_ids"]
    }
    result: dict[str, dict[str, Any]] = {}
    for protocol_id in sorted(protocol_ids):
        artifacts = []
        documents: dict[str, dict[str, Any]] = {}
        for task, filename in zip(("T1", "T2", "T3"), GROUNDTRUTH_FILENAMES):
            path = resolved / protocol_id / filename
            if not path.is_file():
                raise CapabilityImprovementError(
                    f"private ground truth is missing {protocol_id}/{filename}"
                )
            artifacts.append({"filename": filename, "sha256": sha256_file(path)})
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CapabilityImprovementError(
                    f"cannot read private ground truth {protocol_id}/{filename}: {error}"
                ) from error
            if not isinstance(value, dict):
                raise CapabilityImprovementError(
                    f"private ground truth must be an object: {protocol_id}/{filename}"
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


def _batch(experiment_manifest: Mapping[str, Any], batch_id: str) -> Mapping[str, Any]:
    matches = [
        item for item in experiment_manifest["batches"] if item["batch_id"] == batch_id
    ]
    if len(matches) != 1:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}")
    return matches[0]


def _validate_prospective_terminal_gate(
    protocol_ids: Sequence[str],
    terminal_values: Sequence[Mapping[str, Any]],
) -> None:
    observed = {
        (item.get("protocol_id"), item.get("branch")) for item in terminal_values
    }
    expected = {
        (protocol_id, branch)
        for protocol_id in protocol_ids
        for branch in ("C0", ACTIVE_BRANCH)
    }
    if observed != expected or len(terminal_values) != len(expected):
        raise CapabilityImprovementError(
            "prospective reveal requires one frozen C0 and one cumulative "
            "terminal record for every protocol"
        )


def _validate_packet_artifacts(
    protocol_ids: Sequence[str],
    artifacts: Sequence[Mapping[str, Any]],
) -> None:
    allowed_protocols = set(protocol_ids)
    identities: set[tuple[str, str, str]] = set()
    for item in artifacts:
        protocol_id = item.get("protocol_id")
        if protocol_id not in allowed_protocols:
            raise CapabilityImprovementError(
                f"batch packet artifact is outside its protocol set: {protocol_id}"
            )
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise CapabilityImprovementError("batch packet artifact path is missing")
        raw_path = Path(path_value).expanduser()
        if raw_path.is_symlink() or not raw_path.is_file():
            raise CapabilityImprovementError(
                f"batch packet artifact is not a regular file: {path_value}"
            )
        path = raw_path.resolve()
        actual_sha = sha256_file(path)
        if item.get("sha256") != actual_sha:
            raise CapabilityImprovementError(
                f"batch packet artifact hash is stale: {path_value}"
            )
        identity = (str(protocol_id), str(item.get("role")), path.as_posix())
        if identity in identities:
            raise CapabilityImprovementError(
                f"batch packet repeats an artifact: {path_value}"
            )
        identities.add(identity)


def _batch_groundtruth_commitment(
    batch: Mapping[str, Any],
    inventory: Mapping[str, Mapping[str, Any]],
) -> str:
    entries = [inventory[protocol_id] for protocol_id in batch["protocol_ids"]]
    return canonical_digest({"batch_id": batch["batch_id"], "entries": entries})


def _compatibility_status(entries: Sequence[Mapping[str, Any]]) -> str:
    return (
        "current_schema_valid"
        if all(item["linked_validation"] == "pass" for item in entries)
        else "hash_pinned_schema_migration_required"
    )


def _validate_initial_governance(
    *,
    pack_digest: str,
    panel_commitment_sha256: str,
    s0_provenance_audit: Mapping[str, Any],
    transfer_access_policy: Mapping[str, Any],
    baseline_registry: Mapping[str, Any],
    supersession_manifest: Mapping[str, Any],
) -> None:
    documents = (
        (
            s0_provenance_audit,
            "audit_digest",
            "s0_provenance_audit.schema.json",
            "S0 provenance audit",
        ),
        (
            transfer_access_policy,
            "policy_digest",
            "transfer_access_policy.schema.json",
            "transfer-panel access policy",
        ),
        (
            baseline_registry,
            "registry_digest",
            "baseline_registry.schema.json",
            "baseline registry",
        ),
        (
            supersession_manifest,
            "supersession_digest",
            "supersession_manifest.schema.json",
            "supersession manifest",
        ),
    )
    for document, digest_field, schema, label in documents:
        validate_digest(document, digest_field)
        validate_document(
            dict(document),
            improvement_schema_root() / schema,
            label=label,
        )
    if s0_provenance_audit["pack_digest"] != pack_digest:
        raise CapabilityImprovementError("S0 provenance audit covers another pack")
    if transfer_access_policy["panel_commitment_sha256"] != panel_commitment_sha256:
        raise CapabilityImprovementError("transfer access policy covers another panel")
    if baseline_registry["panel_commitment_sha256"] != panel_commitment_sha256:
        raise CapabilityImprovementError("baseline registry covers another panel")


def _validate_revealed_groundtruth_commitment(
    batch: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
) -> None:
    groundtruth = [
        item for item in artifacts if item.get("role") == "approved_groundtruth"
    ]
    expected_pairs = {
        (protocol_id, filename)
        for protocol_id in batch["protocol_ids"]
        for filename in GROUNDTRUTH_FILENAMES
    }
    observed_pairs = {
        (str(item["protocol_id"]), Path(str(item["path"])).name) for item in groundtruth
    }
    if observed_pairs != expected_pairs or len(groundtruth) != len(expected_pairs):
        raise CapabilityImprovementError(
            "revealed prospective packet requires all three pinned ground-truth files per protocol"
        )
    entries: list[dict[str, Any]] = []
    for protocol_id in batch["protocol_ids"]:
        documents: dict[str, dict[str, Any]] = {}
        ordered_artifacts: list[dict[str, str]] = []
        for task, filename in zip(("T1", "T2", "T3"), GROUNDTRUTH_FILENAMES):
            matches = [
                item
                for item in groundtruth
                if item["protocol_id"] == protocol_id
                and Path(item["path"]).name == filename
            ]
            path = Path(matches[0]["path"])
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CapabilityImprovementError(
                    f"cannot read committed ground truth {protocol_id}/{filename}: {error}"
                ) from error
            if not isinstance(value, dict):
                raise CapabilityImprovementError(
                    f"committed ground truth must be an object: {protocol_id}/{filename}"
                )
            documents[task] = value
            ordered_artifacts.append(
                {"filename": filename, "sha256": matches[0]["sha256"]}
            )
        try:
            validate_groundtruth_bundle(documents, protocol_id=protocol_id)
        except LibgenValidationError as error:
            linked_validation = "fails_current_schema"
            validation_error_sha256 = canonical_digest(str(error))
        else:
            linked_validation = "pass"
            validation_error_sha256 = None
        entries.append(
            {
                "protocol_id": protocol_id,
                "artifacts": ordered_artifacts,
                "linked_validation": linked_validation,
                "validation_error_sha256": validation_error_sha256,
            }
        )
    actual = canonical_digest({"batch_id": batch["batch_id"], "entries": entries})
    if actual != batch["groundtruth_commitment_sha256"]:
        raise CapabilityImprovementError(
            "revealed ground truth differs from the frozen prospective commitment"
        )


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_strings(child)
