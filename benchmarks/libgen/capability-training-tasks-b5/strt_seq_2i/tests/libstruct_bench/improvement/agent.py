from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)

from .artifacts import (
    CapabilityImprovementError,
    improvement_schema_root,
    load_and_validate,
    validate_capability_pack,
    with_digest,
)
from .lineage import ACTIVE_BRANCH, checkpoint_before_batch, require_active_branch
from .workflow import (
    create_decision_template,
    finalize_capability_decision,
    record_change_decision,
    validate_capability_proposal,
)


def compile_capability_proposal_draft(
    *,
    experiment_manifest: Mapping[str, Any],
    access_policy_path: Path,
    packet_path: Path,
    parent_pack_root: Path,
    candidate_root: Path,
    draft_path: Path,
    transcript_path: Path,
    output_path: Path,
    prior_proposal_path: Path | None = None,
    revision_decision_path: Path | None = None,
) -> dict[str, Any]:
    """Attach trusted lineage and hashes to an untrusted agent-authored draft."""

    draft = _load_draft(
        draft_path,
        schema_filename="capability_proposal_draft.schema.json",
        label="capability proposal draft",
    )
    packet = load_and_validate(
        packet_path,
        schema_filename="batch_packet.schema.json",
        digest_field="packet_digest",
        label="capability batch packet",
    )
    parent = validate_capability_pack(parent_pack_root)
    transcript = transcript_path.expanduser().resolve()
    if transcript.is_symlink() or not transcript.is_file():
        raise CapabilityImprovementError("proposal transcript is missing")
    branch = packet["branch"]
    checkpoint_from = _checkpoint_from(packet["batch_id"], branch)
    if prior_proposal_path is None and revision_decision_path is None:
        revision_round = 0
        prior_digest = None
        revision_decision_digest = None
        from .validation import (
            build_validation_guidance_record,
            validate_referenced_validation_access_policy,
        )

        experiment_root = access_policy_path.expanduser().resolve().parents[1]
        validation_policy = validate_referenced_validation_access_policy(
            experiment_root=experiment_root,
            experiment_manifest=experiment_manifest,
        )
        validation_guidance = build_validation_guidance_record(
            experiment_root=experiment_root,
            experiment_digest=experiment_manifest["experiment_digest"],
            validation_access_policy=validation_policy,
            batch_id=packet["batch_id"],
            expected_pack_digest=parent["pack_digest"],
            workspace_manifest_path=(
                draft_path.expanduser().resolve().parents[1] / "workspace_manifest.json"
            ),
        )
    elif prior_proposal_path is not None and revision_decision_path is not None:
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
        revision_round = 1
        prior_digest = prior["proposal_digest"]
        revision_decision_digest = decision["decision_digest"]
        validation_guidance = copy.deepcopy(prior["validation_guidance"])
    else:
        raise CapabilityImprovementError(
            "proposal revision requires both prior proposal and revision decision"
        )
    transcript_sha = sha256_file(transcript)
    anchor = experiment_manifest["anchor"]
    ledger = packet.get("learning_ledger")
    if not isinstance(ledger, Mapping):
        raise CapabilityImprovementError(
            "proposal compilation requires the trusted learning ledger"
        )
    clusters = {item["cluster_id"]: item for item in ledger.get("clusters", [])}
    compiled_units: list[dict[str, Any]] = []
    for source_unit in draft["change_units"]:
        unit = copy.deepcopy(source_unit)
        missing = set(unit["cluster_ids"]) - set(clusters)
        if missing:
            raise CapabilityImprovementError(
                "proposal cites unknown learning clusters: "
                + ", ".join(sorted(missing))
            )
        evidence = {
            (
                item["protocol_id"],
                item["artifact_sha256"],
                item["json_pointer"],
            ): copy.deepcopy(item)
            for cluster_id in unit["cluster_ids"]
            for item in clusters[cluster_id]["evidence_refs"]
        }
        unit["evidence_refs"] = [evidence[key] for key in sorted(evidence)]
        compiled_units.append(unit)
    payload: dict[str, Any] = {
        "schema_version": "libstruct.libgen_capability_proposal.v1",
        "proposal_id": (
            f"{packet['batch_id']}:{branch}:r{revision_round}:{transcript_sha[:12]}"
        ),
        "experiment_digest": experiment_manifest["experiment_digest"],
        "branch": branch,
        "batch_id": packet["batch_id"],
        "checkpoint_from": checkpoint_from,
        "packet_digest": packet["packet_digest"],
        "parent_pack_digest": parent["pack_digest"],
        "learning_ledger_digest": ledger["ledger_digest"],
        "validation_guidance": validation_guidance,
        "protocol_ids": list(packet["protocol_ids"]),
        "proposer": {
            "agent": anchor["agent"],
            "harness": anchor["harness"],
            "model": anchor["model"],
            "version": anchor["agent_version"],
            "reasoning_effort": anchor["reasoning_effort"],
            "transcript_sha256": transcript_sha,
        },
        "revision_round": revision_round,
        "revision_of_proposal_digest": prior_digest,
        "revision_request_decision_digest": revision_decision_digest,
        "change_units": compiled_units,
    }
    proposal = with_digest(payload, "proposal_digest")
    write_json_atomic(output_path, proposal, mode=0o444)
    try:
        return validate_capability_proposal(
            experiment_manifest=experiment_manifest,
            access_policy_path=access_policy_path,
            proposal_path=output_path,
            candidate_root=candidate_root,
            parent_pack_root=parent_pack_root,
            packet_path=packet_path,
            prior_proposal_path=prior_proposal_path,
            revision_decision_path=revision_decision_path,
        )
    except BaseException:
        output_path.chmod(0o600)
        output_path.unlink(missing_ok=True)
        raise


def compile_independent_decision_draft(
    *,
    proposal_path: Path,
    draft_path: Path,
    transcript_path: Path,
    output_path: Path,
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    """Compile an independent critic draft into the existing decision format."""

    draft = _load_draft(
        draft_path,
        schema_filename="capability_decision_draft.schema.json",
        label="capability decision draft",
    )
    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    if proposal["branch"] != ACTIVE_BRANCH:
        raise CapabilityImprovementError(
            "only a cumulative-branch proposal accepts an agent-authored review"
        )
    transcript = transcript_path.expanduser().resolve()
    if transcript.is_symlink() or not transcript.is_file():
        raise CapabilityImprovementError("independent-review transcript is missing")
    transcript_sha = sha256_file(transcript)
    if transcript_sha == proposal["proposer"]["transcript_sha256"]:
        raise CapabilityImprovementError(
            "independent critic transcript must differ from proposer transcript"
        )
    decision = create_decision_template(
        proposal_path=proposal_path,
        reviewer_kind="independent_codex",
        reviewer_id="native-codex-independent-critic",
        started_at=started_at,
        reviewer_model=proposal["proposer"]["model"],
        reviewer_version=proposal["proposer"]["version"],
        transcript_sha256=transcript_sha,
    )
    write_json_atomic(output_path, decision)
    try:
        for item in draft["change_decisions"]:
            record_change_decision(
                proposal_path=proposal_path,
                decision_path=output_path,
                change_id=item["change_id"],
                disposition=item["disposition"],
                rationale=item["rationale"],
                revision_instruction=item.get("revision_instruction"),
            )
        return finalize_capability_decision(
            proposal_path=proposal_path,
            decision_path=output_path,
            completed_at=completed_at,
        )
    except BaseException:
        output_path.chmod(0o600)
        output_path.unlink(missing_ok=True)
        raise


def _load_draft(path: Path, *, schema_filename: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise CapabilityImprovementError(f"{label} must be a JSON object")
    validate_document(value, improvement_schema_root() / schema_filename, label=label)
    return value


def _checkpoint_from(batch_id: str, branch: str) -> str:
    require_active_branch(branch)
    return checkpoint_before_batch(batch_id)


def proposal_instruction() -> str:
    return """Read only the mounted current capability pack and current batch packet.
The packet contains a trusted deterministic learning_ledger built from the
revealed verifier error analyses. It excludes infrastructure, evaluator,
policy, source-scope, and ground-truth defects and collapses metric-level
mismatches into stable root-error clusters. Do not independently restore the
excluded observations, regroup raw scores, or invent evidence. Every capability
unit must cite one or more cluster_ids and use only finding_codes present in
those clusters. Metric effects are context, never root-error identity. For a
prospective batch, paired effects compare the current cumulative checkpoint
with C0 on the same five protocols. No alternate branch exists and none may be
inferred.

When `inputs/validation_feedback.json` is staged, it is a sanitized aggregate
over the fixed five-protocol validation panel. Use only its macro means and
counts to prioritize general controls. It is not target evidence: never cite it
as an evidence reference, infer a protocol-specific answer from it, or copy its
scores or contents into a proposal, candidate, memory record, or synthetic
fixture.

Propose at most two protocol-neutral procedural_or_tool capability units for
the five-protocol batch. Do not author or mutate the checkpoint's exemplar
memory; the orchestrator projects it separately from approved training GT. Do
not create target-answer templates. A unit
may contain multiple mutations when its instruction, enforcement, and fixtures
must be accepted or rejected together. Do not split a checker from the fixtures
or instructions required to use it. Classify each unit as deterministic,
hybrid, or instruction_only. Deterministic and hybrid units require at least one
enforcement path plus positive, negative, and boundary fixtures with stable
case IDs. State the residual model judgment for hybrid units. Instruction-only
units require a concrete rationale explaining why deterministic enforcement is
not sound, and residual_judgment must name the exact target-specific choices
that the model still has to make; do not leave it null. Every unit must define
applicability, exclusions, a falsifying counterexample, finding codes, and the
expected invariant.

Declare admission_basis=recurring_root_error only when a cited cluster is
marked recurring_across_protocols. Otherwise a potentially acceptable unit
must use admission_basis=general_invariant_with_synthetic_regression, cite at
least one negative synthetic regression case, and mutate that cited fixture
under synthetic_tests/. Use admission_basis=insufficient when neither gate is
met; such a unit may be reviewed but cannot be accepted. For
general_invariant_with_synthetic_regression, list the exact admission fixture
case IDs in synthetic_regression_case_ids. For recurring_root_error, leave
synthetic_regression_case_ids empty even when the unit also adds and runs
synthetic fixtures. At most one unit can ultimately be accepted from the batch.

Write candidate full-file replacements only under the designated candidate
directory. Cite trusted cluster IDs for every unit; evidence references are
expanded deterministically after the run. Do not copy
protocol names, IDs, real oligo sequences, benchmark answers, scores, or source
filenames into the candidate pack. Do not use the web or remembered kit facts.
Run every cited fixture and all changed enforcement checks before emitting the
draft. Candidate paths and hashes must describe the complete atomic mutation
set exactly.
The proposal is not approval and must not edit the parent pack. Emit only the
proposal-draft schema; trusted lineage, transcript hashes, and the proposal
digest are added deterministically after the run.
"""


def independent_review_instruction() -> str:
    return """Independently review every proposed capability change against the
trusted learning-ledger clusters, current batch packet, parent pack, complete
atomic mutation set, candidate bytes, scope restrictions, enforcement, and all
three fixture polarities. Verify atomicity for every decision: never accept only
part of a unit. Check that finding codes occur in cited clusters, applicability
and exclusions bound the rule, the counterexample can falsify an over-broad
rule, and residual judgment matches the deterministic/hybrid/instruction-only
classification. Emit the capability-decision draft schema with
atomicity_verified=true for every reviewed unit. Accept only generalizable and
evidence-linked procedural/tool changes. Accept at most one unit for the entire
five-protocol batch. An accepted unit must either cite a recurring root-error
cluster or encode a general invariant with a mutated negative synthetic
regression. Reject admission_basis=insufficient, attempts to author or mutate
the deterministic exemplar projection, leakage, and target-specific
corrections. You
may request one bounded revision with disposition=modify; no second revision is
permitted. You do not apply your own decision. Reviewer provenance and decision
hashes are added deterministically afterward.
"""
