#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from exemplar_memory import (
    ExemplarMemoryError,
    TargetWorkRecordError,
    canonical_digest,
    file_sha256,
    load_catalog,
    load_json_object,
    resolve_json_pointer,
    validate_retrieval,
    validate_target_work_record,
    validate_usage,
    with_digest,
    write_json_atomic,
)


REPORT_SCHEMA_VERSION = "libstruct.libgen_target_evidence_guard_report.v1"
FINDING_CODES = {
    "sequence": "memory_sequence_without_target_evidence",
    "operation": "memory_operation_without_target_evidence",
    "state": "memory_state_without_target_evidence",
    "modification": "memory_modification_without_target_evidence",
    "branch": "memory_branch_without_target_evidence",
}


class TargetEvidenceGuardError(ValueError):
    """Raised when the target-evidence audit cannot fail closed."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare final target predictions with the entire frozen exemplar "
            "catalog and require exact target-source support or a recorded "
            "mechanical consequence for every exact overlap."
        )
    )
    parser.add_argument("--work-record", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--retrieval", required=True)
    parser.add_argument("--usage", required=True)
    parser.add_argument("--report-out", required=True)
    return parser


def audit_target_evidence(
    work_record: Mapping[str, Any],
    memory_manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    exemplars: Sequence[Mapping[str, Any]],
    retrieval: Mapping[str, Any],
    usage: Mapping[str, Any],
    *,
    target_work_record_sha256: str,
) -> dict[str, Any]:
    """Audit the final work record against every value in frozen memory.

    Retrieval is deliberately not the comparison scope.  It is validated only
    to prove that the canonical query was rerun against the final work-record
    bytes and the same frozen memory.  This catches direct catalog browsing and
    omitted usage records, including when retrieval returned no exemplars.
    """

    try:
        validate_target_work_record(work_record)
        validate_retrieval(retrieval)
        validate_usage(usage, retrieval)
    except (ExemplarMemoryError, TargetWorkRecordError) as error:
        raise TargetEvidenceGuardError(str(error)) from error
    if retrieval["target_work_record_sha256"] != target_work_record_sha256:
        raise TargetEvidenceGuardError(
            "exemplar query must be rerun after the final work-record update"
        )
    if retrieval["memory_digest"] != memory_manifest["memory_digest"]:
        raise TargetEvidenceGuardError(
            "retrieval is bound to another exemplar-memory manifest"
        )
    if retrieval["catalog_digest"] != catalog["catalog_digest"]:
        raise TargetEvidenceGuardError(
            "retrieval is bound to another exemplar catalog"
        )
    catalog_ids = {item["exemplar_id"] for item in exemplars}
    retrieved_ids = {item["exemplar_id"] for item in retrieval["matches"]}
    if not retrieved_ids <= catalog_ids:
        raise TargetEvidenceGuardError(
            "retrieval names an exemplar absent from the frozen catalog"
        )

    memory_index = _memory_value_index(exemplars)
    target_values = _target_values(work_record["drafts"])
    claims = {
        (claim["target"], claim["json_pointer"]): claim
        for claim in work_record["claims"]
    }
    inventory = {item["inventory_id"]: item for item in work_record["inventory"]}
    findings: list[dict[str, Any]] = []
    checked: set[tuple[str, str, str, str]] = set()
    for category, target, pointer, value in target_values:
        value_digest = canonical_digest(value)
        donors = memory_index.get((category, value_digest))
        if not donors:
            continue
        overlap_key = (category, target, pointer, value_digest)
        if overlap_key in checked:
            continue
        checked.add(overlap_key)
        if _has_target_support(
            category,
            target,
            pointer,
            value,
            work_record,
            claims=claims,
            inventory=inventory,
        ):
            continue
        exemplar_id, donor_pointer = donors[0]
        findings.append(
            {
                "code": FINDING_CODES[category],
                "category": category,
                "target": target,
                "json_pointer": pointer,
                "exemplar_id": exemplar_id,
                "donor_pointer": donor_pointer,
                "memory_value_digest": value_digest,
                "message": (
                    "prediction value exactly overlaps the frozen pseudonymous "
                    "exemplar catalog but lacks exact target-source support or "
                    "a recorded mechanical consequence"
                ),
            }
        )
    findings.sort(
        key=lambda item: (
            item["target"],
            item["json_pointer"],
            item["category"],
            item["exemplar_id"],
            item["donor_pointer"],
        )
    )
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "findings" if findings else "pass",
        "memory_digest": memory_manifest["memory_digest"],
        "catalog_digest": catalog["catalog_digest"],
        "retrieval_digest": retrieval["retrieval_digest"],
        "usage_digest": usage["usage_digest"],
        "target_work_record_sha256": target_work_record_sha256,
        "finding_count": len(findings),
        "checked_overlap_count": len(checked),
        "findings": findings,
    }
    return with_digest(payload, "report_digest")


def _memory_value_index(
    exemplars: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    collected: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for exemplar in exemplars:
        exemplar_id = exemplar["exemplar_id"]
        for target in ("t2", "t3"):
            document = exemplar[target]
            base = f"/exemplars/{exemplar_id}/{target}_example.json/example"
            for category, _, pointer, value in _document_values(
                document,
                target=target,
                base=base,
            ):
                collected[(category, canonical_digest(value))].add(
                    (exemplar_id, pointer)
                )
    return {key: sorted(values) for key, values in collected.items()}


def _target_values(
    drafts: Mapping[str, Any],
) -> list[tuple[str, str, str, Any]]:
    return [
        *_document_values(drafts["t2"], target="t2", base=""),
        *_document_values(drafts["t3"], target="t3", base=""),
    ]


def _document_values(
    document: Mapping[str, Any],
    *,
    target: str,
    base: str,
) -> list[tuple[str, str, str, Any]]:
    result: list[tuple[str, str, str, Any]] = []
    if target == "t2":
        oligos = document.get("oligos")
        if not isinstance(oligos, list):
            raise TargetEvidenceGuardError("T2 document lacks an oligos array")
        for index, oligo in enumerate(oligos):
            if not isinstance(oligo, Mapping):
                raise TargetEvidenceGuardError("T2 oligo must be an object")
            _collect_leaf_categories(
                oligo,
                target="t2",
                pointer=f"{base}/oligos/{index}",
                result=result,
            )
        return result

    workflows = document.get("workflows")
    if not isinstance(workflows, list):
        raise TargetEvidenceGuardError("T3 document lacks a workflows array")
    if len(workflows) > 1:
        result.append(
            (
                "branch",
                "t3",
                f"{base}/workflows",
                {"kind": "multiple_workflows", "workflow_count": len(workflows)},
            )
        )
    for workflow_index, workflow in enumerate(workflows):
        if not isinstance(workflow, Mapping):
            raise TargetEvidenceGuardError("T3 workflow must be an object")
        workflow_base = f"{base}/workflows/{workflow_index}"
        states = workflow.get("states")
        transitions = workflow.get("transitions")
        if not isinstance(states, list) or not isinstance(transitions, list):
            raise TargetEvidenceGuardError("T3 workflow is incomplete")
        for state_index, state in enumerate(states):
            if not isinstance(state, Mapping):
                raise TargetEvidenceGuardError("T3 state must be an object")
            pointer = f"{workflow_base}/states/{state_index}"
            result.append(("state", "t3", pointer, normalize_state(state)))
            _collect_leaf_categories(
                state,
                target="t3",
                pointer=pointer,
                result=result,
            )
        for transition_index, transition in enumerate(transitions):
            if not isinstance(transition, Mapping):
                raise TargetEvidenceGuardError("T3 transition must be an object")
            pointer = f"{workflow_base}/transitions/{transition_index}"
            operation = transition.get("operation")
            if isinstance(operation, str):
                result.append(("operation", "t3", f"{pointer}/operation", operation))
            branch = transition_branch_signature(transition)
            if branch is not None:
                result.append(("branch", "t3", pointer, branch))
            _collect_leaf_categories(
                transition,
                target="t3",
                pointer=pointer,
                result=result,
                include_operations=False,
            )
        result.extend(_workflow_branch_values(workflow, workflow_base))
    return result


def _collect_leaf_categories(
    value: Any,
    *,
    target: str,
    pointer: str,
    result: list[tuple[str, str, str, Any]],
    include_operations: bool = True,
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_pointer = f"{pointer}/{_encode_pointer_token(str(key))}"
            if key in {"sequence", "sequence_architecture"} and isinstance(child, str):
                result.append(("sequence", target, child_pointer, child))
                continue
            if key == "modifications" and isinstance(child, list):
                for index, modification in enumerate(child):
                    if isinstance(modification, str) and modification:
                        result.append(
                            (
                                "modification",
                                target,
                                f"{child_pointer}/{index}",
                                modification,
                            )
                        )
                continue
            if key == "operation" and include_operations and isinstance(child, str):
                result.append(("operation", target, child_pointer, child))
                continue
            _collect_leaf_categories(
                child,
                target=target,
                pointer=child_pointer,
                result=result,
                include_operations=include_operations,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _collect_leaf_categories(
                child,
                target=target,
                pointer=f"{pointer}/{index}",
                result=result,
                include_operations=include_operations,
            )


def normalize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    strands = state.get("strands")
    if not isinstance(strands, list) or not strands:
        raise TargetEvidenceGuardError("state lacks physical strands")
    strand_positions: dict[str, str] = {}
    segment_positions: dict[tuple[str, str], str] = {}
    for strand_index, strand in enumerate(strands):
        if not isinstance(strand, Mapping) or not isinstance(strand.get("strand_id"), str):
            raise TargetEvidenceGuardError("state strand lacks strand_id")
        strand_id = strand["strand_id"]
        if strand_id in strand_positions:
            raise TargetEvidenceGuardError("state has duplicate strand_id")
        strand_positions[strand_id] = f"strand-{strand_index}"
        segments = strand.get("segments")
        if not isinstance(segments, list):
            raise TargetEvidenceGuardError("state strand lacks segments")
        for segment_index, segment in enumerate(segments):
            if not isinstance(segment, Mapping) or not isinstance(
                segment.get("segment_id"), str
            ):
                raise TargetEvidenceGuardError("state segment lacks segment_id")
            key = (strand_id, segment["segment_id"])
            if key in segment_positions:
                raise TargetEvidenceGuardError("state has duplicate segment_id")
            segment_positions[key] = f"strand-{strand_index}/segment-{segment_index}"

    normalized_strands: list[dict[str, Any]] = []
    for strand in strands:
        normalized_segments: list[dict[str, Any]] = []
        for segment in strand["segments"]:
            normalized_segment = {
                key: _normalize_oligo_derivations(child)
                if key == "oligo_derivations"
                else child
                for key, child in segment.items()
                if key != "segment_id"
            }
            normalized_segments.append(normalized_segment)
        normalized_strands.append(
            {
                key: child
                for key, child in strand.items()
                if key not in {"strand_id", "name", "segments"}
            }
            | {"segments": normalized_segments}
        )

    paired_regions: list[dict[str, Any]] = []
    for region in state.get("paired_regions", []):
        if not isinstance(region, Mapping):
            raise TargetEvidenceGuardError("state paired region must be an object")
        normalized_region: dict[str, Any] = {"relationship": region.get("relationship")}
        for side_name in ("side_1", "side_2"):
            side = region.get(side_name)
            if not isinstance(side, Mapping) or side.get("strand_id") not in strand_positions:
                raise TargetEvidenceGuardError("state paired-region side is invalid")
            strand_id = side["strand_id"]
            segment_ids = side.get("segment_ids")
            if not isinstance(segment_ids, list):
                raise TargetEvidenceGuardError(
                    "state paired-region segment IDs are invalid"
                )
            try:
                normalized_segments = [
                    segment_positions[(strand_id, segment_id)]
                    for segment_id in segment_ids
                ]
            except KeyError as error:
                raise TargetEvidenceGuardError(
                    "state paired region references an unknown segment"
                ) from error
            normalized_region[side_name] = {
                "strand": strand_positions[strand_id],
                "segments": normalized_segments,
            }
        paired_regions.append(normalized_region)

    discontinuities: list[dict[str, Any]] = []
    for discontinuity in state.get("discontinuities", []):
        if not isinstance(discontinuity, Mapping):
            raise TargetEvidenceGuardError("state discontinuity must be an object")
        strand_id = discontinuity.get("strand_id")
        try:
            discontinuities.append(
                {
                    "strand": strand_positions[strand_id],
                    "after": segment_positions[
                        (strand_id, discontinuity.get("after_segment_id"))
                    ],
                    "before": segment_positions[
                        (strand_id, discontinuity.get("before_segment_id"))
                    ],
                    "kind": discontinuity.get("kind"),
                }
            )
        except KeyError as error:
            raise TargetEvidenceGuardError(
                "state discontinuity references an unknown strand or segment"
            ) from error
    reference = state.get("reference_strand_id")
    if reference not in strand_positions:
        raise TargetEvidenceGuardError("state reference strand is unknown")
    properties = state.get("properties")
    if not isinstance(properties, list):
        raise TargetEvidenceGuardError("state properties must be an array")
    return {
        "molecule_type": state.get("molecule_type"),
        "strand_architecture": state.get("strand_architecture"),
        "reference_strand": strand_positions[reference],
        "physical_state": state.get("physical_state"),
        "strands": normalized_strands,
        "paired_regions": paired_regions,
        "discontinuities": discontinuities,
        "properties": sorted(properties),
    }


def _normalize_oligo_derivations(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    result = []
    for derivation in value:
        if isinstance(derivation, Mapping):
            result.append(
                {
                    key: child
                    for key, child in derivation.items()
                    if key != "oligo_id"
                }
            )
        else:
            result.append(derivation)
    return result


def transition_branch_signature(
    transition: Mapping[str, Any],
) -> dict[str, Any] | None:
    products = transition.get("product_state_ids")
    carried = transition.get("carried_forward_product_ids")
    discarded = transition.get("discarded_product_ids")
    substrates = transition.get("substrate_state_ids")
    if not all(
        isinstance(item, list) for item in (products, carried, discarded, substrates)
    ):
        raise TargetEvidenceGuardError("transition branch fields must be arrays")
    if not (
        len(products) > 1
        or len(carried) > 1
        or bool(discarded)
        or transition.get("operation") == "sample_split"
    ):
        return None
    product_positions = {state_id: index for index, state_id in enumerate(products)}
    if len(product_positions) != len(products):
        raise TargetEvidenceGuardError("transition has duplicate products")
    try:
        carried_positions = sorted(product_positions[state_id] for state_id in carried)
        discarded_positions = sorted(
            product_positions[state_id] for state_id in discarded
        )
    except KeyError as error:
        raise TargetEvidenceGuardError(
            "transition product dispositions reference an unknown product"
        ) from error
    return {
        "kind": "transition_partition",
        "operation": transition.get("operation"),
        "substrate_count": len(substrates),
        "product_count": len(products),
        "carried_product_positions": carried_positions,
        "discarded_product_positions": discarded_positions,
    }


def _workflow_branch_values(
    workflow: Mapping[str, Any],
    workflow_pointer: str,
) -> list[tuple[str, str, str, Any]]:
    states = workflow.get("states", [])
    transitions = workflow.get("transitions", [])
    state_positions = {
        state.get("state_id"): index
        for index, state in enumerate(states)
        if isinstance(state, Mapping) and isinstance(state.get("state_id"), str)
    }
    consumers: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for transition in transitions:
        if not isinstance(transition, Mapping):
            continue
        for state_id in transition.get("substrate_state_ids", []):
            if isinstance(state_id, str):
                consumers[state_id].append(transition)
    result: list[tuple[str, str, str, Any]] = []
    for state_id, records in consumers.items():
        if len(records) < 2 or state_id not in state_positions:
            continue
        shapes = sorted(
            (
                {
                    "operation": item.get("operation"),
                    "substrate_count": len(item.get("substrate_state_ids", [])),
                    "product_count": len(item.get("product_state_ids", [])),
                    "carried_count": len(item.get("carried_forward_product_ids", [])),
                    "discarded_count": len(item.get("discarded_product_ids", [])),
                }
                for item in records
            ),
            key=canonical_digest,
        )
        result.append(
            (
                "branch",
                "t3",
                workflow_pointer,
                {
                    "kind": "workflow_fanout",
                    "substrate_position": state_positions[state_id],
                    "consumer_count": len(records),
                    "consumer_shapes": shapes,
                },
            )
        )
    modalities = sorted(
        {
            output.get("modality")
            for output in workflow.get("final_outputs", [])
            if isinstance(output, Mapping) and isinstance(output.get("modality"), str)
        }
    )
    if len(modalities) > 1:
        result.append(
            (
                "branch",
                "t3",
                f"{workflow_pointer}/final_outputs",
                {
                    "kind": "modality_branching",
                    "modalities": modalities,
                    "output_count": len(workflow.get("final_outputs", [])),
                },
            )
        )
    return result


def _has_target_support(
    category: str,
    target: str,
    pointer: str,
    value: Any,
    record: Mapping[str, Any],
    *,
    claims: Mapping[tuple[str, str], Mapping[str, Any]],
    inventory: Mapping[str, Mapping[str, Any]],
) -> bool:
    if _exact_or_collection_claim_support(
        category,
        target,
        pointer,
        value,
        record,
        claims,
    ):
        return True
    if category in {"sequence", "modification"}:
        return False
    if category == "state":
        return _state_record_support(pointer, value, record, claims, inventory)
    if category == "operation":
        return _event_record_support(pointer, value, record, inventory)
    if category == "branch":
        return _branch_record_support(pointer, value, record, claims, inventory)
    return False


def _exact_or_collection_claim_support(
    category: str,
    target: str,
    pointer: str,
    value: Any,
    record: Mapping[str, Any],
    claims: Mapping[tuple[str, str], Mapping[str, Any]],
) -> bool:
    claim = claims.get((target, pointer))
    if claim is not None and _supported(claim):
        return True
    if category != "modification":
        return False
    parent = pointer.rsplit("/", 1)[0]
    claim = claims.get((target, parent))
    if claim is None or not _supported(claim):
        return False
    try:
        collection = resolve_json_pointer(record["drafts"][target], parent)
    except TargetWorkRecordError:
        return False
    return isinstance(collection, list) and value in collection


def _state_record_support(
    pointer: str,
    normalized_state: Any,
    record: Mapping[str, Any],
    claims: Mapping[tuple[str, str], Mapping[str, Any]],
    inventory: Mapping[str, Mapping[str, Any]],
) -> bool:
    parsed = _parse_workflow_entity_pointer(pointer, "states")
    if parsed is None:
        return False
    workflow_index, state_index = parsed
    workflow = record["drafts"]["t3"]["workflows"][workflow_index]
    state = workflow["states"][state_index]
    for signature in record["state_signatures"]:
        if (
            signature.get("workflow_id") != workflow.get("workflow_id")
            or signature.get("state_id") != state.get("state_id")
            or not _state_signature_matches(signature, state)
            or normalize_state(state) != normalized_state
        ):
            continue
        if not _inventory_ids_supported(
            signature.get("inventory_ids"),
            inventory,
            expected_kind="molecular_state",
            target="t3",
            pointer=pointer,
        ):
            continue
        detail_pointers = _state_detail_claim_pointers(pointer, state)
        if all(
            ("t3", detail_pointer) in claims
            and _supported(claims[("t3", detail_pointer)])
            for detail_pointer in detail_pointers
        ):
            return True
    return False


def _state_detail_claim_pointers(
    pointer: str,
    state: Mapping[str, Any],
) -> set[str]:
    result = {
        f"{pointer}/physical_state",
        f"{pointer}/properties",
    }
    for strand_index, strand in enumerate(state.get("strands", [])):
        strand_pointer = f"{pointer}/strands/{strand_index}"
        result.update(
            {
                f"{strand_pointer}/orientation",
                f"{strand_pointer}/molecule_type",
            }
        )
        if "sequence_architecture" in strand:
            result.add(f"{strand_pointer}/sequence_architecture")
        for segment_index, segment in enumerate(strand.get("segments", [])):
            segment_pointer = f"{strand_pointer}/segments/{segment_index}"
            result.update(
                {
                    f"{segment_pointer}/role",
                    f"{segment_pointer}/structural_role",
                }
            )
            for field in ("sequence", "length", "placeholder", "oligo_derivations"):
                if field in segment:
                    result.add(f"{segment_pointer}/{field}")
    paired = state.get("paired_regions", [])
    discontinuities = state.get("discontinuities", [])
    if not paired:
        result.add(f"{pointer}/paired_regions")
    else:
        for index, region in enumerate(paired):
            region_pointer = f"{pointer}/paired_regions/{index}"
            result.add(f"{region_pointer}/relationship")
            result.add(f"{region_pointer}/side_1")
            result.add(f"{region_pointer}/side_2")
    if not discontinuities:
        result.add(f"{pointer}/discontinuities")
    else:
        for index, _ in enumerate(discontinuities):
            result.add(f"{pointer}/discontinuities/{index}")
    return result


def _event_record_support(
    operation_pointer: str,
    value: Any,
    record: Mapping[str, Any],
    inventory: Mapping[str, Mapping[str, Any]],
) -> bool:
    transition_pointer = operation_pointer.rsplit("/operation", 1)[0]
    parsed = _parse_workflow_entity_pointer(transition_pointer, "transitions")
    if parsed is None:
        return False
    workflow_index, transition_index = parsed
    workflow = record["drafts"]["t3"]["workflows"][workflow_index]
    transition = workflow["transitions"][transition_index]
    if transition.get("operation") != value:
        return False
    return _matching_supported_event(
        workflow,
        transition,
        transition_pointer,
        record,
        inventory,
    )


def _branch_record_support(
    pointer: str,
    value: Any,
    record: Mapping[str, Any],
    claims: Mapping[tuple[str, str], Mapping[str, Any]],
    inventory: Mapping[str, Mapping[str, Any]],
) -> bool:
    if not isinstance(value, Mapping):
        return False
    kind = value.get("kind")
    parsed_transition = _parse_workflow_entity_pointer(pointer, "transitions")
    if kind == "transition_partition" and parsed_transition is not None:
        workflow_index, transition_index = parsed_transition
        workflow = record["drafts"]["t3"]["workflows"][workflow_index]
        transition = workflow["transitions"][transition_index]
        return (
            transition_branch_signature(transition) == value
            and _matching_supported_event(
                workflow,
                transition,
                pointer,
                record,
                inventory,
            )
        )
    if kind == "multiple_workflows":
        claim = claims.get(("t3", "/workflows"))
        return claim is not None and _supported(claim)
    workflow_index = _parse_workflow_pointer(pointer)
    if workflow_index is None and pointer.endswith("/final_outputs"):
        workflow_index = _parse_workflow_pointer(pointer.rsplit("/final_outputs", 1)[0])
    if workflow_index is None:
        return False
    workflow = record["drafts"]["t3"]["workflows"][workflow_index]
    workflow_pointer = f"/workflows/{workflow_index}"
    if not _inventory_pointer_supported(
        inventory,
        expected_kind="branch",
        target="t3",
        pointers={pointer, workflow_pointer},
    ):
        return False
    if kind == "modality_branching":
        claim = claims.get(("t3", f"{workflow_pointer}/final_outputs"))
        return claim is not None and _supported(claim)
    if kind != "workflow_fanout":
        return False
    state_position = value.get("substrate_position")
    states = workflow.get("states", [])
    if not isinstance(state_position, int) or not 0 <= state_position < len(states):
        return False
    state_id = states[state_position].get("state_id")
    consumers = [
        transition
        for transition in workflow.get("transitions", [])
        if state_id in transition.get("substrate_state_ids", [])
    ]
    matching_values = [
        branch_value
        for _, _, _, branch_value in _workflow_branch_values(
            workflow, workflow_pointer
        )
        if isinstance(branch_value, Mapping)
        and branch_value.get("kind") == "workflow_fanout"
        and branch_value.get("substrate_position") == state_position
    ]
    if matching_values != [value] or not consumers:
        return False
    transition_positions = {
        transition.get("transition_id"): index
        for index, transition in enumerate(workflow.get("transitions", []))
    }
    return all(
        _matching_supported_event(
            workflow,
            transition,
            f"{workflow_pointer}/transitions/"
            f"{transition_positions[transition.get('transition_id')]}",
            record,
            inventory,
        )
        for transition in consumers
    )


def _matching_supported_event(
    workflow: Mapping[str, Any],
    transition: Mapping[str, Any],
    transition_pointer: str,
    record: Mapping[str, Any],
    inventory: Mapping[str, Mapping[str, Any]],
) -> bool:
    for event in record["event_records"]:
        if (
            event.get("workflow_id") == workflow.get("workflow_id")
            and event.get("transition_id") == transition.get("transition_id")
            and _event_matches(event, transition)
            and _inventory_ids_supported(
                event.get("inventory_ids"),
                inventory,
                expected_kind="molecular_event",
                target="t3",
                pointer=transition_pointer,
            )
        ):
            return True
    return False


def _state_signature_matches(
    signature: Mapping[str, Any],
    state: Mapping[str, Any],
) -> bool:
    expected_strands = [
        {
            "strand_id": strand.get("strand_id"),
            "molecule_type": strand.get("molecule_type"),
            "segment_ids": [
                segment.get("segment_id") for segment in strand.get("segments", [])
            ],
        }
        for strand in state.get("strands", [])
        if isinstance(strand, Mapping)
    ]
    return (
        signature.get("strand_architecture") == state.get("strand_architecture")
        and signature.get("reference_strand_id") == state.get("reference_strand_id")
        and signature.get("strands") == expected_strands
        and signature.get("paired_region_ids")
        == [item.get("paired_region_id") for item in state.get("paired_regions", [])]
        and signature.get("discontinuity_ids")
        == [item.get("discontinuity_id") for item in state.get("discontinuities", [])]
    )


def _event_matches(
    event: Mapping[str, Any],
    transition: Mapping[str, Any],
) -> bool:
    return all(
        event.get(field) == transition.get(field)
        for field in (
            "operation",
            "substrate_state_ids",
            "product_state_ids",
            "carried_forward_product_ids",
            "discarded_product_ids",
            "oligo_ids",
        )
    )


def _inventory_ids_supported(
    inventory_ids: Any,
    inventory: Mapping[str, Mapping[str, Any]],
    *,
    expected_kind: str,
    target: str,
    pointer: str,
) -> bool:
    if not isinstance(inventory_ids, list) or not inventory_ids:
        return False
    return any(
        inventory_id in inventory
        and inventory[inventory_id].get("item_kind") == expected_kind
        and _supported(inventory[inventory_id])
        and _item_has_pointer(inventory[inventory_id], target, pointer)
        for inventory_id in inventory_ids
    )


def _inventory_pointer_supported(
    inventory: Mapping[str, Mapping[str, Any]],
    *,
    expected_kind: str,
    target: str,
    pointers: set[str],
) -> bool:
    return any(
        item.get("item_kind") == expected_kind
        and _supported(item)
        and any(_item_has_pointer(item, target, pointer) for pointer in pointers)
        for item in inventory.values()
    )


def _item_has_pointer(item: Mapping[str, Any], target: str, pointer: str) -> bool:
    return any(
        candidate.get("target") == target
        and candidate.get("json_pointer") == pointer
        for candidate in item.get("target_pointers", [])
        if isinstance(candidate, Mapping)
    )


def _supported(record: Mapping[str, Any]) -> bool:
    support = record.get("support")
    if support == "explicit":
        return bool(record.get("source_locators"))
    return (
        support == "derivable"
        and bool(record.get("source_locators"))
        and isinstance(record.get("derivation"), str)
        and bool(record["derivation"].strip())
    )


def _parse_workflow_entity_pointer(
    pointer: str,
    collection: str,
) -> tuple[int, int] | None:
    parts = pointer.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "workflows" or parts[2] != collection:
        return None
    try:
        return int(parts[1]), int(parts[3])
    except ValueError:
        return None


def _parse_workflow_pointer(pointer: str) -> int | None:
    parts = pointer.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "workflows":
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _encode_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        work_record_path = Path(args.work_record)
        work_record = load_json_object(work_record_path, "target work record")
        manifest, catalog, exemplars = load_catalog(Path(args.catalog))
        retrieval = load_json_object(Path(args.retrieval), "exemplar retrieval")
        usage = load_json_object(Path(args.usage), "exemplar usage")
        report = audit_target_evidence(
            work_record,
            manifest,
            catalog,
            exemplars,
            retrieval,
            usage,
            target_work_record_sha256=file_sha256(work_record_path),
        )
        write_json_atomic(Path(args.report_out), report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "pass" else 1
    except (
        ExemplarMemoryError,
        TargetWorkRecordError,
        TargetEvidenceGuardError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "error",
            "finding_count": 0,
            "checked_overlap_count": 0,
            "findings": [],
            "error": str(error),
        }
        try:
            write_json_atomic(Path(args.report_out), report)
        except OSError:
            pass
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
