from __future__ import annotations

import copy
import re
import shlex
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.external_knowledge import (
    MEMORY_WARNING,
    PRECEDENCE_RULE,
    TARGET_PROTOCOL_IDS,
    ExternalKnowledgeBuildError,
    canonical_digest,
    load_json,
    sha256_file,
    validate_external_knowledge_assets,
    validate_external_knowledge_review_candidate,
    write_json,
)
from libstruct_bench.libgen.error_analysis import task_bundle_sha256
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION


CONDITION_IDS = (
    "general_methods_v1",
    "cross_protocol_memory_v1",
    "general_methods_plus_memory_v1",
)
EXTERNAL_KNOWLEDGE_MOUNT_TARGET = "/workspace/external_knowledge"
VERIFIER_ARTIFACTS = (
    "/logs/verifier/reward.json",
    "/logs/verifier/details.json",
    "/logs/verifier/error_analysis.json",
    "/logs/verifier/error.json",
)
REVIEW_ONLY_FILENAMES = frozenset(
    {
        "donor_target_overlap.tsv",
        "projection_validation_report.json",
    }
)

FINAL_APPROVAL_SCHEMA_VERSION = (
    "libstruct.libgen_external_knowledge_final_approval.v1"
)
HARBOR_INTEGRATION_SCHEMA_VERSION = (
    "libstruct.libgen_external_knowledge_harbor_integration.v1"
)
HARBOR_PLAN_SCHEMA_VERSION = "libstruct.libgen_external_knowledge_harbor_plan.v1"


def build_external_knowledge_final_approval(
    *,
    review_candidate: Mapping[str, Any],
    reviewer_identity: str,
    approved_at: str,
    rationale: str,
    harbor_integration_authorized: bool = True,
    experiment_run_authorized: bool = False,
) -> dict[str, Any]:
    """Record a human decision without mutating either frozen asset package."""

    validate_external_knowledge_review_candidate(review_candidate)
    reviewer_identity = reviewer_identity.strip()
    rationale = rationale.strip()
    if not reviewer_identity:
        raise ExternalKnowledgeBuildError("final approval requires reviewer identity")
    if not approved_at:
        raise ExternalKnowledgeBuildError("final approval requires an approval time")
    if not rationale:
        raise ExternalKnowledgeBuildError("final approval requires a rationale")
    if harbor_integration_authorized is not True:
        raise ExternalKnowledgeBuildError(
            "this approval record is specifically for Harbor integration"
        )

    payload: dict[str, Any] = {
        "schema_version": FINAL_APPROVAL_SCHEMA_VERSION,
        "approval_id": review_candidate["review_candidate_id"] + ":approval-001",
        "status": "final",
        "decision": "approve",
        "reviewer_identity": reviewer_identity,
        "approved_at": approved_at,
        "rationale": rationale,
        "review_candidate": {
            "review_candidate_id": review_candidate["review_candidate_id"],
            "review_candidate_digest": review_candidate[
                "review_candidate_digest"
            ],
        },
        "approved_package": copy.deepcopy(review_candidate["revised_package"]),
        "source_locator_review": {
            "status": "completed_and_human_approved",
            "evidence_row_count": review_candidate["primer_review"][
                "evidence_row_count"
            ],
        },
        "primer_review": {
            "decision": "approve_revised_primer",
            "card_count": review_candidate["primer_review"]["card_count"],
            "revised_card_ids": copy.deepcopy(
                review_candidate["primer_review"]["revised_card_ids"]
            ),
            "caution_card_ids": copy.deepcopy(
                review_candidate["primer_review"]["caution_card_ids"]
            ),
            "artifact_warning_card_ids": copy.deepcopy(
                review_candidate["primer_review"]["artifact_warning_card_ids"]
            ),
        },
        "donor_projection_review": {
            "decision": "approve_approved_current_lineage",
            "lineage_selection": review_candidate["donor_projection_review"][
                "lineage_selection"
            ],
            "direct_report_verification": review_candidate[
                "donor_projection_review"
            ]["direct_report_verification"],
        },
        "overlap_review": {
            "decision": "accept_full_solved_protocol_memory_condition",
            "agent_visibility": review_candidate["overlap_review"][
                "agent_visibility"
            ],
            "interpretation": review_candidate["overlap_review"][
                "interpretation"
            ],
        },
        "analysis_preregistration": copy.deepcopy(
            review_candidate["analysis_preregistration"]
        ),
        "authorization": {
            "harbor_integration_authorized": harbor_integration_authorized,
            "experiment_run_authorized": experiment_run_authorized,
        },
    }
    approval = _with_digest(payload, "approval_digest")
    validate_external_knowledge_final_approval(
        approval,
        review_candidate=review_candidate,
    )
    return approval


def validate_external_knowledge_final_approval(
    approval: Mapping[str, Any],
    *,
    review_candidate: Mapping[str, Any],
) -> None:
    validate_external_knowledge_review_candidate(review_candidate)
    _validate_digest(approval, "approval_digest")
    if approval.get("schema_version") != FINAL_APPROVAL_SCHEMA_VERSION:
        raise ExternalKnowledgeBuildError("unknown final-approval schema")
    if approval.get("status") != "final" or approval.get("decision") != "approve":
        raise ExternalKnowledgeBuildError("external knowledge is not finally approved")
    if not isinstance(approval.get("reviewer_identity"), str) or not approval[
        "reviewer_identity"
    ].strip():
        raise ExternalKnowledgeBuildError("final approval lacks reviewer identity")
    if not isinstance(approval.get("rationale"), str) or not approval["rationale"].strip():
        raise ExternalKnowledgeBuildError("final approval lacks a rationale")
    expected_candidate = {
        "review_candidate_id": review_candidate["review_candidate_id"],
        "review_candidate_digest": review_candidate["review_candidate_digest"],
    }
    if approval.get("review_candidate") != expected_candidate:
        raise ExternalKnowledgeBuildError("approval covers another review candidate")
    if approval.get("approved_package") != review_candidate["revised_package"]:
        raise ExternalKnowledgeBuildError("approval covers another revised package")
    locator_review = approval.get("source_locator_review")
    if not isinstance(locator_review, Mapping) or locator_review.get("status") != (
        "completed_and_human_approved"
    ):
        raise ExternalKnowledgeBuildError("source-locator review is not approved")
    if locator_review.get("evidence_row_count") != review_candidate["primer_review"][
        "evidence_row_count"
    ]:
        raise ExternalKnowledgeBuildError("approval covers a different evidence table")
    if approval.get("analysis_preregistration") != review_candidate[
        "analysis_preregistration"
    ]:
        raise ExternalKnowledgeBuildError("approval changes the preregistered analysis")
    authorization = approval.get("authorization")
    if not isinstance(authorization, Mapping) or authorization.get(
        "harbor_integration_authorized"
    ) is not True:
        raise ExternalKnowledgeBuildError("Harbor integration is not authorized")
    if not isinstance(authorization.get("experiment_run_authorized"), bool):
        raise ExternalKnowledgeBuildError("run authorization must be explicit")
    if approval.get("overlap_review", {}).get("agent_visibility") != (
        "review_only_hidden"
    ):
        raise ExternalKnowledgeBuildError("overlap report must remain hidden")


def build_external_knowledge_harbor_integration(
    *,
    asset_root: Path,
    tasks_root: Path,
    review_candidate: Mapping[str, Any],
    approval: Mapping[str, Any],
    integration_root: Path,
    harbor_version: str,
    created_at: str,
) -> dict[str, Any]:
    """Build agent-only, allowlisted Harbor mount roots for approved conditions."""

    if integration_root.exists() and any(integration_root.iterdir()):
        raise ExternalKnowledgeBuildError(
            f"refusing to overwrite non-empty integration root: {integration_root}"
        )
    validate_external_knowledge_assets(asset_root)
    validate_external_knowledge_final_approval(
        approval,
        review_candidate=review_candidate,
    )
    asset_inventory = _directory_inventory(asset_root)
    asset_package_digest = _inventory_digest(asset_inventory)
    revised_package = review_candidate["revised_package"]
    if asset_package_digest != revised_package["package_digest"]:
        raise ExternalKnowledgeBuildError(
            "approved asset package bytes do not match the review candidate"
        )
    if asset_root.name != revised_package["root_name"]:
        raise ExternalKnowledgeBuildError(
            "approved asset package root name does not match the review candidate"
        )
    if not harbor_version:
        raise ExternalKnowledgeBuildError("Harbor integration requires a version pin")

    tasks_root = tasks_root.resolve()
    _validate_target_tasks_use_separate_verifier(tasks_root)
    task_digest_before = task_bundle_sha256(tasks_root, TARGET_PROTOCOL_IDS)

    integration_root.mkdir(parents=True, exist_ok=True)
    lineage_root = integration_root / "lineage"
    write_json(lineage_root / "review_candidate.json", review_candidate)
    write_json(lineage_root / "final_approval.json", approval)

    condition_summaries: list[dict[str, Any]] = []
    for condition_id in CONDITION_IDS:
        condition_source_path = (
            asset_root / "conditions" / condition_id / "manifest.json"
        )
        condition_manifest = load_json(condition_source_path)
        approved_digest = revised_package["condition_digests"][condition_id]
        if condition_manifest.get("condition_digest") != approved_digest:
            raise ExternalKnowledgeBuildError(
                f"condition digest is not human-approved: {condition_id}"
            )
        _validate_digest(condition_manifest, "condition_digest")

        condition_root = integration_root / "conditions" / condition_id
        exposure_root = condition_root / "exposure"
        exposure_root.mkdir(parents=True)
        shutil.copyfile(
            condition_source_path,
            condition_root / "source_condition_manifest.json",
        )

        exposed_files: list[dict[str, Any]] = []
        for entry in condition_manifest["included_files"]:
            if entry.get("visibility") != "agent":
                raise ExternalKnowledgeBuildError(
                    f"condition contains a non-agent entry: {condition_id}"
                )
            relative = _safe_relative_path(entry.get("path"))
            if relative.name in REVIEW_ONLY_FILENAMES:
                raise ExternalKnowledgeBuildError(
                    f"condition attempts to expose review-only data: {relative}"
                )
            source = asset_root / relative
            if source.is_symlink() or not source.is_file():
                raise ExternalKnowledgeBuildError(
                    f"condition source is not a regular file: {relative}"
                )
            if sha256_file(source) != entry.get("sha256"):
                raise ExternalKnowledgeBuildError(
                    f"condition source hash is stale: {relative}"
                )
            destination = exposure_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            exposed_files.append(
                {
                    "path": relative.as_posix(),
                    "sha256": sha256_file(destination),
                    "size_bytes": destination.stat().st_size,
                }
            )

        instruction = _condition_instruction(
            condition_id=condition_id,
            condition_digest=approved_digest,
            included_assets=condition_manifest["included_assets"],
        )
        instruction_path = condition_root / "extra_instruction.md"
        instruction_path.write_text(instruction, encoding="utf-8")
        mount = {
            "type": "bind",
            "source": exposure_root.resolve().as_posix(),
            "target": EXTERNAL_KNOWLEDGE_MOUNT_TARGET,
            "read_only": True,
            "bind": {"create_host_path": False},
        }
        condition_payload: dict[str, Any] = {
            "schema_version": HARBOR_INTEGRATION_SCHEMA_VERSION,
            "condition_id": condition_id,
            "condition_digest": approved_digest,
            "source_condition_manifest_sha256": sha256_file(
                condition_root / "source_condition_manifest.json"
            ),
            "exposure_root": exposure_root.resolve().as_posix(),
            "exposed_files": exposed_files,
            "extra_instruction_path": instruction_path.resolve().as_posix(),
            "extra_instruction_sha256": sha256_file(instruction_path),
            "mount": mount,
            "agent_visibility": "allowlisted_files_only",
            "verifier_visibility": "none_separate_environment",
            "contents_merged_or_rewritten": False,
            "review_only_reports_exposed": False,
            "source_pdfs_exposed": False,
        }
        condition_integration = _with_digest(
            condition_payload,
            "integration_condition_digest",
        )
        condition_integration_path = condition_root / "integration_manifest.json"
        write_json(condition_integration_path, condition_integration)
        condition_summaries.append(
            {
                "condition_id": condition_id,
                "condition_digest": approved_digest,
                "integration_condition_digest": condition_integration[
                    "integration_condition_digest"
                ],
                "integration_manifest_path": condition_integration_path.relative_to(
                    integration_root
                ).as_posix(),
                "integration_manifest_sha256": sha256_file(
                    condition_integration_path
                ),
                "exposed_file_count": len(exposed_files),
            }
        )

    task_digest_after = task_bundle_sha256(tasks_root, TARGET_PROTOCOL_IDS)
    if task_digest_after != task_digest_before:
        raise ExternalKnowledgeBuildError(
            "baseline target task bytes changed while building integration"
        )

    root_payload: dict[str, Any] = {
        "schema_version": HARBOR_INTEGRATION_SCHEMA_VERSION,
        "integration_id": "libgen-improvement-v1:harbor-integration-001",
        "status": "approved_and_prepared_not_run",
        "created_at": created_at,
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "harbor_version": harbor_version,
        "asset_package": {
            "root_name": asset_root.name,
            "package_digest": asset_package_digest,
        },
        "review_candidate": {
            "review_candidate_id": review_candidate["review_candidate_id"],
            "review_candidate_digest": review_candidate[
                "review_candidate_digest"
            ],
            "path": "lineage/review_candidate.json",
            "sha256": sha256_file(lineage_root / "review_candidate.json"),
        },
        "final_approval": {
            "approval_id": approval["approval_id"],
            "approval_digest": approval["approval_digest"],
            "reviewer_identity": approval["reviewer_identity"],
            "path": "lineage/final_approval.json",
            "sha256": sha256_file(lineage_root / "final_approval.json"),
        },
        "target_protocol_ids": list(TARGET_PROTOCOL_IDS),
        "target_task_bundle_sha256": task_digest_before,
        "target_tasks_root": tasks_root.as_posix(),
        "conditions": condition_summaries,
        "precedence_rule": PRECEDENCE_RULE,
        "analysis_preregistration": copy.deepcopy(
            approval["analysis_preregistration"]
        ),
        "isolation": {
            "baseline_task_files_modified": False,
            "task_schemas_modified": False,
            "ground_truth_modified": False,
            "scorer_modified": False,
            "network_policy_modified": False,
            "mount_target": EXTERNAL_KNOWLEDGE_MOUNT_TARGET,
            "mount_read_only": True,
            "agent_only_mount": True,
            "separate_verifier_required": True,
            "review_only_reports_hidden": True,
        },
        "authorization": copy.deepcopy(approval["authorization"]),
    }
    integration = _with_digest(root_payload, "integration_digest")
    write_json(integration_root / "integration_manifest.json", integration)
    validate_external_knowledge_harbor_integration(
        integration_root,
        tasks_root=tasks_root,
    )
    return integration


def validate_external_knowledge_harbor_integration(
    integration_root: Path,
    *,
    tasks_root: Path | None = None,
) -> dict[str, Any]:
    manifest_path = integration_root / "integration_manifest.json"
    manifest = load_json(manifest_path)
    _validate_digest(manifest, "integration_digest")
    if manifest.get("schema_version") != HARBOR_INTEGRATION_SCHEMA_VERSION:
        raise ExternalKnowledgeBuildError("unknown Harbor integration schema")
    if manifest.get("status") != "approved_and_prepared_not_run":
        raise ExternalKnowledgeBuildError("Harbor integration is not approved")
    if manifest.get("benchmark_version") != LIBGEN_BENCHMARK_VERSION:
        raise ExternalKnowledgeBuildError("Harbor integration benchmark version drift")
    if manifest.get("precedence_rule") != PRECEDENCE_RULE:
        raise ExternalKnowledgeBuildError("Harbor integration precedence-rule drift")

    review_candidate = load_json(
        integration_root / manifest["review_candidate"]["path"]
    )
    approval = load_json(integration_root / manifest["final_approval"]["path"])
    validate_external_knowledge_final_approval(
        approval,
        review_candidate=review_candidate,
    )
    if sha256_file(
        integration_root / manifest["review_candidate"]["path"]
    ) != manifest["review_candidate"]["sha256"]:
        raise ExternalKnowledgeBuildError("review-candidate lineage bytes changed")
    if sha256_file(
        integration_root / manifest["final_approval"]["path"]
    ) != manifest["final_approval"]["sha256"]:
        raise ExternalKnowledgeBuildError("final-approval lineage bytes changed")

    condition_ids = [item["condition_id"] for item in manifest["conditions"]]
    if tuple(condition_ids) != CONDITION_IDS:
        raise ExternalKnowledgeBuildError("integration condition set or order changed")
    approved_conditions = approval["approved_package"]["condition_digests"]
    condition_reports: list[dict[str, Any]] = []
    for summary in manifest["conditions"]:
        condition_id = summary["condition_id"]
        condition_root = integration_root / "conditions" / condition_id
        source_manifest_path = condition_root / "source_condition_manifest.json"
        source_manifest = load_json(source_manifest_path)
        _validate_digest(source_manifest, "condition_digest")
        if source_manifest["condition_digest"] != approved_conditions[condition_id]:
            raise ExternalKnowledgeBuildError(
                f"integration condition is no longer approved: {condition_id}"
            )
        integration_path = integration_root / summary["integration_manifest_path"]
        if sha256_file(integration_path) != summary["integration_manifest_sha256"]:
            raise ExternalKnowledgeBuildError(
                f"condition integration manifest changed: {condition_id}"
            )
        condition = load_json(integration_path)
        _validate_digest(condition, "integration_condition_digest")
        if condition["integration_condition_digest"] != summary[
            "integration_condition_digest"
        ]:
            raise ExternalKnowledgeBuildError(
                f"condition integration digest changed: {condition_id}"
            )
        exposure_root = Path(condition["exposure_root"])
        expected_exposure_root = (condition_root / "exposure").resolve()
        if exposure_root != expected_exposure_root:
            raise ExternalKnowledgeBuildError(
                f"condition exposure root moved: {condition_id}"
            )
        exposure_inventory = _directory_inventory(exposure_root)
        expected_inventory = {
            entry["path"]: entry["sha256"] for entry in condition["exposed_files"]
        }
        if exposure_inventory != expected_inventory:
            raise ExternalKnowledgeBuildError(
                f"condition exposure bytes changed: {condition_id}"
            )
        source_inventory = {
            entry["path"]: entry["sha256"]
            for entry in source_manifest["included_files"]
        }
        if exposure_inventory != source_inventory:
            raise ExternalKnowledgeBuildError(
                f"condition exposure is not the exact allowlist: {condition_id}"
            )
        if any(
            PurePosixPath(relative).name in REVIEW_ONLY_FILENAMES
            for relative in exposure_inventory
        ):
            raise ExternalKnowledgeBuildError(
                f"review-only file exposed to agent: {condition_id}"
            )
        if any(relative.lower().endswith(".pdf") for relative in exposure_inventory):
            raise ExternalKnowledgeBuildError(
                f"source PDF exposed to agent: {condition_id}"
            )
        instruction_path = Path(condition["extra_instruction_path"])
        expected_instruction_path = (condition_root / "extra_instruction.md").resolve()
        if instruction_path != expected_instruction_path:
            raise ExternalKnowledgeBuildError(
                f"condition instruction path moved: {condition_id}"
            )
        if sha256_file(instruction_path) != condition["extra_instruction_sha256"]:
            raise ExternalKnowledgeBuildError(
                f"condition instruction changed: {condition_id}"
            )
        expected_instruction = _condition_instruction(
            condition_id=condition_id,
            condition_digest=source_manifest["condition_digest"],
            included_assets=source_manifest["included_assets"],
        )
        if instruction_path.read_text(encoding="utf-8") != expected_instruction:
            raise ExternalKnowledgeBuildError(
                f"condition instruction is not deterministic: {condition_id}"
            )
        expected_mount = {
            "type": "bind",
            "source": exposure_root.as_posix(),
            "target": EXTERNAL_KNOWLEDGE_MOUNT_TARGET,
            "read_only": True,
            "bind": {"create_host_path": False},
        }
        if condition.get("mount") != expected_mount:
            raise ExternalKnowledgeBuildError(
                f"condition mount is not the approved read-only bind: {condition_id}"
            )
        if condition.get("verifier_visibility") != "none_separate_environment":
            raise ExternalKnowledgeBuildError(
                f"condition is not isolated from verifier: {condition_id}"
            )
        condition_reports.append(
            {
                "condition_id": condition_id,
                "condition_digest": source_manifest["condition_digest"],
                "exposed_file_count": len(exposure_inventory),
                "exposure_check": "pass",
                "instruction_check": "pass",
                "mount_check": "pass",
            }
        )

    isolation = manifest.get("isolation")
    required_isolation = {
        "baseline_task_files_modified": False,
        "task_schemas_modified": False,
        "ground_truth_modified": False,
        "scorer_modified": False,
        "network_policy_modified": False,
        "mount_target": EXTERNAL_KNOWLEDGE_MOUNT_TARGET,
        "mount_read_only": True,
        "agent_only_mount": True,
        "separate_verifier_required": True,
        "review_only_reports_hidden": True,
    }
    if isolation != required_isolation:
        raise ExternalKnowledgeBuildError("integration isolation contract changed")

    if tasks_root is not None:
        tasks_root = tasks_root.resolve()
        _validate_target_tasks_use_separate_verifier(tasks_root)
        if task_bundle_sha256(tasks_root, TARGET_PROTOCOL_IDS) != manifest[
            "target_task_bundle_sha256"
        ]:
            raise ExternalKnowledgeBuildError(
                "target task bytes changed after integration approval"
            )
    return {
        "status": "pass",
        "integration_digest": manifest["integration_digest"],
        "condition_reports": condition_reports,
        "agent_only_mount_check": "pass",
        "target_task_bundle_check": "pass" if tasks_root is not None else "not_run",
    }


def build_external_knowledge_harbor_plan(
    *,
    integration_root: Path,
    tasks_root: Path,
    base_job_config_paths: Sequence[Path],
    output_root: Path,
    jobs_dir: Path,
    created_at: str,
    condition_ids: Sequence[str] = CONDITION_IDS,
) -> dict[str, Any]:
    """Clone completed baseline job configs into locked intervention jobs."""

    if output_root.exists() and any(output_root.iterdir()):
        raise ExternalKnowledgeBuildError(
            f"refusing to overwrite non-empty Harbor plan root: {output_root}"
        )
    if not base_job_config_paths:
        raise ExternalKnowledgeBuildError("at least one baseline job config is required")
    selected_conditions = tuple(condition_ids)
    if not selected_conditions or len(set(selected_conditions)) != len(
        selected_conditions
    ):
        raise ExternalKnowledgeBuildError("condition selection is empty or duplicated")
    if not set(selected_conditions) <= set(CONDITION_IDS):
        raise ExternalKnowledgeBuildError("plan selects an unknown condition")

    integration_report = validate_external_knowledge_harbor_integration(
        integration_root,
        tasks_root=tasks_root,
    )
    integration_manifest_path = integration_root / "integration_manifest.json"
    integration_manifest = load_json(integration_manifest_path)
    tasks_root = tasks_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    config_root = output_root / "jobs"
    config_root.mkdir()
    jobs_dir = jobs_dir.resolve()

    planned_jobs: list[dict[str, Any]] = []
    scripts_by_base: dict[str, list[str]] = {}
    seen_base_names: set[str] = set()
    for base_config_path in base_job_config_paths:
        base_config_path = base_config_path.resolve()
        base_config = load_json(base_config_path)
        base_name, agent_name = _validate_baseline_job_config(base_config)
        if base_name in seen_base_names:
            raise ExternalKnowledgeBuildError(
                f"duplicate baseline job name: {base_name}"
            )
        seen_base_names.add(base_name)
        scripts_by_base[base_name] = []
        for condition_id in selected_conditions:
            condition = _load_integration_condition(
                integration_root,
                condition_id,
            )
            config = copy.deepcopy(base_config)
            job_name = f"{base_name}--{condition_id}"
            config["job_name"] = job_name
            config["jobs_dir"] = jobs_dir.as_posix()
            config["n_attempts"] = int(base_config.get("n_attempts", 1))
            config["n_concurrent_trials"] = int(
                base_config.get("n_concurrent_trials", 4)
            )
            config["retry"] = copy.deepcopy(
                base_config.get("retry", {"max_retries": 0})
            )
            config["environment"] = copy.deepcopy(base_config.get("environment", {}))
            config["environment"]["type"] = "docker"
            config["environment"]["mounts"] = [copy.deepcopy(condition["mount"])]
            config["datasets"] = [
                {
                    "path": tasks_root.as_posix(),
                    "task_names": list(TARGET_PROTOCOL_IDS),
                }
            ]
            config["tasks"] = []
            config["extra_instruction_paths"] = [
                condition["extra_instruction_path"]
            ]
            config["artifacts"] = _merged_artifacts(base_config.get("artifacts", []))
            agent = config["agents"][0]
            agent_env = dict(agent.get("env", {}))
            agent_env.update(
                {
                    "LIBGEN_EXTERNAL_KNOWLEDGE_CONDITION": condition_id,
                    "LIBGEN_EXTERNAL_KNOWLEDGE_DIGEST": condition[
                        "condition_digest"
                    ],
                }
            )
            agent["env"] = agent_env

            config_path = config_root / f"{_slug(base_name)}__{condition_id}.json"
            write_json(config_path, config)
            command = (
                "harbor run -c "
                + shlex.quote(config_path.resolve().as_posix())
                + " -y"
            )
            scripts_by_base[base_name].append(command)
            planned_jobs.append(
                {
                    "base_job_name": base_name,
                    "base_config_path": base_config_path.as_posix(),
                    "base_config_sha256": sha256_file(base_config_path),
                    "agent_name": agent_name,
                    "model_name": agent.get("model_name"),
                    "condition_id": condition_id,
                    "condition_digest": condition["condition_digest"],
                    "job_name": job_name,
                    "job_config_path": config_path.resolve().as_posix(),
                    "job_config_sha256": sha256_file(config_path),
                    "n_attempts": config["n_attempts"],
                    "n_concurrent_trials": config["n_concurrent_trials"],
                    "expected_trial_count": len(TARGET_PROTOCOL_IDS)
                    * config["n_attempts"],
                }
            )

    repo_root = Path(__file__).resolve().parents[3]
    validation_command = (
        f"PYTHONPATH={shlex.quote((repo_root / 'src').as_posix())} "
        "python -m libstruct_bench.cli.validate_libgen_external_knowledge_harbor "
        f"--integration-root {shlex.quote(integration_root.resolve().as_posix())} "
        f"--tasks {shlex.quote(tasks_root.as_posix())} "
        f"--plan-root {shlex.quote(output_root.resolve().as_posix())}"
    )
    script_paths: list[dict[str, str]] = []
    for base_name, commands in scripts_by_base.items():
        script_path = output_root / f"run_{_slug(base_name)}.sh"
        lines = ["#!/usr/bin/env bash", "set -euo pipefail", validation_command]
        lines.extend(_auth_preflight_lines(planned_jobs, base_name=base_name))
        lines.extend(commands)
        script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        script_path.chmod(0o755)
        script_paths.append(
            {
                "base_job_name": base_name,
                "path": script_path.resolve().as_posix(),
                "sha256": sha256_file(script_path),
            }
        )

    all_script = output_root / "run_all.sh"
    all_lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    all_lines.extend(
        "bash " + shlex.quote(item["path"]) for item in script_paths
    )
    all_script.write_text("\n".join(all_lines) + "\n", encoding="utf-8")
    all_script.chmod(0o755)

    lock_payload: dict[str, Any] = {
        "schema_version": HARBOR_PLAN_SCHEMA_VERSION,
        "plan_id": "libgen-improvement-v1:external-knowledge-plan-001",
        "status": "planned_not_run",
        "created_at": created_at,
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "harbor_version": integration_manifest["harbor_version"],
        "integration": {
            "root": integration_root.resolve().as_posix(),
            "manifest_path": integration_manifest_path.resolve().as_posix(),
            "manifest_sha256": sha256_file(integration_manifest_path),
            "integration_digest": integration_report["integration_digest"],
        },
        "target_protocol_ids": list(TARGET_PROTOCOL_IDS),
        "target_task_bundle_sha256": integration_manifest[
            "target_task_bundle_sha256"
        ],
        "conditions": list(selected_conditions),
        "jobs_dir": jobs_dir.as_posix(),
        "planned_jobs": planned_jobs,
        "run_scripts": [
            *script_paths,
            {
                "base_job_name": "all",
                "path": all_script.resolve().as_posix(),
                "sha256": sha256_file(all_script),
            },
        ],
        "expected_trial_count": sum(
            job["expected_trial_count"] for job in planned_jobs
        ),
        "execution_policy": {
            "automatic_retries": 0,
            "baseline_job_config_cloned": True,
            "baseline_task_files_modified": False,
            "condition_is_only_intervention": True,
            "condition_mount_read_only": True,
            "condition_mount_agent_only": True,
            "run_not_started_by_planner": True,
        },
        "analysis_preregistration": copy.deepcopy(
            integration_manifest["analysis_preregistration"]
        ),
        "authorization": copy.deepcopy(integration_manifest["authorization"]),
    }
    lock = _with_digest(lock_payload, "plan_digest")
    write_json(output_root / "experiment_lock.json", lock)
    validate_external_knowledge_harbor_plan(
        output_root,
        integration_root=integration_root,
        tasks_root=tasks_root,
    )
    return lock


def validate_external_knowledge_harbor_plan(
    plan_root: Path,
    *,
    integration_root: Path,
    tasks_root: Path,
) -> dict[str, Any]:
    integration = validate_external_knowledge_harbor_integration(
        integration_root,
        tasks_root=tasks_root,
    )
    lock_path = plan_root / "experiment_lock.json"
    lock = load_json(lock_path)
    _validate_digest(lock, "plan_digest")
    if lock.get("schema_version") != HARBOR_PLAN_SCHEMA_VERSION:
        raise ExternalKnowledgeBuildError("unknown external-knowledge plan schema")
    if lock.get("status") != "planned_not_run":
        raise ExternalKnowledgeBuildError("external-knowledge plan is not pristine")
    if lock.get("integration", {}).get("integration_digest") != integration[
        "integration_digest"
    ]:
        raise ExternalKnowledgeBuildError("plan references another integration")
    if task_bundle_sha256(tasks_root.resolve(), TARGET_PROTOCOL_IDS) != lock[
        "target_task_bundle_sha256"
    ]:
        raise ExternalKnowledgeBuildError("target task bytes changed after planning")
    if set(lock.get("conditions", [])) - set(CONDITION_IDS):
        raise ExternalKnowledgeBuildError("plan contains an unknown condition")

    expected_trials = 0
    for job in lock["planned_jobs"]:
        base_path = Path(job["base_config_path"])
        config_path = Path(job["job_config_path"])
        if sha256_file(base_path) != job["base_config_sha256"]:
            raise ExternalKnowledgeBuildError(
                f"baseline job config changed: {base_path}"
            )
        if sha256_file(config_path) != job["job_config_sha256"]:
            raise ExternalKnowledgeBuildError(f"planned job config changed: {config_path}")
        config = load_json(config_path)
        if config.get("job_name") != job["job_name"]:
            raise ExternalKnowledgeBuildError("planned job name changed")
        if config.get("retry", {}).get("max_retries", 0) != 0:
            raise ExternalKnowledgeBuildError("automatic retry enabled in primary plan")
        datasets = config.get("datasets")
        if datasets != [
            {
                "path": tasks_root.resolve().as_posix(),
                "task_names": list(TARGET_PROTOCOL_IDS),
            }
        ]:
            raise ExternalKnowledgeBuildError("planned job target set changed")
        if config.get("tasks") != []:
            raise ExternalKnowledgeBuildError("planned job adds an unapproved task")
        condition = _load_integration_condition(
            integration_root,
            job["condition_id"],
        )
        if config.get("environment", {}).get("mounts") != [condition["mount"]]:
            raise ExternalKnowledgeBuildError("planned job mount changed")
        if config.get("extra_instruction_paths") != [
            condition["extra_instruction_path"]
        ]:
            raise ExternalKnowledgeBuildError("planned job instruction changed")
        if config["agents"][0].get("env", {}).get(
            "LIBGEN_EXTERNAL_KNOWLEDGE_DIGEST"
        ) != condition["condition_digest"]:
            raise ExternalKnowledgeBuildError("planned job condition label changed")
        if config.get("artifacts") != _merged_artifacts(config.get("artifacts", [])):
            raise ExternalKnowledgeBuildError("planned job omits verifier diagnostics")
        expected_trials += len(TARGET_PROTOCOL_IDS) * int(config["n_attempts"])
    if expected_trials != lock.get("expected_trial_count"):
        raise ExternalKnowledgeBuildError("planned trial count changed")
    for script in lock["run_scripts"]:
        path = Path(script["path"])
        if sha256_file(path) != script["sha256"]:
            raise ExternalKnowledgeBuildError(f"run script changed: {path}")
    return {
        "status": "pass",
        "plan_digest": lock["plan_digest"],
        "planned_job_count": len(lock["planned_jobs"]),
        "expected_trial_count": expected_trials,
    }


def _condition_instruction(
    *,
    condition_id: str,
    condition_digest: str,
    included_assets: Sequence[str],
) -> str:
    asset_description = {
        "general_methods_v1": (
            "a general molecular-method primer and its evidence-location table"
        ),
        "cross_protocol_memory_v1": (
            "prediction-shaped worked examples from five donor protocols"
        ),
        "general_methods_plus_memory_v1": (
            "the general molecular-method primer, its evidence-location table, and "
            "prediction-shaped worked examples from five donor protocols"
        ),
    }[condition_id]
    memory_clause = ""
    if "cross_protocol_memory_v1" in included_assets:
        memory_clause = (
            "\n\n"
            f"> {MEMORY_WARNING}\n\n"
            "Shared exact sequences may occur across platforms. Treat them as prior "
            "worked-example memory, never as proof that the target uses the same "
            "sequence or molecular step."
        )
    primer_clause = ""
    if "general_molecular_methods_v1" in included_assets:
        primer_clause = (
            "\n\nThe molecular-method primer is general background. Its cards constrain "
            "physical interpretation but do not establish that any operation occurred "
            "in this target protocol. Source-limit and artifact-warning cards must be "
            "followed literally."
        )
    return (
        "# Frozen external-knowledge intervention\n\n"
        f"Condition: `{condition_id}`  \n"
        f"Condition digest: `{condition_digest}`\n\n"
        "This trial intentionally adds one approved external-knowledge condition to "
        "the unchanged baseline task. As a narrow exception to the baseline source-only "
        "instruction, you may and should read every regular file under "
        f"`{EXTERNAL_KNOWLEDGE_MOUNT_TARGET}/`. The mounted files contain "
        f"{asset_description}. They are the only additional knowledge assets permitted "
        "for this trial. Do not use web search, remembered kit knowledge, benchmark "
        "ground truth, prior target answers, audit records, or any unlisted files."
        f"{primer_clause}{memory_clause}\n\n"
        f"{PRECEDENCE_RULE} If an external-knowledge item conflicts with, extends, or "
        "is not supported by the target protocol's own supplied sources, follow the "
        "target sources and preserve the gap instead of importing the external claim.\n\n"
        "The required T2/T3 output paths, schemas, linked validation, and all other "
        "task rules are unchanged.\n"
    )


def _validate_target_tasks_use_separate_verifier(tasks_root: Path) -> None:
    for protocol_id in TARGET_PROTOCOL_IDS:
        task_root = tasks_root / protocol_id
        task_toml = task_root / "task.toml"
        if not task_toml.is_file():
            raise ExternalKnowledgeBuildError(f"missing target task: {task_root}")
        text = task_toml.read_text(encoding="utf-8")
        verifier_match = re.search(
            r"(?ms)^\[verifier\]\s*$\n(?P<body>.*?)(?=^\[[^\n]+\]\s*$|\Z)",
            text,
        )
        if verifier_match is None or re.search(
            r'(?m)^environment_mode\s*=\s*"separate"\s*$',
            verifier_match.group("body"),
        ) is None:
            raise ExternalKnowledgeBuildError(
                f"target task does not use a separate verifier: {protocol_id}"
            )


def _validate_baseline_job_config(config: Mapping[str, Any]) -> tuple[str, str]:
    base_name = config.get("job_name")
    if not isinstance(base_name, str) or not base_name:
        raise ExternalKnowledgeBuildError("baseline job config lacks a job name")
    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise ExternalKnowledgeBuildError(
            f"baseline job must contain exactly one agent: {base_name}"
        )
    agent_name = agents[0].get("name")
    if not isinstance(agent_name, str) or not agent_name:
        raise ExternalKnowledgeBuildError(f"baseline agent lacks a name: {base_name}")
    if config.get("tasks"):
        raise ExternalKnowledgeBuildError(
            f"baseline config mixes explicit tasks with its dataset: {base_name}"
        )
    if config.get("extra_instruction_paths"):
        raise ExternalKnowledgeBuildError(
            f"baseline config already has extra instructions: {base_name}"
        )
    environment = config.get("environment", {})
    if environment.get("type") not in {None, "docker"}:
        raise ExternalKnowledgeBuildError(
            f"baseline config is not a Docker run: {base_name}"
        )
    if environment.get("mounts"):
        raise ExternalKnowledgeBuildError(
            f"baseline config already has host mounts: {base_name}"
        )
    if int(config.get("n_attempts", 1)) < 1:
        raise ExternalKnowledgeBuildError(f"invalid baseline attempts: {base_name}")
    if int(config.get("n_concurrent_trials", 4)) < 1:
        raise ExternalKnowledgeBuildError(f"invalid baseline concurrency: {base_name}")
    if config.get("retry", {}).get("max_retries", 0) != 0:
        raise ExternalKnowledgeBuildError(
            f"baseline config enables automatic retry: {base_name}"
        )
    datasets = config.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != 1:
        raise ExternalKnowledgeBuildError(
            f"baseline config must contain one task dataset: {base_name}"
        )
    return base_name, agent_name


def _load_integration_condition(
    integration_root: Path,
    condition_id: str,
) -> dict[str, Any]:
    return load_json(
        integration_root
        / "conditions"
        / condition_id
        / "integration_manifest.json"
    )


def _merged_artifacts(artifacts: Sequence[Any]) -> list[Any]:
    result = copy.deepcopy(list(artifacts))
    present = {
        item if isinstance(item, str) else item.get("source")
        for item in result
        if isinstance(item, (str, Mapping))
    }
    for artifact in VERIFIER_ARTIFACTS:
        if artifact not in present:
            result.append(artifact)
    return result


def _auth_preflight_lines(
    planned_jobs: Sequence[Mapping[str, Any]],
    *,
    base_name: str,
) -> list[str]:
    agent_names = {
        item["agent_name"]
        for item in planned_jobs
        if item["base_job_name"] == base_name
    }
    if len(agent_names) != 1:
        raise ExternalKnowledgeBuildError(
            f"cannot resolve auth preflight for baseline {base_name}"
        )
    agent_name = next(iter(agent_names))
    if agent_name == "codex":
        return ['export CODEX_FORCE_AUTH_JSON="${CODEX_FORCE_AUTH_JSON:-1}"']
    if agent_name == "claude-code":
        return ['export CLAUDE_FORCE_OAUTH="${CLAUDE_FORCE_OAUTH:-1}"']
    if agent_name == "antigravity-cli":
        return ['export AGY_FORCE_AUTH_JSON="${AGY_FORCE_AUTH_JSON:-1}"']
    if agent_name == "kimi-cli":
        return [
            'if [ -z "${MOONSHOT_API_KEY:-}" ] && [ -z "${KIMI_API_KEY:-}" ]; then',
            '  echo "Set MOONSHOT_API_KEY or KIMI_API_KEY before running Kimi." >&2',
            "  exit 2",
            "fi",
        ]
    return []


def _safe_relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ExternalKnowledgeBuildError("condition contains an invalid path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ExternalKnowledgeBuildError(f"unsafe condition path: {value!r}")
    return Path(*pure.parts)


def _directory_inventory(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ExternalKnowledgeBuildError(f"asset directory is missing: {root}")
    inventory: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ExternalKnowledgeBuildError(f"symlink is not allowed: {path}")
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = sha256_file(path)
    return inventory


def _inventory_digest(inventory: Mapping[str, str]) -> str:
    return canonical_digest(
        {
            "files": [
                {"path": path, "sha256": inventory[path]}
                for path in sorted(inventory)
            ]
        }
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _with_digest(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    result[field] = canonical_digest(result)
    return result


def _validate_digest(document: Mapping[str, Any], field: str) -> None:
    actual = document.get(field)
    payload = {key: value for key, value in document.items() if key != field}
    expected = canonical_digest(payload)
    if actual != expected:
        raise ExternalKnowledgeBuildError(
            f"digest mismatch for {field}: expected {expected}, got {actual}"
        )
