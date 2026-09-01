from __future__ import annotations

import sys
from difflib import unified_diff
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic

from .artifacts import (
    CapabilityImprovementError,
    load_and_validate,
    safe_relative_path,
)
from .mutation_lock import (
    assert_no_interrupted_split_freeze,
    assert_no_nearby_split_freeze_journal,
    experiment_mutation_lock,
    resolve_experiment_root_from_paths,
)
from .lineage import ACTIVE_BRANCH
from .workflow import (
    create_decision_template,
    finalize_capability_decision,
    record_change_decision,
    validate_capability_decision,
)
from .review_summary import (
    render_capability_review_summary,
    write_or_validate_capability_review_summary,
)


InputFunction = Callable[[str], str]


def run_human_review_console(
    *,
    proposal_path: Path,
    decision_path: Path,
    reviewer_id: str,
    parent_pack_root: Path,
    candidate_root: Path,
    started_at: str | None = None,
    completed_at: str | None = None,
    input_function: InputFunction = input,
    output: TextIO = sys.stdout,
    experiment_root: Path | None = None,
) -> dict[str, Any]:
    """Walk one human through proposal changes and checkpoint every answer."""

    inferred_root = resolve_experiment_root_from_paths(proposal_path, decision_path)
    root = (
        experiment_root.expanduser().resolve()
        if experiment_root is not None
        else inferred_root
    )
    if experiment_root is not None and inferred_root not in {None, root}:
        raise CapabilityImprovementError(
            "interactive review artifacts belong to another experiment"
        )
    if root is None:
        assert_no_nearby_split_freeze_journal(proposal_path, decision_path)
        return _run_human_review_console_unlocked(
            proposal_path=proposal_path,
            decision_path=decision_path,
            reviewer_id=reviewer_id,
            parent_pack_root=parent_pack_root,
            candidate_root=candidate_root,
            started_at=started_at,
            completed_at=completed_at,
            input_function=input_function,
            output=output,
        )
    with experiment_mutation_lock(root, operation="interactive human review"):
        assert_no_interrupted_split_freeze(root)
        return _run_human_review_console_unlocked(
            proposal_path=proposal_path,
            decision_path=decision_path,
            reviewer_id=reviewer_id,
            parent_pack_root=parent_pack_root,
            candidate_root=candidate_root,
            started_at=started_at,
            completed_at=completed_at,
            input_function=input_function,
            output=output,
        )


def _run_human_review_console_unlocked(
    *,
    proposal_path: Path,
    decision_path: Path,
    reviewer_id: str,
    parent_pack_root: Path,
    candidate_root: Path,
    started_at: str | None = None,
    completed_at: str | None = None,
    input_function: InputFunction = input,
    output: TextIO = sys.stdout,
) -> dict[str, Any]:

    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    if proposal["branch"] != ACTIVE_BRANCH:
        raise CapabilityImprovementError(
            "interactive human review accepts only a cumulative-branch proposal"
        )
    if decision_path.exists():
        _, decision = validate_capability_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            require_final=False,
        )
        if decision["reviewer"]["reviewer_id"] != reviewer_id:
            raise CapabilityImprovementError(
                "reviewer ID differs from the resumable decision"
            )
        if decision["review_state"] != "in_progress":
            _write(output, _status_text(proposal, decision))
            _write_completed_summary(
                proposal_path=proposal_path,
                decision_path=decision_path,
                output=output,
            )
            return decision
    else:
        decision = create_decision_template(
            proposal_path=proposal_path,
            reviewer_kind="human",
            reviewer_id=reviewer_id,
            started_at=started_at or _now(),
        )
        write_json_atomic(decision_path, decision)

    changes = list(proposal["change_units"])
    index = _first_undecided_index(changes, decision)
    review_existing = False
    while True:
        _, decision = validate_capability_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            require_final=False,
        )
        decided = {item["change_id"] for item in decision["change_decisions"]}
        if len(decided) == len(changes) and not review_existing:
            _write(output, _status_text(proposal, decision))
            answer = (
                input_function("Finalize this decision? [yes/no/status/back/quit]: ")
                .strip()
                .lower()
            )
            if answer in {"yes", "y"}:
                finalized = finalize_capability_decision(
                    proposal_path=proposal_path,
                    decision_path=decision_path,
                    completed_at=completed_at or _now(),
                )
                _write_completed_summary(
                    proposal_path=proposal_path,
                    decision_path=decision_path,
                    output=output,
                )
                return finalized
            if answer in {"status", "s"}:
                continue
            if answer in {"back", "b"}:
                index = max(0, len(changes) - 1)
                review_existing = True
                continue
            if answer in {"quit", "q", "no", "n"}:
                return decision
            _write(output, "Unknown command. Nothing was changed.\n")
            continue

        if not review_existing:
            index = _next_undecided_index(changes, decided, index)
        change = changes[index]
        _write(
            output,
            render_change_card(
                proposal,
                change,
                index + 1,
                parent_pack_root=parent_pack_root,
                candidate_root=candidate_root,
            ),
        )
        answer = (
            input_function(
                "Decision [accept/reject/modify/unresolved/back/skip/status/quit]: "
            )
            .strip()
            .lower()
        )
        if answer in {"back", "b"}:
            index = max(0, index - 1)
            review_existing = changes[index]["change_id"] in decided
            continue
        if answer in {"skip", "s"}:
            index = (index + 1) % len(changes)
            continue
        if answer == "status":
            _write(output, _status_text(proposal, decision))
            continue
        if answer in {"quit", "q"}:
            return decision
        disposition = _disposition(answer)
        if disposition is None:
            _write(output, "Unknown command. Nothing was changed.\n")
            continue
        rationale = _required_answer(input_function, output, "Rationale: ")
        revision_instruction = None
        if disposition == "modify":
            revision_instruction = _required_answer(
                input_function,
                output,
                "Revision instruction: ",
            )
        decision = record_change_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            change_id=change["change_id"],
            disposition=disposition,
            rationale=rationale,
            revision_instruction=revision_instruction,
        )
        review_existing = False
        _write(
            output,
            f"Checkpointed {change['change_id']} as {disposition}; "
            f"decision digest {decision['decision_digest']}.\n",
        )
        index = (index + 1) % len(changes)


def render_change_card(
    proposal: Mapping[str, Any],
    change: Mapping[str, Any],
    ordinal: int,
    *,
    parent_pack_root: Path,
    candidate_root: Path,
) -> str:
    evidence = "\n".join(
        "  - "
        + f"protocol={item['protocol_id']} artifact={item['artifact_sha256']} "
        + f"pointer={item['json_pointer']}"
        for item in change["evidence_refs"]
    )
    tests = "\n".join(
        f"  - {item['polarity']} {item['case_id']}: {item['path']}"
        for item in change["fixtures"]
    )
    mutations = _change_mutations(change)
    differences = []
    for mutation_index, mutation in enumerate(mutations, start=1):
        differences.append(
            f"Mutation {mutation_index}/{len(mutations)}: "
            f"{mutation['operation']} {mutation['path']}\n"
            + _change_diff(
                mutation,
                parent_pack_root=parent_pack_root,
                candidate_root=candidate_root,
            )
        )
    difference = "\n".join(differences)
    operation_text = ", ".join(
        f"{item['operation']} {item['path']}" for item in mutations
    )
    control = (
        f"Capability class: {change['capability_class']}\n"
        f"Finding codes: {', '.join(change['finding_codes'])}\n"
        f"Residual judgment: {change['residual_judgment'] or '(none)'}\n"
    )
    return (
        "\n"
        + f"Change {ordinal}/{len(proposal['change_units'])}: {change['change_id']}\n"
        + f"Branch/batch: {proposal['branch']} / {proposal['batch_id']}\n"
        + f"Mutations: {operation_text}\n"
        + control
        + f"Generalized failure pattern:\n  {change['generalized_failure_pattern']}\n"
        + f"Expected invariant:\n  {change['expected_invariant']}\n"
        + f"Exact proposed changes:\n{difference}\n"
        + f"Evidence:\n{evidence}\n"
        + f"Synthetic fixtures:\n{tests or '  (none)'}\n"
        + f"Leakage attestation: {change['leakage_attestation']}\n"
    )


def _first_undecided_index(
    changes: list[Mapping[str, Any]], decision: Mapping[str, Any]
) -> int:
    decided = {item["change_id"] for item in decision["change_decisions"]}
    return _next_undecided_index(changes, decided, 0)


def _next_undecided_index(
    changes: list[Mapping[str, Any]], decided: set[str], start: int
) -> int:
    for offset in range(len(changes)):
        index = (start + offset) % len(changes)
        if changes[index]["change_id"] not in decided:
            return index
    return 0


def _disposition(answer: str) -> str | None:
    values = {
        "accept": "accept",
        "a": "accept",
        "reject": "reject",
        "r": "reject",
        "modify": "modify",
        "m": "modify",
        "unresolved": "unresolved",
        "u": "unresolved",
    }
    return values.get(answer)


def _required_answer(
    input_function: InputFunction,
    output: TextIO,
    prompt: str,
) -> str:
    while True:
        value = input_function(prompt).strip()
        if value:
            return value
        _write(output, "A non-empty value is required.\n")


def _status_text(proposal: Mapping[str, Any], decision: Mapping[str, Any]) -> str:
    values = {
        item["change_id"]: item["disposition"] for item in decision["change_decisions"]
    }
    lines = [
        "\nReview status",
        f"State: {decision['review_state']}",
        f"Decided: {len(values)}/{len(proposal['change_units'])}",
    ]
    lines.extend(
        f"  {change['change_id']}: {values.get(change['change_id'], 'undecided')}"
        for change in proposal["change_units"]
    )
    return "\n".join(lines) + "\n"


def _write(output: TextIO, value: str) -> None:
    output.write(value)
    output.flush()


def _write_completed_summary(
    *,
    proposal_path: Path,
    decision_path: Path,
    output: TextIO,
) -> None:
    summary_path, summary = write_or_validate_capability_review_summary(
        proposal_path=proposal_path,
        decision_path=decision_path,
    )
    _write(
        output,
        "\n"
        + render_capability_review_summary(
            summary,
            summary_path=summary_path,
        ),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _change_diff(
    mutation: Mapping[str, Any],
    *,
    parent_pack_root: Path,
    candidate_root: Path,
) -> str:
    relative = safe_relative_path(mutation["path"])
    current_path = parent_pack_root.expanduser().resolve() / relative
    candidate_path = candidate_root.expanduser().resolve() / relative
    operation = mutation["operation"]
    if operation in {"replace", "remove"}:
        if (
            not current_path.is_file()
            or sha256_file(current_path) != mutation["baseline_sha256"]
        ):
            raise CapabilityImprovementError(
                f"human review current file is stale: {relative.as_posix()}"
            )
        current = current_path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        current = []
    if operation in {"add", "replace"}:
        if (
            not candidate_path.is_file()
            or sha256_file(candidate_path) != mutation["candidate_sha256"]
        ):
            raise CapabilityImprovementError(
                f"human review candidate file is stale: {relative.as_posix()}"
            )
        candidate = candidate_path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        candidate = []
    lines = list(
        unified_diff(
            current,
            candidate,
            fromfile=f"current/{relative.as_posix()}",
            tofile=f"candidate/{relative.as_posix()}",
        )
    )
    return "".join(lines) or "  (no textual difference)\n"


def _change_mutations(change: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(change["mutations"])
