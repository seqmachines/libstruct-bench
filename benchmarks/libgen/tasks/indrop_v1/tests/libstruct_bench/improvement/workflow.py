from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)

from .artifacts import (
    CapabilityImprovementError,
    REQUIRED_PATHS,
    copy_capability_pack,
    freeze_tree,
    improvement_schema_root,
    is_editable_pack_path,
    load_capability_control_bundle,
    load_and_validate,
    normalized_timestamp,
    safe_relative_path,
    thaw_tree,
    validate_capability_pack,
    with_digest,
    write_capability_pack_manifest,
)
from .learning_ledger import validate_learning_ledger
from .lineage import (
    ACTIVE_BRANCH,
    batch_for_protocol_count,
    checkpoint_id,
    parent_checkpoint,
    require_active_branch,
)
from .mutation_lock import guard_experiment_mutation
from .exemplar_memory import (
    create_empty_exemplar_memory,
    ensure_exemplar_identity_map,
    exemplar_memory_record,
    extend_exemplar_memory_from_packet,
    validate_exemplar_identity_map,
    validate_exemplar_memory,
)


PROPOSAL_SCHEMA_VERSION = "libstruct.libgen_capability_proposal.v1"
DECISION_SCHEMA_VERSION = "libstruct.libgen_capability_decision.v1"
APPLICATION_SCHEMA_VERSION = "libstruct.libgen_capability_application.v1"
CHECKPOINT_SCHEMA_VERSION = "libstruct.libgen_capability_checkpoint.v1"
CHECKPOINT_RUNTIME_SCHEMA_VERSION = "libstruct.libgen_capability_runtime.v1"
NUCLEOTIDE_RUN_RE = re.compile(r"(?<![A-Za-z])[ACGTRYSWKMBDHVN]{8,}(?![A-Za-z])", re.I)
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".txt",
    ".tsv",
    ".csv",
    ".toml",
    ".yaml",
    ".yml",
}

CHECKPOINT_REQUIRED_READ_ORDER = (
    "pack/PLAYBOOK.md",
    "pack/checklists/evidence_ledger.md",
    "pack/checklists/state_conservation.md",
    "pack/checklists/transition_accounting.md",
    "pack/checklists/final_graph_audit.md",
)
CHECKPOINT_RESOURCE_ROOTS = {
    "checklists": "pack/checklists",
    "schemas": "pack/schemas",
    "tools": "pack/tools",
    "synthetic_tests": "pack/synthetic_tests",
    "adapters": "pack/adapters",
}
EXEMPLAR_ACCESS_BY_MAX_RESULTS = {
    1: "query_only_maximum_one_retrieved_exemplar",
    2: "query_only_maximum_two_retrieved_exemplars",
    3: "query_only_maximum_three_retrieved_exemplars",
}


def validate_capability_proposal(
    *,
    experiment_manifest: Mapping[str, Any],
    access_policy_path: Path,
    proposal_path: Path,
    candidate_root: Path,
    parent_pack_root: Path,
    packet_path: Path | None = None,
    prior_proposal_path: Path | None = None,
    revision_decision_path: Path | None = None,
) -> dict[str, Any]:
    from .governance import (
        assert_transfer_panel_isolation,
        validate_transfer_access_policy,
    )

    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    parent = validate_capability_pack(parent_pack_root)
    policy = validate_transfer_access_policy(access_policy_path)
    experiment_root = access_policy_path.expanduser().resolve().parents[1]
    from .validation import (
        assert_validation_learning_artifact_isolated,
        scan_validation_feedback_copy,
        scan_validation_pack_leakage,
        validate_referenced_validation_access_policy,
        validate_validation_guidance_record,
    )

    validation_policy = validate_referenced_validation_access_policy(
        experiment_root=experiment_root,
        experiment_manifest=experiment_manifest,
    )
    panel = experiment_manifest["frozen_retrospective_transfer_panel"]
    if policy["policy_digest"] != panel["access_policy"]["digest"]:
        raise CapabilityImprovementError("proposal access policy is stale")
    if proposal["experiment_digest"] != experiment_manifest["experiment_digest"]:
        raise CapabilityImprovementError("proposal belongs to another experiment")
    if proposal["parent_pack_digest"] != parent["pack_digest"]:
        raise CapabilityImprovementError("proposal references a stale parent pack")
    if packet_path is not None:
        packet = load_and_validate(
            packet_path,
            schema_filename="batch_packet.schema.json",
            digest_field="packet_digest",
            label="capability batch packet",
        )
        if proposal["packet_digest"] != packet["packet_digest"]:
            raise CapabilityImprovementError("proposal references a stale batch packet")
        for key in (
            "experiment_digest",
            "branch",
            "batch_id",
            "parent_pack_digest",
            "protocol_ids",
        ):
            if proposal[key] != packet[key]:
                raise CapabilityImprovementError(f"proposal {key} differs from packet")
        if packet["reveal_state"] != "revealed":
            raise CapabilityImprovementError(
                "capability proposals require a revealed batch packet"
            )
        if packet["eligibility_status"] != "eligible_for_improvement":
            raise CapabilityImprovementError(
                "capability packet is superseded or ineligible"
            )
        if packet["transfer_access_policy_digest"] != policy["policy_digest"]:
            raise CapabilityImprovementError(
                "capability packet uses another access policy"
            )
        if packet["experiment_digest"] != experiment_manifest["experiment_digest"]:
            raise CapabilityImprovementError(
                "capability packet belongs to another experiment"
            )
        assert_transfer_panel_isolation(
            protocol_ids=packet["protocol_ids"],
            artifacts=packet["artifacts"],
            policy=policy,
        )
        assert_validation_learning_artifact_isolated(
            packet,
            validation_access_policy=validation_policy,
            label="capability batch packet",
        )
        ledger = validate_learning_ledger(
            packet.get("learning_ledger"),
            batch_id=packet["batch_id"],
            protocol_ids=packet["protocol_ids"],
            artifacts=packet["artifacts"],
            require_revealed=True,
        )
        assert ledger is not None
        if proposal["learning_ledger_digest"] != ledger["ledger_digest"]:
            raise CapabilityImprovementError(
                "proposal references a stale capability learning ledger"
            )
    else:
        ledger = None
    validation_feedback = validate_validation_guidance_record(
        proposal["validation_guidance"],
        experiment_root=experiment_root,
        experiment_digest=experiment_manifest["experiment_digest"],
        validation_access_policy=validation_policy,
        batch_id=proposal["batch_id"],
        expected_pack_digest=parent["pack_digest"],
        workspace_search_root=(
            packet_path.parent if packet_path is not None else proposal_path.parent
        ),
    )
    _validate_revision_lineage(
        proposal,
        prior_proposal_path=prior_proposal_path,
        revision_decision_path=revision_decision_path,
    )
    candidate_root = candidate_root.expanduser().resolve()
    if not candidate_root.is_dir():
        raise CapabilityImprovementError(f"candidate root is missing: {candidate_root}")
    changes = proposal["change_units"]
    change_ids = [item["change_id"] for item in changes]
    if len(change_ids) != len(set(change_ids)):
        raise CapabilityImprovementError("proposal contains duplicate change IDs")
    expected_candidate_files: set[str] = set()
    parent_files = {item["path"]: item["sha256"] for item in parent["files"]}
    cluster_index = {
        item["cluster_id"]: item for item in (ledger or {}).get("clusters", [])
    }
    mutation_paths: list[str] = []
    mutation_owner: dict[str, str] = {}
    for change in changes:
        unit_id = change["change_id"]
        _validate_unit_semantics(
            change, cluster_index=cluster_index if ledger else None
        )
        for mutation in change["mutations"]:
            path = safe_relative_path(mutation["path"])
            relative = path.as_posix()
            mutation_paths.append(relative)
            mutation_owner[relative] = unit_id
            if not is_editable_pack_path(relative):
                raise CapabilityImprovementError(
                    "proposal attempts to edit immutable or out-of-scope path: "
                    + relative
                )
            operation = mutation["operation"]
            baseline_sha = mutation["baseline_sha256"]
            candidate_sha = mutation["candidate_sha256"]
            current_sha = parent_files.get(relative)
            if operation == "add":
                if (
                    current_sha is not None
                    or baseline_sha is not None
                    or candidate_sha is None
                ):
                    raise CapabilityImprovementError(
                        f"invalid add mutation for {relative}"
                    )
            elif operation == "replace":
                if (
                    current_sha is None
                    or baseline_sha != current_sha
                    or candidate_sha is None
                ):
                    raise CapabilityImprovementError(
                        f"stale replace mutation for {relative}"
                    )
            elif operation == "remove":
                if (
                    current_sha is None
                    or baseline_sha != current_sha
                    or candidate_sha is not None
                ):
                    raise CapabilityImprovementError(
                        f"stale remove mutation for {relative}"
                    )
                if relative in REQUIRED_PATHS:
                    raise CapabilityImprovementError(
                        f"required pack file cannot be removed: {relative}"
                    )
            if operation != "remove":
                candidate = candidate_root / path
                if candidate.is_symlink() or not candidate.is_file():
                    raise CapabilityImprovementError(
                        f"candidate file is missing: {relative}"
                    )
                if sha256_file(candidate) != candidate_sha:
                    raise CapabilityImprovementError(
                        f"candidate hash is stale: {relative}"
                    )
                expected_candidate_files.add(relative)
        if not change["mutations"]:
            raise CapabilityImprovementError(
                f"capability unit {unit_id} contains no mutations"
            )
    if len(mutation_paths) != len(set(mutation_paths)):
        raise CapabilityImprovementError(
            "proposal may mutate a path in only one atomic capability unit"
        )
    for change in changes:
        resulting_files = set(parent_files)
        for mutation in change["mutations"]:
            if mutation["operation"] == "remove":
                resulting_files.discard(mutation["path"])
            else:
                resulting_files.add(mutation["path"])
        for path_value in change["enforcement_paths"]:
            relative = safe_relative_path(path_value).as_posix()
            owner = mutation_owner.get(relative)
            if owner is not None and owner != change["change_id"]:
                raise CapabilityImprovementError(
                    f"capability unit {change['change_id']} depends on a mutation "
                    f"owned by another unit: {relative}"
                )
            if relative not in resulting_files:
                raise CapabilityImprovementError(
                    f"capability unit {change['change_id']} cites missing enforcement: "
                    f"{relative}"
                )
        for fixture in change["fixtures"]:
            relative = safe_relative_path(fixture["path"]).as_posix()
            owner = mutation_owner.get(relative)
            if owner is not None and owner != change["change_id"]:
                raise CapabilityImprovementError(
                    f"capability unit {change['change_id']} depends on a fixture "
                    f"mutation owned by another unit: {relative}"
                )
            if not relative.startswith("synthetic_tests/"):
                raise CapabilityImprovementError(
                    f"capability unit {change['change_id']} cites a non-synthetic fixture"
                )
            if relative not in resulting_files:
                raise CapabilityImprovementError(
                    f"capability unit {change['change_id']} cites missing fixture: "
                    f"{relative}"
                )
    actual_candidate_files: set[str] = set()
    for path in sorted(candidate_root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(f"candidate symlink is forbidden: {path}")
        if path.is_file():
            actual_candidate_files.add(path.relative_to(candidate_root).as_posix())
    if actual_candidate_files != expected_candidate_files:
        raise CapabilityImprovementError(
            "candidate root does not exactly match proposed add/replace files; "
            f"missing={sorted(expected_candidate_files - actual_candidate_files)}, "
            f"extra={sorted(actual_candidate_files - expected_candidate_files)}"
        )
    assert_validation_learning_artifact_isolated(
        proposal,
        validation_access_policy=validation_policy,
        label="capability proposal",
    )
    validation_leakage = scan_validation_pack_leakage(
        candidate_root,
        validation_policy,
    )
    if validation_leakage:
        raise CapabilityImprovementError(
            "candidate bytes cross the validation learning boundary: "
            + "; ".join(validation_leakage[:8])
        )
    copied_feedback = scan_validation_feedback_copy(
        candidate_root,
        validation_feedback,
    )
    if copied_feedback:
        raise CapabilityImprovementError(
            "candidate bytes copy validation score payloads: "
            + "; ".join(copied_feedback[:8])
        )
    return proposal


def _validate_unit_semantics(
    unit: Mapping[str, Any],
    *,
    cluster_index: Mapping[str, Mapping[str, Any]] | None,
) -> None:
    unit_id = unit["change_id"]
    if unit["update_type"] != "procedural_or_tool":
        raise CapabilityImprovementError(
            f"capability unit {unit_id} is not a procedural/tool update"
        )
    applicability = {item.strip() for item in unit["applicability"]}
    exclusions = {item.strip() for item in unit["exclusions"]}
    if not applicability or "" in applicability or "" in exclusions:
        raise CapabilityImprovementError(
            f"capability unit {unit_id} has an empty applicability boundary"
        )
    if applicability & exclusions:
        raise CapabilityImprovementError(
            f"capability unit {unit_id} has overlapping applicability and exclusions"
        )
    if cluster_index is not None:
        unknown = set(unit["cluster_ids"]) - set(cluster_index)
        if unknown:
            raise CapabilityImprovementError(
                f"capability unit {unit_id} cites unknown learning clusters: "
                + ", ".join(sorted(unknown))
            )
        cited = [cluster_index[item] for item in unit["cluster_ids"]]
        available_codes = {
            code for cluster in cited for code in cluster["finding_codes"]
        }
        if not set(unit["finding_codes"]) <= available_codes:
            raise CapabilityImprovementError(
                f"capability unit {unit_id} invents finding codes outside its clusters"
            )
        expected_evidence = {
            (
                item["protocol_id"],
                item["artifact_sha256"],
                item["json_pointer"],
            ): item
            for cluster in cited
            for item in cluster["evidence_refs"]
        }
        observed_evidence = {
            (
                item["protocol_id"],
                item["artifact_sha256"],
                item["json_pointer"],
            ): item
            for item in unit["evidence_refs"]
        }
        if observed_evidence != expected_evidence or len(observed_evidence) != len(
            unit["evidence_refs"]
        ):
            raise CapabilityImprovementError(
                f"capability unit {unit_id} evidence differs from its trusted clusters"
            )
    classification = unit["capability_class"]
    fixture_ids = [item["case_id"] for item in unit["fixtures"]]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise CapabilityImprovementError(
            f"capability unit {unit_id} repeats a fixture case ID"
        )
    regression_ids = set(unit["synthetic_regression_case_ids"])
    if not regression_ids <= set(fixture_ids):
        raise CapabilityImprovementError(
            f"capability unit {unit_id} cites an unknown synthetic regression"
        )
    admission_basis = unit["admission_basis"]
    if admission_basis == "recurring_root_error":
        if regression_ids:
            raise CapabilityImprovementError(
                f"recurrence-backed capability unit {unit_id} cannot claim a "
                "synthetic-regression admission basis"
            )
        if cluster_index is not None and not any(
            cluster_index[cluster_id]["root_error_status"]
            == "recurring_across_protocols"
            for cluster_id in unit["cluster_ids"]
        ):
            raise CapabilityImprovementError(
                f"capability unit {unit_id} does not cite a recurring root error"
            )
    elif admission_basis == "general_invariant_with_synthetic_regression":
        fixture_by_id = {item["case_id"]: item for item in unit["fixtures"]}
        selected = [fixture_by_id[case_id] for case_id in sorted(regression_ids)]
        mutation_paths = {item["path"] for item in unit["mutations"]}
        if (
            not selected
            or not any(item["polarity"] == "negative" for item in selected)
            or any(
                not item["path"].startswith("synthetic_tests/")
                or item["path"] not in mutation_paths
                for item in selected
            )
        ):
            raise CapabilityImprovementError(
                f"capability unit {unit_id} requires a mutated negative synthetic "
                "regression for its general invariant"
            )
    elif regression_ids:
        raise CapabilityImprovementError(
            f"insufficient capability unit {unit_id} cannot claim a regression"
        )
    polarities = {item["polarity"] for item in unit["fixtures"]}
    if classification in {"deterministic", "hybrid"}:
        if not unit["enforcement_paths"]:
            raise CapabilityImprovementError(
                f"{classification} capability unit {unit_id} requires enforcement"
            )
        if polarities != {"positive", "negative", "boundary"}:
            raise CapabilityImprovementError(
                f"{classification} capability unit {unit_id} requires positive, "
                "negative, and boundary fixtures"
            )
        if unit["instruction_only_rationale"] is not None:
            raise CapabilityImprovementError(
                f"{classification} capability unit {unit_id} cannot use an "
                "instruction-only rationale"
            )
    if classification == "deterministic" and unit["residual_judgment"] is not None:
        raise CapabilityImprovementError(
            f"deterministic capability unit {unit_id} cannot leave residual judgment"
        )
    if classification == "hybrid" and not unit["residual_judgment"]:
        raise CapabilityImprovementError(
            f"hybrid capability unit {unit_id} must state residual judgment"
        )
    if classification == "instruction_only":
        if not unit["instruction_only_rationale"]:
            raise CapabilityImprovementError(
                f"instruction-only capability unit {unit_id} requires a rationale"
            )
        if unit["enforcement_paths"]:
            raise CapabilityImprovementError(
                f"instruction-only capability unit {unit_id} cannot claim enforcement"
            )
        if not unit["residual_judgment"]:
            raise CapabilityImprovementError(
                f"instruction-only capability unit {unit_id} must state the "
                "remaining model judgment"
            )


def _validate_unit_acceptance_admission(unit: Mapping[str, Any]) -> None:
    if unit["admission_basis"] == "insufficient":
        raise CapabilityImprovementError(
            f"capability unit {unit['change_id']} lacks recurring root-error or "
            "general-invariant synthetic-regression support"
        )


def render_capability_review(proposal: Mapping[str, Any]) -> str:
    lines = [
        f"Capability proposal {proposal['proposal_id']} — {len(proposal['change_units'])} change unit(s)",
        f"Branch / batch: {proposal['branch']} / {proposal['batch_id']}",
        f"Parent: {proposal['checkpoint_from']} ({proposal['parent_pack_digest']})",
    ]
    for index, change in enumerate(proposal["change_units"], start=1):
        evidence = "; ".join(
            f"{item['protocol_id']} {item['artifact_sha256'][:12]}… "
            f"{item['json_pointer']}"
            for item in change["evidence_refs"]
        )
        mutations = ", ".join(
            f"{item['operation']} {item['path']}" for item in change["mutations"]
        )
        lines.extend(
            [
                "",
                f"[{index}] {change['change_id']} — {change['capability_class']}",
                f"Update / admission: {change['update_type']} / "
                f"{change['admission_basis']}",
                f"Atomic mutations: {mutations}",
                f"Clusters: {', '.join(change['cluster_ids'])}",
                f"Pattern: {change['generalized_failure_pattern']}",
                f"Invariant: {change['expected_invariant']}",
                f"Evidence: {evidence}",
                "Decision: accept / reject / modify / unresolved",
            ]
        )
    return "\n".join(lines) + "\n"


def create_decision_template(
    *,
    proposal_path: Path,
    reviewer_kind: str,
    reviewer_id: str,
    started_at: str,
    reviewer_model: str | None = None,
    reviewer_version: str | None = None,
    transcript_sha256: str | None = None,
) -> dict[str, Any]:
    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    require_active_branch(proposal["branch"])
    if reviewer_kind not in {"independent_codex", "human"}:
        raise CapabilityImprovementError(
            "reviewer_kind must be independent_codex or human"
        )
    payload: dict[str, Any] = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "decision_id": proposal["proposal_id"] + ":decision",
        "proposal_digest": proposal["proposal_digest"],
        "proposal_sha256": sha256_file(proposal_path),
        "branch": proposal["branch"],
        "batch_id": proposal["batch_id"],
        "reviewer_kind": reviewer_kind,
        "reviewer": {
            "reviewer_id": reviewer_id,
            "model": reviewer_model,
            "version": reviewer_version,
            "transcript_sha256": transcript_sha256,
        },
        "revision_round": proposal["revision_round"],
        "review_state": "in_progress",
        "started_at": normalized_timestamp(started_at),
        "completed_at": None,
        "change_decisions": [],
    }
    return with_digest(payload, "decision_digest")


def record_change_decision(
    *,
    proposal_path: Path,
    decision_path: Path,
    change_id: str,
    disposition: str,
    rationale: str,
    revision_instruction: str | None = None,
) -> dict[str, Any]:
    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    decision = load_and_validate(
        decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="capability decision",
    )
    _validate_decision_identity(proposal_path, proposal, decision)
    if decision["review_state"] != "in_progress":
        raise CapabilityImprovementError("only an in-progress decision can be updated")
    known = {item["change_id"] for item in proposal["change_units"]}
    if change_id not in known:
        raise CapabilityImprovementError(f"unknown change ID: {change_id}")
    if disposition not in {"accept", "reject", "modify", "unresolved"}:
        raise CapabilityImprovementError(f"invalid disposition: {disposition}")
    if disposition == "modify":
        if proposal["revision_round"] >= 1:
            raise CapabilityImprovementError(
                "the one revision round has already been used"
            )
        if not revision_instruction or not revision_instruction.strip():
            raise CapabilityImprovementError("modify requires a revision instruction")
    elif revision_instruction is not None:
        raise CapabilityImprovementError(
            "revision_instruction is valid only for modify"
        )
    values = [
        item for item in decision["change_decisions"] if item["change_id"] != change_id
    ]
    unit = next(
        item for item in proposal["change_units"] if item["change_id"] == change_id
    )
    if disposition == "accept":
        _validate_unit_acceptance_admission(unit)
        already_accepted = sum(item["disposition"] == "accept" for item in values)
        if already_accepted >= 1:
            raise CapabilityImprovementError(
                "a five-protocol batch may accept at most one procedural/tool update"
            )
    values.append(
        {
            "change_id": change_id,
            "mutation_paths": sorted(
                mutation["path"] for mutation in unit["mutations"]
            ),
            "atomicity_verified": True,
            "disposition": disposition,
            "rationale": rationale.strip(),
            "revision_instruction": revision_instruction,
        }
    )
    if not rationale.strip():
        raise CapabilityImprovementError("decision rationale is required")
    updated = {
        key: value for key, value in decision.items() if key != "decision_digest"
    }
    updated["change_decisions"] = sorted(values, key=lambda item: item["change_id"])
    result = with_digest(updated, "decision_digest")
    validate_document(
        result,
        improvement_schema_root() / "capability_decision.schema.json",
        label="capability decision",
    )
    write_json_atomic(decision_path, result)
    return result


def finalize_capability_decision(
    *,
    proposal_path: Path,
    decision_path: Path,
    completed_at: str,
) -> dict[str, Any]:
    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    decision = load_and_validate(
        decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="capability decision",
    )
    _validate_decision_identity(proposal_path, proposal, decision)
    expected = {item["change_id"] for item in proposal["change_units"]}
    observed = {item["change_id"] for item in decision["change_decisions"]}
    if observed != expected or len(observed) != len(decision["change_decisions"]):
        raise CapabilityImprovementError(
            "final decision must decide every change exactly once"
        )
    dispositions = {item["disposition"] for item in decision["change_decisions"]}
    accepted = [
        item for item in decision["change_decisions"] if item["disposition"] == "accept"
    ]
    if len(accepted) > 1:
        raise CapabilityImprovementError(
            "a five-protocol batch may accept at most one procedural/tool update"
        )
    units = {item["change_id"]: item for item in proposal["change_units"]}
    for item in accepted:
        _validate_unit_acceptance_admission(units[item["change_id"]])
    if proposal["revision_round"] == 1 and "modify" in dispositions:
        raise CapabilityImprovementError("no second proposal revision is permitted")
    state = "revision_requested" if "modify" in dispositions else "final"
    updated = {
        key: value for key, value in decision.items() if key != "decision_digest"
    }
    updated["review_state"] = state
    updated["completed_at"] = normalized_timestamp(completed_at)
    result = with_digest(updated, "decision_digest")
    validate_document(
        result,
        improvement_schema_root() / "capability_decision.schema.json",
        label="capability decision",
    )
    write_json_atomic(decision_path, result, mode=0o444)
    return result


def validate_capability_decision(
    *,
    proposal_path: Path,
    decision_path: Path,
    require_final: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    decision = load_and_validate(
        decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="capability decision",
    )
    _validate_decision_identity(proposal_path, proposal, decision)
    expected = {item["change_id"] for item in proposal["change_units"]}
    values = decision["change_decisions"]
    observed = [item["change_id"] for item in values]
    if len(observed) != len(set(observed)) or set(observed) - expected:
        raise CapabilityImprovementError(
            "decision contains duplicate or unknown changes"
        )
    if require_final and decision["review_state"] != "final":
        raise CapabilityImprovementError("capability decision is not final")
    if (
        decision["review_state"] in {"final", "revision_requested"}
        and set(observed) != expected
    ):
        raise CapabilityImprovementError("completed review must decide every change")
    if decision["review_state"] == "revision_requested":
        if proposal["revision_round"] != 0 or not any(
            item["disposition"] == "modify" for item in values
        ):
            raise CapabilityImprovementError("invalid revision request")
    if proposal["revision_round"] == 1 and any(
        item["disposition"] == "modify" for item in values
    ):
        raise CapabilityImprovementError("one-revision policy was exceeded")
    units = {item["change_id"]: item for item in proposal["change_units"]}
    accepted = [item for item in values if item["disposition"] == "accept"]
    if len(accepted) > 1:
        raise CapabilityImprovementError(
            "a five-protocol batch may accept at most one procedural/tool update"
        )
    for item in values:
        expected_paths = sorted(
            mutation["path"] for mutation in units[item["change_id"]]["mutations"]
        )
        if (
            item["mutation_paths"] != expected_paths
            or item["atomicity_verified"] is not True
        ):
            raise CapabilityImprovementError(
                f"decision does not cover the complete atomic unit: {item['change_id']}"
            )
        if item["disposition"] == "accept":
            _validate_unit_acceptance_admission(units[item["change_id"]])
    return proposal, decision


def scan_capability_leakage(
    pack_root: Path,
    policy: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    forbidden_terms = {
        str(item).strip().lower()
        for item in policy.get("forbidden_terms", [])
        if str(item).strip()
    }
    forbidden_sequences = {
        re.sub(r"[^ACGTRYSWKMBDHVN]", "", str(item).upper())
        for item in policy.get("forbidden_sequences", [])
    }
    forbidden_sequences.discard("")
    forbidden_scaffolds = {
        str(item).strip().lower()
        for item in policy.get("forbidden_scaffolds", [])
        if str(item).strip()
    }
    allowed_synthetic = {
        safe_relative_path(str(item)).as_posix()
        for item in policy.get("allowed_synthetic_paths", [])
    }
    for path in sorted(pack_root.rglob("*")):
        if (
            not path.is_file()
            or path.name == "manifest.json"
            or path.suffix.lower() not in TEXT_SUFFIXES
        ):
            continue
        relative = path.relative_to(pack_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(f"non-UTF-8 pack text: {relative}")
            continue
        lowered = text.lower()
        for term in sorted(forbidden_terms):
            if term in lowered:
                issues.append(f"forbidden protocol-specific term in {relative}: {term}")
        for scaffold in sorted(forbidden_scaffolds):
            if scaffold in lowered:
                issues.append(f"forbidden family scaffold in {relative}: {scaffold}")
        if relative in allowed_synthetic:
            continue
        for match in NUCLEOTIDE_RUN_RE.finditer(text):
            run = match.group(0).upper()
            rc = _reverse_complement(run)
            if any(
                run in sequence or rc in sequence or sequence in run or sequence in rc
                for sequence in forbidden_sequences
            ):
                issues.append(
                    f"ground-truth sequence overlap in {relative} at byte {match.start()}"
                )
    return sorted(set(issues))


def run_pack_synthetic_suite_docker(
    pack_root: Path,
    *,
    image: str = "python:3.13-slim",
    timeout_sec: int = 120,
) -> list[str]:
    return list(
        run_pack_synthetic_suite_docker_report(
            pack_root,
            image=image,
            timeout_sec=timeout_sec,
        )["issues"]
    )


def run_pack_synthetic_suite_docker_report(
    pack_root: Path,
    *,
    image: str = "python:3.13-slim",
    timeout_sec: int = 120,
) -> dict[str, Any]:
    """Run every registered case in a read-only, networkless container.

    The report deliberately records stable case IDs so proposal/application
    lineage can cite the exact executed positive, negative, and boundary cases.
    """

    root = pack_root.expanduser().resolve()
    _, suite = load_capability_control_bundle(root)
    issues: list[str] = []
    executed: list[str] = []
    for case in suite["cases"]:
        case_id = case["case_id"]
        argv = [
            value.replace("{pack}", "/pack").replace("{tmp}", f"/tmp/cases/{case_id}")
            for value in case["argv"]
        ]
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "--volume",
            f"{root.as_posix()}:/pack:ro",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=32m",
            "--workdir",
            "/tmp",
            image,
            *argv,
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            issues.append(f"sandbox execution failed for {case_id}: {error}")
            continue
        executed.append(case_id)
        expected = case["expected"]
        try:
            output = json.loads(result.stdout)
            if not isinstance(output, dict):
                raise ValueError("output is not a JSON object")
        except (json.JSONDecodeError, ValueError) as error:
            issues.append(
                f"synthetic case {case_id} returned invalid JSON: {error}: "
                + (result.stderr.strip() or result.stdout.strip())[:500]
            )
            continue
        codes = Counter(
            item.get("code")
            for item in output.get("findings", [])
            if isinstance(item, Mapping) and isinstance(item.get("code"), str)
        )
        if not codes and isinstance(output.get("code"), str):
            codes[output["code"]] += 1
        actual_codes = dict(sorted(codes.items()))
        mismatches: list[str] = []
        if result.returncode != expected["exit_code"]:
            mismatches.append(
                f"exit={result.returncode}, expected={expected['exit_code']}"
            )
        if output.get("status") != expected["status"]:
            mismatches.append(
                f"status={output.get('status')!r}, expected={expected['status']!r}"
            )
        if actual_codes != expected["finding_codes"]:
            mismatches.append(
                f"codes={actual_codes!r}, expected={expected['finding_codes']!r}"
            )
        if mismatches:
            issues.append(
                f"synthetic case {case_id} mismatch: " + "; ".join(mismatches)
            )
    return {
        "schema_version": "libstruct.libgen_capability_synthetic_suite_report.v1",
        "suite_sha256": sha256_file(root / "synthetic_tests" / "suite.json"),
        "executed_case_ids": executed,
        "case_count": len(suite["cases"]),
        "status": "fail" if issues else "pass",
        "issues": issues,
    }


@guard_experiment_mutation("apply deterministic capability decision")
def apply_capability_decision(
    *,
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
    access_policy_path: Path,
    proposal_path: Path,
    decision_path: Path,
    candidate_root: Path,
    parent_pack_root: Path,
    output_dir: Path,
    created_at: str,
    packet_path: Path | None = None,
    prior_proposal_path: Path | None = None,
    revision_decision_path: Path | None = None,
    leakage_policy: Mapping[str, Any] | None = None,
    synthetic_runner: Callable[[Path], Sequence[str]]
    | None = run_pack_synthetic_suite_docker,
) -> dict[str, Any]:
    from .governance import assert_capability_modification_open

    assert_capability_modification_open(
        experiment_root,
        experiment_digest=experiment_manifest["experiment_digest"],
    )
    proposal = validate_capability_proposal(
        experiment_manifest=experiment_manifest,
        access_policy_path=access_policy_path,
        proposal_path=proposal_path,
        candidate_root=candidate_root,
        parent_pack_root=parent_pack_root,
        packet_path=packet_path,
        prior_proposal_path=prior_proposal_path,
        revision_decision_path=revision_decision_path,
    )
    _, decision = validate_capability_decision(
        proposal_path=proposal_path,
        decision_path=decision_path,
        require_final=True,
    )
    parent = validate_capability_pack(parent_pack_root)
    from .validation import (
        scan_validation_feedback_copy,
        scan_validation_pack_leakage,
        validate_required_validation_aggregate,
        validate_referenced_validation_access_policy,
    )

    validation_policy = validate_referenced_validation_access_policy(
        experiment_root=experiment_root,
        experiment_manifest=experiment_manifest,
    )
    validation_feedback = validate_required_validation_aggregate(
        experiment_root=experiment_root,
        experiment_digest=experiment_manifest["experiment_digest"],
        validation_access_policy=validation_policy,
        batch_id=proposal["batch_id"],
        expected_pack_digest=parent["pack_digest"],
    )
    accepted = {
        item["change_id"]
        for item in decision["change_decisions"]
        if item["disposition"] == "accept"
    }
    skipped = {item["change_id"] for item in decision["change_decisions"]} - accepted
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise CapabilityImprovementError(
            f"application output already exists: {output_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    validation = {
        "static": "pass",
        "leakage": "not_configured",
        "synthetic": "not_run",
        "issues": [],
    }
    status = "no_op" if not accepted else "applied"
    try:
        working = temporary / "pack"
        copy_capability_pack(parent_pack_root, working, freeze=False)
        thaw_tree(working)
        by_id = {item["change_id"]: item for item in proposal["change_units"]}
        for change_id in sorted(accepted):
            unit = by_id[change_id]
            for mutation in sorted(unit["mutations"], key=lambda item: item["path"]):
                target = working / safe_relative_path(mutation["path"])
                if mutation["operation"] == "remove":
                    target.unlink()
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(
                    candidate_root / safe_relative_path(mutation["path"]),
                    target,
                )
                target.chmod(
                    0o755
                    if target.suffix == ".py" and target.parent.name == "tools"
                    else 0o644
                )
        manifest_path = working / "manifest.json"
        manifest_path.unlink(missing_ok=True)
        write_capability_pack_manifest(working)
        try:
            candidate_manifest = validate_capability_pack(working)
        except CapabilityImprovementError as error:
            validation["static"] = "fail"
            validation["issues"].append(str(error))
            candidate_manifest = parent
        if leakage_policy is None:
            if accepted:
                validation["leakage"] = "fail"
                validation["issues"].append(
                    "leakage policy is required for a changed pack"
                )
        else:
            leakage = scan_capability_leakage(working, leakage_policy)
            validation["leakage"] = "fail" if leakage else "pass"
            validation["issues"].extend(leakage)
        validation_leakage = scan_validation_pack_leakage(
            working,
            validation_policy,
        )
        if validation_leakage:
            validation["leakage"] = "fail"
            validation["issues"].extend(validation_leakage)
        copied_feedback = scan_validation_feedback_copy(
            working,
            validation_feedback,
        )
        if copied_feedback:
            validation["leakage"] = "fail"
            validation["issues"].extend(copied_feedback)
        if synthetic_runner is None:
            if accepted:
                validation["synthetic"] = "fail"
                validation["issues"].append(
                    "sandboxed synthetic validation was not run"
                )
        else:
            synthetic_issues = list(synthetic_runner(working))
            validation["synthetic"] = "fail" if synthetic_issues else "pass"
            validation["issues"].extend(synthetic_issues)
        if validation["issues"]:
            status = "no_op_validation_failed"
            shutil.rmtree(working)
            copy_capability_pack(parent_pack_root, working, freeze=False)
            candidate_manifest = validate_capability_pack(working)
        freeze_tree(working)
        payload: dict[str, Any] = {
            "schema_version": APPLICATION_SCHEMA_VERSION,
            "application_id": proposal["proposal_id"] + ":application",
            "proposal_digest": proposal["proposal_digest"],
            "decision_digest": decision["decision_digest"],
            "parent_pack_digest": parent["pack_digest"],
            "validation_guidance": dict(proposal["validation_guidance"]),
            "status": status,
            "accepted_change_ids": sorted(accepted),
            "skipped_change_ids": sorted(skipped),
            "output_pack": {
                "path": "pack",
                "pack_digest": candidate_manifest["pack_digest"],
                "manifest_sha256": sha256_file(working / "manifest.json"),
            },
            "validation": validation,
            "created_at": normalized_timestamp(created_at),
        }
        application = with_digest(payload, "application_digest")
        validate_document(
            application,
            improvement_schema_root() / "capability_application.schema.json",
            label="capability application",
        )
        write_json_atomic(temporary / "application.json", application, mode=0o444)
        temporary.replace(output_dir)
        return application
    except BaseException:
        _discard_incomplete_frozen_tree(temporary)
        raise


@guard_experiment_mutation("freeze cumulative capability checkpoint")
def freeze_checkpoint(
    *,
    experiment_root: Path,
    experiment_digest: str,
    branch: str,
    batch_id: str,
    protocol_count: int,
    parent_checkpoint_id: str,
    proposal_path: Path,
    decision_path: Path,
    application_dir: Path,
    output_dir: Path,
    created_at: str,
    exemplar_max_results: int = 3,
) -> dict[str, Any]:
    from .governance import assert_capability_modification_open

    assert_capability_modification_open(
        experiment_root,
        experiment_digest=experiment_digest,
    )
    require_active_branch(branch)
    exemplar_max_results = _validate_exemplar_max_results(exemplar_max_results)
    checkpoint_id_value = checkpoint_id(protocol_count)
    expected_parent = parent_checkpoint(protocol_count)
    expected_batch = batch_for_protocol_count(protocol_count)
    if parent_checkpoint_id != expected_parent or batch_id != expected_batch:
        raise CapabilityImprovementError(
            "checkpoint lineage does not match protocol count"
        )
    root = experiment_root.expanduser().resolve()
    canonical_output = root / "checkpoints" / checkpoint_id_value
    if output_dir.expanduser().resolve() != canonical_output:
        raise CapabilityImprovementError(
            f"checkpoint output must use the canonical path: {canonical_output}"
        )
    parent_record, _, _ = validate_checkpoint_runtime(
        root / "checkpoints" / expected_parent
    )
    if parent_record["experiment_digest"] != experiment_digest:
        raise CapabilityImprovementError(
            "parent checkpoint belongs to another experiment"
        )
    application_path = application_dir / "application.json"
    application = load_and_validate(
        application_path,
        schema_filename="capability_application.schema.json",
        digest_field="application_digest",
        label="capability application",
    )
    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    decision = load_and_validate(
        decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="capability decision",
    )
    if proposal["branch"] != branch or proposal["batch_id"] != batch_id:
        raise CapabilityImprovementError(
            "checkpoint proposal belongs to another branch or batch"
        )
    if (
        application["proposal_digest"] != proposal["proposal_digest"]
        or application["decision_digest"] != decision["decision_digest"]
        or application["parent_pack_digest"] != parent_record["pack_digest"]
        or application["validation_guidance"] != proposal["validation_guidance"]
    ):
        raise CapabilityImprovementError("checkpoint application lineage is stale")
    experiment_manifest = load_and_validate(
        root / "design" / "experiment_manifest.json",
        schema_filename="experiment_manifest.schema.json",
        digest_field="experiment_digest",
        label="capability experiment manifest",
    )
    if experiment_manifest["experiment_digest"] != experiment_digest:
        raise CapabilityImprovementError(
            "checkpoint freeze belongs to another experiment manifest"
        )
    from .validation import (
        validate_referenced_validation_access_policy,
        validate_validation_guidance_record,
    )

    validation_policy = validate_referenced_validation_access_policy(
        experiment_root=root,
        experiment_manifest=experiment_manifest,
    )
    validate_validation_guidance_record(
        proposal["validation_guidance"],
        experiment_root=root,
        experiment_digest=experiment_digest,
        validation_access_policy=validation_policy,
        batch_id=batch_id,
        expected_pack_digest=parent_record["pack_digest"],
        workspace_search_root=proposal_path.parent,
    )
    source_pack = application_dir / application["output_pack"]["path"]
    pack = validate_capability_pack(source_pack)
    _assert_pack_validation_clean(
        experiment_root=experiment_root,
        experiment_digest=experiment_digest,
        pack_root=source_pack,
    )
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise CapabilityImprovementError(
            f"checkpoint output already exists: {output_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    try:
        copy_capability_pack(source_pack, temporary / "pack", freeze=True)
        split = _load_active_frozen_split(root, experiment_manifest)
        identity_map = ensure_exemplar_identity_map(
            experiment_root=root,
            split_digest=split["split_digest"],
        )
        packet_path = root / "rounds" / batch_id / branch / "packet.json"
        packet = load_and_validate(
            packet_path,
            schema_filename="batch_packet.schema.json",
            digest_field="packet_digest",
            label="capability batch packet",
        )
        if proposal["packet_digest"] != packet["packet_digest"]:
            raise CapabilityImprovementError(
                "checkpoint exemplar projection references a stale batch packet"
            )
        extend_exemplar_memory_from_packet(
            parent_memory_root=root / "checkpoints" / expected_parent / "memory",
            output_memory_root=temporary / "memory",
            packet_path=packet_path,
            identity_map=identity_map,
            experiment_digest=experiment_digest,
            batch_id=batch_id,
            expected_count=protocol_count,
        )
        memory_record = exemplar_memory_record(temporary / "memory")
        runtime = _build_checkpoint_runtime(
            checkpoint_id=checkpoint_id_value,
            pack_digest=pack["pack_digest"],
            exemplar_memory=memory_record,
            exemplar_max_results=exemplar_max_results,
        )
        runtime_path = temporary / "runtime.json"
        write_json_atomic(runtime_path, runtime, mode=0o444)
        if application["status"] == "no_op_validation_failed":
            raise CapabilityImprovementError(
                "a failed no-op validation cannot be frozen as a checkpoint"
            )
        status = {
            "applied": "procedural_and_exemplar",
            "no_op": "exemplar_only",
        }[application["status"]]
        payload: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_id": checkpoint_id_value,
            "experiment_digest": experiment_digest,
            "branch": branch,
            "protocol_count": protocol_count,
            "batch_id": batch_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "validation_guidance": dict(proposal["validation_guidance"]),
            "pack_digest": pack["pack_digest"],
            "pack_manifest_sha256": sha256_file(temporary / "pack" / "manifest.json"),
            "exemplar_memory": memory_record,
            "runtime_manifest_sha256": sha256_file(runtime_path),
            "proposal_sha256": sha256_file(proposal_path),
            "decision_sha256": sha256_file(decision_path),
            "application_sha256": sha256_file(application_path),
            "status": status,
            "frozen": True,
            "created_at": normalized_timestamp(created_at),
        }
        checkpoint = with_digest(payload, "checkpoint_digest")
        validate_document(
            checkpoint,
            improvement_schema_root() / "checkpoint.schema.json",
            label="capability checkpoint",
        )
        write_json_atomic(temporary / "checkpoint.json", checkpoint, mode=0o444)
        temporary.replace(output_dir)
        return checkpoint
    except BaseException:
        _discard_incomplete_frozen_tree(temporary)
        raise


def _discard_incomplete_frozen_tree(path: Path) -> None:
    """Remove a transactional output tree even after files were frozen."""

    if not path.exists():
        return
    try:
        thaw_tree(path)
    except (CapabilityImprovementError, OSError):
        # Best-effort cleanup must not mask the scientific or validation error
        # that aborted the checkpoint.  rmtree gets a second chance below and
        # any surviving tree remains visible to the closed-world lineage gate.
        pass
    shutil.rmtree(path, ignore_errors=True)


def freeze_baseline_checkpoint(
    *,
    experiment_digest: str,
    source_pack_root: Path,
    source_memory_root: Path | None = None,
    output_dir: Path,
    created_at: str,
) -> dict[str, Any]:
    """Freeze the clean initial capability pack as the real C0 checkpoint."""

    validate_digest_value = re.fullmatch(r"[a-f0-9]{64}", experiment_digest)
    if validate_digest_value is None:
        raise CapabilityImprovementError("C0 requires a valid experiment digest")
    experiment_root = output_dir.expanduser().resolve().parents[1]
    from .governance import assert_capability_modification_open

    assert_capability_modification_open(
        experiment_root,
        experiment_digest=experiment_digest,
    )
    canonical_output = experiment_root / "checkpoints" / "C0"
    if output_dir.expanduser().resolve() != canonical_output:
        raise CapabilityImprovementError(
            f"C0 output must use the canonical path: {canonical_output}"
        )
    pack = validate_capability_pack(source_pack_root)
    _assert_pack_validation_clean(
        experiment_root=experiment_root,
        experiment_digest=experiment_digest,
        pack_root=source_pack_root,
    )
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise CapabilityImprovementError(
            f"checkpoint output already exists: {output_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    try:
        copy_capability_pack(source_pack_root, temporary / "pack", freeze=True)
        experiment_manifest = load_and_validate(
            experiment_root / "design" / "experiment_manifest.json",
            schema_filename="experiment_manifest.schema.json",
            digest_field="experiment_digest",
            label="capability experiment manifest",
        )
        split = _load_active_frozen_split(experiment_root, experiment_manifest)
        identity_map = ensure_exemplar_identity_map(
            experiment_root=experiment_root,
            split_digest=split["split_digest"],
        )
        if source_memory_root is None:
            create_empty_exemplar_memory(
                memory_root=temporary / "memory",
                identity_map=identity_map,
            )
        else:
            source_memory = source_memory_root.expanduser().resolve()
            validate_exemplar_memory(
                source_memory,
                expected_count=0,
                identity_map=identity_map,
            )
            shutil.copytree(source_memory, temporary / "memory")
            freeze_tree(temporary / "memory")
        memory_record = exemplar_memory_record(temporary / "memory")
        runtime = _build_checkpoint_runtime(
            checkpoint_id="C0",
            pack_digest=pack["pack_digest"],
            exemplar_memory=memory_record,
        )
        runtime_path = temporary / "runtime.json"
        write_json_atomic(runtime_path, runtime, mode=0o444)
        payload: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_id": "C0",
            "experiment_digest": experiment_digest,
            "branch": ACTIVE_BRANCH,
            "protocol_count": 0,
            "batch_id": None,
            "parent_checkpoint_id": None,
            "validation_guidance": None,
            "pack_digest": pack["pack_digest"],
            "pack_manifest_sha256": sha256_file(temporary / "pack" / "manifest.json"),
            "exemplar_memory": memory_record,
            "runtime_manifest_sha256": sha256_file(runtime_path),
            "proposal_sha256": None,
            "decision_sha256": None,
            "application_sha256": None,
            "status": "baseline",
            "frozen": True,
            "created_at": normalized_timestamp(created_at),
        }
        checkpoint = with_digest(payload, "checkpoint_digest")
        validate_document(
            checkpoint,
            improvement_schema_root() / "checkpoint.schema.json",
            label="baseline capability checkpoint",
        )
        write_json_atomic(temporary / "checkpoint.json", checkpoint, mode=0o444)
        temporary.replace(output_dir)
        return checkpoint
    except BaseException:
        _discard_incomplete_frozen_tree(temporary)
        raise


def validate_checkpoint_runtime(
    checkpoint_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load and verify a frozen checkpoint through its model-neutral contract."""

    unresolved_root = checkpoint_root.expanduser()
    if unresolved_root.is_symlink():
        raise CapabilityImprovementError(
            f"checkpoint root may not be a symlink: {unresolved_root}"
        )
    root = unresolved_root.resolve()
    if not root.is_dir():
        raise CapabilityImprovementError(f"checkpoint root is missing: {root}")

    checkpoint_path = root / "checkpoint.json"
    runtime_path = root / "runtime.json"
    checkpoint = load_and_validate(
        checkpoint_path,
        schema_filename="checkpoint.schema.json",
        digest_field="checkpoint_digest",
        label="capability checkpoint",
    )
    checkpoint_label = checkpoint_id(checkpoint["protocol_count"])
    if checkpoint["checkpoint_id"] != checkpoint_label:
        raise CapabilityImprovementError(
            "checkpoint ID does not match its cumulative protocol count"
        )
    require_active_branch(checkpoint["branch"])
    if checkpoint["protocol_count"] == 0:
        if (
            checkpoint["batch_id"] is not None
            or checkpoint["parent_checkpoint_id"] is not None
            or checkpoint["validation_guidance"] is not None
            or checkpoint["status"] != "baseline"
            or any(
                checkpoint[field] is not None
                for field in (
                    "proposal_sha256",
                    "decision_sha256",
                    "application_sha256",
                )
            )
        ):
            raise CapabilityImprovementError("C0 has invalid baseline lineage")
    elif (
        checkpoint["batch_id"] != batch_for_protocol_count(checkpoint["protocol_count"])
        or checkpoint["parent_checkpoint_id"]
        != parent_checkpoint(checkpoint["protocol_count"])
        or checkpoint["status"] == "baseline"
        or checkpoint["validation_guidance"] is None
        or checkpoint["validation_guidance"]["checkpoint_label"]
        != checkpoint["parent_checkpoint_id"]
        or any(
            checkpoint[field] is None
            for field in (
                "proposal_sha256",
                "decision_sha256",
                "application_sha256",
            )
        )
    ):
        raise CapabilityImprovementError(
            "learned checkpoint has invalid cumulative lineage"
        )
    runtime = load_and_validate(
        runtime_path,
        schema_filename="checkpoint_runtime.schema.json",
        digest_field="runtime_digest",
        label="checkpoint runtime contract",
    )
    if sha256_file(runtime_path) != checkpoint["runtime_manifest_sha256"]:
        raise CapabilityImprovementError(
            f"checkpoint runtime contract has a stale hash: {runtime_path}"
        )
    if runtime["checkpoint_id"] != checkpoint["checkpoint_id"]:
        raise CapabilityImprovementError(
            "checkpoint runtime contract belongs to another checkpoint"
        )
    if runtime["pack_digest"] != checkpoint["pack_digest"]:
        raise CapabilityImprovementError(
            "checkpoint runtime contract references another capability pack"
        )
    if runtime["exemplar_memory"] != checkpoint["exemplar_memory"]:
        raise CapabilityImprovementError(
            "checkpoint runtime contract references another exemplar memory"
        )

    memory_record = checkpoint["exemplar_memory"]
    if memory_record["exemplar_count"] != checkpoint["protocol_count"]:
        raise CapabilityImprovementError(
            "checkpoint exemplar count differs from cumulative protocol count"
        )
    memory_root = root / safe_relative_path(memory_record["path"])
    identity_map = _canonical_identity_map_if_available(root, checkpoint)
    memory_manifest = validate_exemplar_memory(
        memory_root,
        expected_count=checkpoint["protocol_count"],
        identity_map=identity_map,
    )
    expected_memory_record = exemplar_memory_record(memory_root)
    if memory_record != expected_memory_record:
        raise CapabilityImprovementError("checkpoint exemplar-memory binding is stale")
    if memory_manifest["memory_digest"] != memory_record["memory_digest"]:
        raise CapabilityImprovementError("checkpoint exemplar-memory digest is stale")

    content = runtime["content"]
    pack_root = root / safe_relative_path(content["pack_path"])
    pack = validate_capability_pack(pack_root)
    if pack["pack_kind"] != "model_neutral":
        raise CapabilityImprovementError(
            "checkpoint capability pack is not model-neutral"
        )
    if pack["pack_digest"] != checkpoint["pack_digest"]:
        raise CapabilityImprovementError(
            f"checkpoint pack has a stale digest: {pack_root}"
        )

    manifest_path = root / safe_relative_path(content["manifest_path"])
    if manifest_path != pack_root / "manifest.json":
        raise CapabilityImprovementError(
            "checkpoint runtime manifest path does not resolve inside its pack"
        )
    if sha256_file(manifest_path) != checkpoint["pack_manifest_sha256"]:
        raise CapabilityImprovementError(
            f"checkpoint capability manifest has a stale hash: {manifest_path}"
        )

    for relative in content["required_read_order"]:
        required = root / safe_relative_path(relative)
        if required.is_symlink() or not required.is_file():
            raise CapabilityImprovementError(
                f"checkpoint required runtime resource is missing: {relative}"
            )
    for relative in content["resource_roots"].values():
        resource = root / safe_relative_path(relative)
        if resource.is_symlink() or not resource.is_dir():
            raise CapabilityImprovementError(
                f"checkpoint runtime resource root is missing: {relative}"
            )
    interfaces = runtime["interfaces"]
    for key in (
        "work_record_schema",
        "audit_report_schema",
        "control_index",
        "synthetic_suite",
        "exemplar_query_schema",
        "exemplar_retrieval_schema",
        "exemplar_usage_schema",
        "target_evidence_guard_report_schema",
    ):
        relative = interfaces[key]
        resource = root / safe_relative_path(relative)
        if resource.is_symlink() or not resource.is_file():
            raise CapabilityImprovementError(
                f"checkpoint runtime interface is missing: {relative}"
            )
    for key in ("compiler", "audit", "exemplar_query", "target_evidence_guard"):
        relative = interfaces[key]["path"]
        resource = root / safe_relative_path(relative)
        if resource.is_symlink() or not resource.is_file():
            raise CapabilityImprovementError(
                f"checkpoint runtime interface is missing: {relative}"
            )
    exemplar_max_results = checkpoint_exemplar_max_results(runtime)
    expected_runtime = _build_checkpoint_runtime(
        checkpoint_id=checkpoint["checkpoint_id"],
        pack_digest=checkpoint["pack_digest"],
        exemplar_memory=memory_record,
        exemplar_max_results=exemplar_max_results,
    )
    if runtime != expected_runtime:
        raise CapabilityImprovementError(
            "checkpoint runtime contract differs from the canonical interface"
        )
    return checkpoint, runtime, pack


def _build_checkpoint_runtime(
    *,
    checkpoint_id: str,
    pack_digest: str,
    exemplar_memory: Mapping[str, Any],
    exemplar_max_results: int = 3,
) -> dict[str, Any]:
    exemplar_max_results = _validate_exemplar_max_results(exemplar_max_results)
    payload: dict[str, Any] = {
        "schema_version": CHECKPOINT_RUNTIME_SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "pack_digest": pack_digest,
        "exemplar_memory": dict(exemplar_memory),
        "format": "model_neutral_filesystem_pack",
        "content": {
            "pack_path": "pack",
            "manifest_path": "pack/manifest.json",
            "entrypoint": "pack/PLAYBOOK.md",
            "required_read_order": list(CHECKPOINT_REQUIRED_READ_ORDER),
            "resource_roots": dict(CHECKPOINT_RESOURCE_ROOTS),
        },
        "interfaces": {
            "work_record_schema": "pack/schemas/work_record.schema.json",
            "audit_report_schema": "pack/schemas/audit_report.schema.json",
            "control_index": "pack/tools/control_index.json",
            "compiler": {
                "path": "pack/tools/compile_work_record.py",
                "argv_template": [
                    "python3",
                    "pack/tools/compile_work_record.py",
                    "--work-record",
                    "{work_record}",
                    "--t2-out",
                    "{t2_output}",
                    "--t3-out",
                    "{t3_output}",
                ],
            },
            "audit": {
                "path": "pack/tools/audit_predictions.py",
                "argv_template": [
                    "python3",
                    "pack/tools/audit_predictions.py",
                    "--work-record",
                    "{work_record}",
                    "--t2",
                    "{t2_output}",
                    "--t3",
                    "{t3_output}",
                ],
            },
            "synthetic_suite": "pack/synthetic_tests/suite.json",
            "exemplar_query_schema": (
                "memory/runtime/schemas/exemplar_query.schema.json"
            ),
            "exemplar_retrieval_schema": (
                "memory/runtime/schemas/exemplar_retrieval.schema.json"
            ),
            "exemplar_usage_schema": (
                "memory/runtime/schemas/exemplar_usage.schema.json"
            ),
            "target_evidence_guard_report_schema": (
                "memory/runtime/schemas/target_evidence_guard_report.schema.json"
            ),
            "exemplar_query": {
                "path": "memory/runtime/tools/query_exemplars.py",
                "argv_template": [
                    "python3",
                    "memory/runtime/tools/query_exemplars.py",
                    "--query",
                    "{exemplar_query}",
                    "--work-record",
                    "{work_record}",
                    "--catalog",
                    "{exemplar_catalog}",
                    "--retrieval-out",
                    "{exemplar_retrieval}",
                    "--usage-out",
                    "{exemplar_usage}",
                    "--max-results",
                    str(exemplar_max_results),
                ],
            },
            "target_evidence_guard": {
                "path": "memory/runtime/tools/guard_target_evidence.py",
                "argv_template": [
                    "python3",
                    "memory/runtime/tools/guard_target_evidence.py",
                    "--work-record",
                    "{work_record}",
                    "--catalog",
                    "{exemplar_catalog}",
                    "--retrieval",
                    "{exemplar_retrieval}",
                    "--usage",
                    "{exemplar_usage}",
                    "--report-out",
                    "{target_evidence_guard_report}",
                ],
            },
            "output_contract": {
                "format": "json",
                "success_exit": 0,
                "findings_exit": 1,
                "input_error_exit": 2,
            },
        },
        "consumer_contract": {
            "agent_compatibility": "any",
            "framework_compatibility": "any",
            "delivery": "read_only_mount_or_copy",
            "adapter_policy": "optional_framework_specific",
            "target_evidence_authority": (
                "target_sources_authoritative_pack_is_not_target_evidence"
            ),
            "exemplar_access": EXEMPLAR_ACCESS_BY_MAX_RESULTS[
                exemplar_max_results
            ],
            "target_evidence_guard": "required_before_finalization",
        },
    }
    runtime = with_digest(payload, "runtime_digest")
    validate_document(
        runtime,
        improvement_schema_root() / "checkpoint_runtime.schema.json",
        label="checkpoint runtime contract",
    )
    return runtime


def checkpoint_exemplar_max_results(runtime: Mapping[str, Any]) -> int:
    """Return the exact donor cap declared by a frozen runtime contract."""

    try:
        argv = runtime["interfaces"]["exemplar_query"]["argv_template"]
    except (KeyError, TypeError) as error:
        raise CapabilityImprovementError(
            "checkpoint runtime lacks its exemplar query command"
        ) from error
    if not isinstance(argv, list):
        raise CapabilityImprovementError(
            "checkpoint exemplar query command must be an argument list"
        )
    positions = [index for index, value in enumerate(argv) if value == "--max-results"]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise CapabilityImprovementError(
            "checkpoint exemplar query command requires one retrieval limit"
        )
    raw_limit = argv[positions[0] + 1]
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as error:
        raise CapabilityImprovementError(
            "checkpoint exemplar retrieval limit must be an integer"
        ) from error
    if str(limit) != raw_limit:
        raise CapabilityImprovementError(
            "checkpoint exemplar retrieval limit is not canonical"
        )
    limit = _validate_exemplar_max_results(limit)
    expected_access = EXEMPLAR_ACCESS_BY_MAX_RESULTS[limit]
    if runtime.get("consumer_contract", {}).get("exemplar_access") != expected_access:
        raise CapabilityImprovementError(
            "checkpoint exemplar access contract differs from its retrieval limit"
        )
    return limit


def _validate_exemplar_max_results(value: int) -> int:
    if isinstance(value, bool) or value not in EXEMPLAR_ACCESS_BY_MAX_RESULTS:
        raise CapabilityImprovementError(
            "checkpoint exemplar retrieval limit must be 1, 2, or 3"
        )
    return value


def _assert_pack_validation_clean(
    *,
    experiment_root: Path,
    experiment_digest: str,
    pack_root: Path,
) -> None:
    """Enforce the aggregate-only validation boundary at every freeze point."""

    manifest = load_and_validate(
        experiment_root / "design" / "experiment_manifest.json",
        schema_filename="experiment_manifest.schema.json",
        digest_field="experiment_digest",
        label="capability experiment manifest",
    )
    if manifest["experiment_digest"] != experiment_digest:
        raise CapabilityImprovementError(
            "checkpoint freeze belongs to another experiment"
        )
    from .validation import (
        scan_validation_pack_leakage,
        validate_referenced_validation_access_policy,
    )

    policy = validate_referenced_validation_access_policy(
        experiment_root=experiment_root,
        experiment_manifest=manifest,
    )
    issues = scan_validation_pack_leakage(pack_root, policy)
    if issues:
        raise CapabilityImprovementError(
            "checkpoint pack crosses the validation learning boundary: "
            + "; ".join(issues[:8])
        )


def _load_active_frozen_split(
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    reference = experiment_manifest["frozen_split"]
    path = experiment_root / safe_relative_path(reference["path"])
    split = load_and_validate(
        path,
        schema_filename="frozen_split.schema.json",
        digest_field="split_manifest_digest",
        label="frozen experiment split",
    )
    if reference["digest"] != split["split_manifest_digest"] or reference[
        "sha256"
    ] != sha256_file(path):
        raise CapabilityImprovementError(
            "experiment manifest references a stale frozen split"
        )
    return split


def _canonical_identity_map_if_available(
    checkpoint_root: Path,
    checkpoint: Mapping[str, Any],
) -> dict[str, Any] | None:
    if checkpoint_root.parent.name != "checkpoints":
        return None
    experiment_root = checkpoint_root.parents[1]
    identity_map_path = experiment_root / "private" / "exemplar_identity_map.json"
    manifest_path = experiment_root / "design" / "experiment_manifest.json"
    if not identity_map_path.is_file() or not manifest_path.is_file():
        return None
    manifest = load_and_validate(
        manifest_path,
        schema_filename="experiment_manifest.schema.json",
        digest_field="experiment_digest",
        label="capability experiment manifest",
    )
    if manifest["experiment_digest"] != checkpoint["experiment_digest"]:
        raise CapabilityImprovementError(
            "checkpoint belongs to another canonical experiment"
        )
    split = _load_active_frozen_split(experiment_root, manifest)
    return validate_exemplar_identity_map(
        identity_map_path,
        split_digest=split["split_digest"],
    )


def _validate_decision_identity(
    proposal_path: Path,
    proposal: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> None:
    if decision["proposal_sha256"] != sha256_file(proposal_path):
        raise CapabilityImprovementError("decision references a stale proposal file")
    for key in ("proposal_digest", "branch", "batch_id", "revision_round"):
        if decision[key] != proposal[key]:
            raise CapabilityImprovementError(f"decision {key} differs from proposal")
    require_active_branch(proposal["branch"])
    reviewer_kind = decision["reviewer_kind"]
    reviewer = decision["reviewer"]
    if reviewer_kind == "independent_codex" and (
        reviewer.get("model") != proposal["proposer"]["model"]
        or reviewer.get("version") != proposal["proposer"]["version"]
        or not reviewer.get("transcript_sha256")
        or reviewer.get("transcript_sha256")
        == proposal["proposer"]["transcript_sha256"]
    ):
        raise CapabilityImprovementError(
            "cumulative-branch review lacks independent Codex provenance"
        )
    if reviewer_kind == "human" and any(
        reviewer.get(key) is not None
        for key in ("model", "version", "transcript_sha256")
    ):
        raise CapabilityImprovementError(
            "human review must not claim agent model or transcript provenance"
        )


def _validate_revision_lineage(
    proposal: Mapping[str, Any],
    *,
    prior_proposal_path: Path | None,
    revision_decision_path: Path | None,
) -> None:
    revision_round = proposal["revision_round"]
    prior_digest = proposal["revision_of_proposal_digest"]
    decision_digest = proposal["revision_request_decision_digest"]
    if revision_round == 0:
        if prior_digest is not None or decision_digest is not None:
            raise CapabilityImprovementError(
                "an initial proposal cannot claim revision lineage"
            )
        if prior_proposal_path is not None or revision_decision_path is not None:
            raise CapabilityImprovementError(
                "revision inputs are invalid for an initial proposal"
            )
        return
    if prior_digest is None or decision_digest is None:
        raise CapabilityImprovementError(
            "a revised proposal requires prior proposal and revision-decision digests"
        )
    if prior_proposal_path is None or revision_decision_path is None:
        raise CapabilityImprovementError(
            "a revised proposal requires pinned prior proposal and decision files"
        )
    prior = load_and_validate(
        prior_proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="prior capability proposal",
    )
    decision = load_and_validate(
        revision_decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="capability revision decision",
    )
    if prior["revision_round"] != 0 or prior["proposal_digest"] != prior_digest:
        raise CapabilityImprovementError(
            "revised proposal has stale prior-proposal lineage"
        )
    if decision["decision_digest"] != decision_digest:
        raise CapabilityImprovementError("revised proposal has stale decision lineage")
    _validate_decision_identity(prior_proposal_path, prior, decision)
    if decision["review_state"] != "revision_requested":
        raise CapabilityImprovementError(
            "revision lineage does not contain a revision request"
        )
    prior_changes = {item["change_id"]: item for item in prior["change_units"]}
    prior_decisions = {item["change_id"]: item for item in decision["change_decisions"]}
    if set(prior_decisions) != set(prior_changes):
        raise CapabilityImprovementError(
            "revision request does not decide every prior change"
        )
    revised_changes = {item["change_id"]: item for item in proposal["change_units"]}
    blocked_paths = {
        mutation["path"]
        for change_id, review in prior_decisions.items()
        if review["disposition"] in {"reject", "unresolved"}
        for mutation in prior_changes[change_id]["mutations"]
    }
    reintroduced_paths = {
        mutation["path"]
        for item in proposal["change_units"]
        for mutation in item["mutations"]
        if mutation["path"] in blocked_paths
    }
    if reintroduced_paths:
        raise CapabilityImprovementError(
            "revised proposal reintroduces rejected or unresolved paths: "
            + ", ".join(sorted(reintroduced_paths))
        )
    for change_id, review in prior_decisions.items():
        disposition = review["disposition"]
        revised = revised_changes.get(change_id)
        if disposition == "accept":
            if revised != prior_changes[change_id]:
                raise CapabilityImprovementError(
                    "revised proposal must carry accepted change units forward "
                    f"unchanged: {change_id}"
                )
        elif disposition in {"reject", "unresolved"}:
            if revised is not None:
                raise CapabilityImprovementError(
                    "revised proposal must omit rejected or unresolved change: "
                    f"{change_id}"
                )
        elif disposition == "modify":
            if revised is None:
                raise CapabilityImprovementError(
                    f"revised proposal omits requested modification: {change_id}"
                )
            if revised == prior_changes[change_id]:
                raise CapabilityImprovementError(
                    f"requested modification was not revised: {change_id}"
                )
    for key in (
        "experiment_digest",
        "branch",
        "batch_id",
        "checkpoint_from",
        "packet_digest",
        "parent_pack_digest",
        "learning_ledger_digest",
        "validation_guidance",
        "protocol_ids",
    ):
        if proposal[key] != prior[key]:
            raise CapabilityImprovementError(
                f"revised proposal changes immutable lineage field {key}"
            )


def _reverse_complement(value: str) -> str:
    table = str.maketrans(
        "ACGTRYSWKMBDHVN",
        "TGCAYRSWMKVHDBN",
    )
    return value.translate(table)[::-1]
