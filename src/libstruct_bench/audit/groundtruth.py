from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping

from libstruct_bench.modalities import (
    CANONICAL_MODALITIES,
    canonical_modality_label,
    modality_key,
)
from libstruct_bench.normalization import sequence_tokens

from .artifacts import AuditArtifactError, validate_document


TASK_ARTIFACTS = {
    "T1": {
        "filename": "groundtruth_final_lib_struct.json",
        "schema": "final_library_groundtruth.schema.json",
        "root_key": "libraries",
    },
    "T2": {
        "filename": "groundtruth_oligos.json",
        "schema": "oligo_groundtruth.schema.json",
        "root_key": "oligos",
    },
    "T3": {
        "filename": "groundtruth_library_generation_workflow.json",
        "schema": "library_generation_workflow.schema.json",
        "root_key": "workflows",
    },
}


class GroundtruthValidationError(ValueError):
    """Raised when T1-T3 documents are invalid or internally inconsistent."""


def validate_task_document(
    task: str,
    document: dict[str, Any],
    *,
    protocol_id: str,
    schema_dir: Path,
) -> None:
    if task not in TASK_ARTIFACTS:
        raise GroundtruthValidationError(f"unknown ground-truth task: {task}")
    if document.get("protocol_id") != protocol_id:
        raise GroundtruthValidationError(
            f"{task} protocol_id does not match {protocol_id!r}"
        )
    _validate_canonical_modality_labels(task, document)
    try:
        validate_document(
            document,
            schema_dir / TASK_ARTIFACTS[task]["schema"],
            label=f"{task} ground truth",
        )
    except AuditArtifactError as error:
        raise GroundtruthValidationError(str(error)) from error
    _validate_ordered_sequence_assemblies(task, document)


def documents_by_task(
    documents: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Identify T1-T3 documents by their canonical root collection."""

    result: dict[str, dict[str, Any]] = {}
    for source_id, document in documents.items():
        matches = [
            task
            for task, details in TASK_ARTIFACTS.items()
            if details["root_key"] in document
        ]
        if not matches:
            continue
        if len(matches) != 1:
            raise GroundtruthValidationError(
                f"ground-truth document {source_id} has multiple task root collections"
            )
        task = matches[0]
        if task in result:
            raise GroundtruthValidationError(
                f"multiple ground-truth documents identify as {task}: {source_id}"
            )
        result[task] = document
    return result


def validate_cross_task_links(documents: Mapping[str, dict[str, Any]]) -> None:
    """Validate T1-T2-T3 references, scopes, reachability, and graph structure."""

    if not documents:
        return
    _preflight_cross_task_shape(documents)
    for task in ("T1", "T3"):
        document = documents.get(task)
        if document is not None:
            _validate_ordered_sequence_assemblies(task, document)
    protocol_ids = {document.get("protocol_id") for document in documents.values()}
    if len(protocol_ids) != 1:
        raise GroundtruthValidationError("T1, T2, and T3 protocol IDs must agree")
    scopes = {
        _scope_key(scope)
        for document in documents.values()
        if (scope := document.get("protocol_scope")) is not None
    }
    if len(scopes) > 1:
        raise GroundtruthValidationError(
            "T1, T2, and T3 protocol versions and variant scopes must agree"
        )

    t1 = documents.get("T1")
    t2 = documents.get("T2")
    t3 = documents.get("T3")
    document_scope = next(
        (
            scope
            for document in documents.values()
            if (scope := document.get("protocol_scope")) is not None
        ),
        None,
    )

    libraries = list(t1.get("libraries", []) if t1 else [])
    oligos = {item["oligo_id"]: item for item in (t2.get("oligos", []) if t2 else [])}
    _require_unique_count(oligos, t2.get("oligos", []) if t2 else [], "T2 oligos")

    if t1 is not None:
        segment_ids: set[str] = set()
        for library_index, library in enumerate(t1["libraries"]):
            library_label = f"T1 library at index {library_index}"
            _required_field(library, "modality", library_label)
            library_scope = _resolved_scope(library, document_scope)
            _validate_child_scope(
                library.get("protocol_scope"),
                document_scope,
                library_label,
            )
            for segment in library["segments"]:
                _add_unique(segment_ids, segment["segment_id"], "T1 segment")
                _validate_child_scope(
                    segment.get("protocol_scope"),
                    library_scope,
                    f"T1 segment {segment['segment_id']}",
                )
                derivations = segment.get("oligo_derivations", [])
                oligo_ids = [item["oligo_id"] for item in derivations]
                _require_refs(
                    oligo_ids,
                    set(oligos),
                    f"T1 segment {segment['segment_id']} oligo derivations",
                    target_available=t2 is not None,
                )
                if len(oligo_ids) != len(set(oligo_ids)):
                    raise GroundtruthValidationError(
                        f"T1 segment {segment['segment_id']} derives from the "
                        "same T2 oligo more than once"
                    )
                segment_scope = _resolved_scope(segment, library_scope)
                for derivation in derivations:
                    oligo_id = derivation["oligo_id"]
                    oligo = oligos.get(oligo_id)
                    if oligo is not None:
                        _require_scope_compatibility(
                            segment_scope,
                            _resolved_scope(oligo, document_scope),
                            f"T1 segment {segment['segment_id']} and T2 oligo {oligo_id}",
                            document_scope,
                        )
                        _validate_oligo_derivation_orientation(
                            segment,
                            oligo,
                            derivation,
                            f"T1 segment {segment['segment_id']} and T2 oligo {oligo_id}",
                        )

    if t2 is not None:
        for oligo in t2["oligos"]:
            oligo_scope = _resolved_scope(oligo, document_scope)
            _validate_child_scope(
                oligo.get("protocol_scope"),
                document_scope,
                f"T2 oligo {oligo['oligo_id']}",
            )
            for component_index, component in enumerate(oligo["components"]):
                _validate_child_scope(
                    component.get("protocol_scope"),
                    oligo_scope,
                    f"T2 oligo {oligo['oligo_id']} component at index {component_index}",
                )

    if t3 is None:
        return
    workflow_ids: set[str] = set()
    terminal_states: list[tuple[dict[str, Any], dict[str, Any] | None, str]] = []
    for workflow in t3["workflows"]:
        workflow_id = workflow["workflow_id"]
        _add_unique(workflow_ids, workflow_id, "T3 workflow")
        workflow_scope = _resolved_scope(workflow, document_scope)
        _validate_child_scope(
            workflow.get("protocol_scope"),
            document_scope,
            f"T3 workflow {workflow_id}",
        )
        states = {state["state_id"]: state for state in workflow["states"]}
        _require_unique_count(
            states, workflow["states"], f"T3 workflow {workflow_id} states"
        )
        for state in workflow["states"]:
            state_scope = _resolved_scope(state, workflow_scope)
            _validate_child_scope(
                state.get("protocol_scope"),
                workflow_scope,
                f"T3 state {state['state_id']}",
            )
            validate_molecular_state_architecture(
                state, label=f"T3 state {state['state_id']}"
            )
            for strand in state["strands"]:
                for segment in strand["segments"]:
                    derivations = segment.get("oligo_derivations", [])
                    oligo_ids = [item["oligo_id"] for item in derivations]
                    _require_refs(
                        oligo_ids,
                        set(oligos),
                        f"T3 state {state['state_id']} strand {strand['strand_id']} "
                        f"segment {segment['segment_id']} oligo derivations",
                        target_available=t2 is not None,
                    )
                    if len(oligo_ids) != len(set(oligo_ids)):
                        raise GroundtruthValidationError(
                            f"T3 state {state['state_id']} strand "
                            f"{strand['strand_id']} segment {segment['segment_id']} "
                            "derives from the same T2 oligo more than once"
                        )
                    for derivation in derivations:
                        oligo_id = derivation["oligo_id"]
                        oligo = oligos.get(oligo_id)
                        if oligo is not None:
                            _require_scope_compatibility(
                                state_scope,
                                _resolved_scope(oligo, document_scope),
                                f"T3 state {state['state_id']} and T2 oligo {oligo_id}",
                                document_scope,
                            )
                            _validate_oligo_derivation_orientation(
                                segment,
                                oligo,
                                derivation,
                                f"T3 state {state['state_id']} strand "
                                f"{strand['strand_id']} segment "
                                f"{segment['segment_id']} and T2 oligo {oligo_id}",
                            )

        state_ids = set(states)
        initial_ids = set(workflow["initial_state_ids"])
        final_output_state_ids = [
            output["state_id"] for output in workflow["final_outputs"]
        ]
        if len(final_output_state_ids) != len(set(final_output_state_ids)):
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} lists the same terminal state more than once"
            )
        final_ids = set(final_output_state_ids)
        _require_refs(
            workflow["initial_state_ids"],
            state_ids,
            f"T3 workflow {workflow_id} initial_state_ids",
            target_available=True,
        )
        _require_refs(
            final_output_state_ids,
            state_ids,
            f"T3 workflow {workflow_id} final_outputs",
            target_available=True,
        )

        terminal_states.extend(
            (states[output["state_id"]], workflow_scope, output["modality"])
            for output in workflow["final_outputs"]
        )

        adjacency: dict[str, set[str]] = defaultdict(set)
        continuing_adjacency: dict[str, set[str]] = defaultdict(set)
        carried: set[str] = set()
        discarded_products: set[str] = set()
        downstream_substrates: set[str] = set()
        transition_ids: set[str] = set()
        for transition in workflow["transitions"]:
            transition_id = transition["transition_id"]
            _add_unique(
                transition_ids,
                transition_id,
                f"T3 workflow {workflow_id} transition",
            )
            _validate_child_scope(
                transition.get("protocol_scope"),
                workflow_scope,
                f"T3 transition {transition_id}",
            )
            substrates = set(transition["substrate_state_ids"])
            products = set(transition["product_state_ids"])
            continuing = set(transition["carried_forward_product_ids"])
            discarded = set(transition["discarded_product_ids"])
            _require_refs(
                list(substrates | products),
                state_ids,
                f"T3 transition {transition_id} state references",
                target_available=True,
            )
            _require_refs(
                transition["oligo_ids"],
                set(oligos),
                f"T3 transition {transition_id} oligo_ids",
                target_available=t2 is not None,
            )
            if not continuing.issubset(products) or not discarded.issubset(products):
                raise GroundtruthValidationError(
                    f"T3 transition {transition_id} carried/discarded products must be product states"
                )
            if continuing & discarded:
                raise GroundtruthValidationError(
                    f"T3 transition {transition_id} cannot both carry and discard a product"
                )
            if continuing | discarded != products:
                raise GroundtruthValidationError(
                    f"T3 transition {transition_id} must classify every product as carried forward or discarded"
                )
            carried.update(continuing)
            discarded_products.update(discarded)
            downstream_substrates.update(substrates)
            for substrate in substrates:
                adjacency[substrate].update(products)
                continuing_adjacency[substrate].update(continuing)

        reused_discarded = discarded_products & downstream_substrates
        if reused_discarded:
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} uses discarded products as downstream substrates: {', '.join(sorted(reused_discarded))}"
            )
        unproduced_substrates = downstream_substrates - initial_ids - carried
        if unproduced_substrates:
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} has downstream substrates that were not carried forward: {', '.join(sorted(unproduced_substrates))}"
            )

        unused_carried = carried - final_ids - downstream_substrates
        if unused_carried:
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} has nonfinal carried-forward products that are not downstream substrates: {', '.join(sorted(unused_carried))}"
            )
        _reject_cycles(state_ids, adjacency, workflow_id)
        _require_weakly_connected_workflow(
            state_ids=state_ids,
            transitions=workflow["transitions"],
            workflow_id=workflow_id,
        )
        reachable = _reachable(initial_ids, continuing_adjacency)
        unreachable = final_ids - reachable
        if unreachable:
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} final states are unreachable: {', '.join(sorted(unreachable))}"
            )

        for transition in workflow["transitions"]:
            for oligo_id in transition["oligo_ids"]:
                oligo = oligos.get(oligo_id)
                if oligo is not None:
                    _require_scope_compatibility(
                        _resolved_scope(transition, workflow_scope),
                        _resolved_scope(oligo, document_scope),
                        f"T3 transition {transition['transition_id']} and T2 oligo {oligo_id}",
                        document_scope,
                    )

    if t1 is not None:
        _validate_terminal_libraries(
            terminal_states=terminal_states,
            libraries=libraries,
            document_scope=document_scope,
        )


def _scope_key(scope: dict[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    return scope["protocol_version"], tuple(sorted(scope["applicable_variants"]))


def _modality_key(value: str) -> str:
    return modality_key(value)


def _validate_canonical_modality_labels(task: str, document: Mapping[str, Any]) -> None:
    if task == "T1":
        records = document.get("libraries", [])
        label = "T1 library"
    elif task == "T3":
        workflows = document.get("workflows", [])
        if not isinstance(workflows, list):
            return
        records = [
            output
            for workflow in workflows
            if isinstance(workflow, dict)
            for output in workflow.get("final_outputs", [])
            if isinstance(output, dict)
        ]
        label = "T3 final output"
    else:
        return
    if not isinstance(records, list):
        return
    for index, record in enumerate(records):
        value = record.get("modality")
        if isinstance(value, str) and value not in CANONICAL_MODALITIES:
            canonical = canonical_modality_label(value)
            expectation = (
                repr(canonical)
                if canonical in CANONICAL_MODALITIES
                else "one of "
                + ", ".join(repr(item) for item in sorted(CANONICAL_MODALITIES))
            )
            raise GroundtruthValidationError(
                f"{label} at index {index} must use canonical modality "
                f"{expectation}, not {value!r}"
            )


def _preflight_cross_task_shape(
    documents: Mapping[str, dict[str, Any]],
) -> None:
    for task, document in documents.items():
        if not isinstance(document, dict):
            raise GroundtruthValidationError(f"{task} ground truth must be an object")
        _required_field(document, "protocol_id", f"{task} ground truth")

    t1 = documents.get("T1")
    if t1 is not None:
        for index, library in enumerate(
            _required_list(t1, "libraries", "T1 ground truth")
        ):
            label = f"T1 library at index {index}"
            _required_field(library, "modality", label)
            for segment_index, segment in enumerate(
                _required_list(library, "segments", label)
            ):
                _required_field(
                    segment,
                    "segment_id",
                    f"{label} segment at index {segment_index}",
                )

    t2 = documents.get("T2")
    if t2 is not None:
        for index, oligo in enumerate(_required_list(t2, "oligos", "T2 ground truth")):
            label = f"T2 oligo at index {index}"
            oligo_id = _required_field(oligo, "oligo_id", label)
            label = f"T2 oligo {oligo_id}"
            _required_list(oligo, "components", label)

    t3 = documents.get("T3")
    if t3 is not None:
        for workflow_index, workflow in enumerate(
            _required_list(t3, "workflows", "T3 ground truth")
        ):
            label = f"T3 workflow at index {workflow_index}"
            workflow_id = _required_field(workflow, "workflow_id", label)
            label = f"T3 workflow {workflow_id}"
            for state_index, state in enumerate(
                _required_list(workflow, "states", label)
            ):
                state_label = f"{label} state at index {state_index}"
                state_id = _required_field(state, "state_id", state_label)
                state_label = f"{label} state {state_id}"
                _required_field(state, "strand_architecture", state_label)
                _required_field(state, "reference_strand_id", state_label)
                for strand_index, strand in enumerate(
                    _required_list(state, "strands", state_label)
                ):
                    strand_label = f"{state_label} strand at index {strand_index}"
                    strand_id = _required_field(strand, "strand_id", strand_label)
                    strand_label = f"{state_label} strand {strand_id}"
                    _required_field(strand, "orientation", strand_label)
                    for segment_index, segment in enumerate(
                        _required_list(strand, "segments", strand_label)
                    ):
                        segment_id = _required_field(
                            segment,
                            "segment_id",
                            f"{strand_label} segment at index {segment_index}",
                        )
                        _required_field(
                            segment,
                            "structural_role",
                            f"{strand_label} segment {segment_id}",
                        )
                _required_list(state, "paired_regions", state_label)
                _required_list(state, "discontinuities", state_label)
            for transition_index, transition in enumerate(
                _required_list(workflow, "transitions", label)
            ):
                transition_label = f"{label} transition at index {transition_index}"
                _required_field(transition, "transition_id", transition_label)
                for field in (
                    "substrate_state_ids",
                    "product_state_ids",
                    "carried_forward_product_ids",
                    "discarded_product_ids",
                    "oligo_ids",
                ):
                    _required_list(transition, field, transition_label)
            _required_list(workflow, "initial_state_ids", label)
            for output_index, output in enumerate(
                _required_list(workflow, "final_outputs", label)
            ):
                output_label = f"{label} final output at index {output_index}"
                _required_field(output, "state_id", output_label)
                _required_field(output, "modality", output_label)


def _required_field(item: Any, field: str, label: str) -> Any:
    if not isinstance(item, dict) or field not in item:
        raise GroundtruthValidationError(f"{label} is missing required field {field!r}")
    return item[field]


def _required_list(item: Any, field: str, label: str) -> list[Any]:
    value = _required_field(item, field, label)
    if not isinstance(value, list):
        raise GroundtruthValidationError(f"{label} field {field!r} must be an array")
    return value


def validate_molecular_state_architecture(
    state: dict[str, Any], *, label: str | None = None
) -> None:
    """Validate explicit strands, pairings, overhangs, and discontinuities."""

    state_label = label or f"molecular state {state.get('state_id', '<unknown>')}"
    strands_list = _required_list(state, "strands", state_label)
    strands: dict[str, dict[str, Any]] = {}
    for index, strand in enumerate(strands_list):
        strand_id = _required_field(
            strand, "strand_id", f"{state_label} strand at index {index}"
        )
        strands[strand_id] = strand
    _require_unique_count(strands, strands_list, f"{state_label} strands")
    reference_strand_id = _required_field(state, "reference_strand_id", state_label)
    if reference_strand_id not in strands:
        raise GroundtruthValidationError(
            f"{state_label} reference_strand_id {reference_strand_id!r} "
            "does not resolve to a strand"
        )

    segment_locations: dict[str, tuple[str, int, dict[str, Any]]] = {}
    for strand in strands_list:
        strand_id = strand["strand_id"]
        if strand.get("orientation") != "5_to_3":
            raise GroundtruthValidationError(
                f"{state_label} strand {strand_id} must be recorded 5_to_3"
            )
        _required_field(strand, "molecule_type", f"{state_label} strand {strand_id}")
        segments = _required_list(
            strand, "segments", f"{state_label} strand {strand_id}"
        )
        for position, segment in enumerate(segments):
            segment_id = _required_field(
                segment,
                "segment_id",
                f"{state_label} strand {strand_id} segment at index {position}",
            )
            _required_field(
                segment,
                "structural_role",
                f"{state_label} strand {strand_id} segment {segment_id}",
            )
            if segment_id in segment_locations:
                raise GroundtruthValidationError(
                    f"{state_label} has duplicate segment ID {segment_id}"
                )
            segment_locations[segment_id] = (strand_id, position, segment)

    paired_regions = _required_list(state, "paired_regions", state_label)
    paired_region_ids: set[str] = set()
    paired_segment_ids: set[str] = set()
    architecture = _required_field(state, "strand_architecture", state_label)
    allowed_architectures = {
        "single_stranded",
        "double_stranded",
        "partially_duplex",
        "rna_dna_hybrid",
        "y_shaped_duplex",
        "mixed_population",
        "unknown",
    }
    if architecture not in allowed_architectures:
        raise GroundtruthValidationError(
            f"{state_label} has unsupported strand_architecture {architecture!r}"
        )
    for region in paired_regions:
        region_id = _required_field(region, "paired_region_id", state_label)
        _add_unique(paired_region_ids, region_id, f"{state_label} paired region")
        side_1_value = _required_field(
            region, "side_1", f"{state_label} paired region {region_id}"
        )
        side_2_value = _required_field(
            region, "side_2", f"{state_label} paired region {region_id}"
        )
        side_1 = _pairing_side_segments(
            side_1_value,
            strands,
            segment_locations,
            f"{state_label} paired region {region_id} side_1",
        )
        side_2 = _pairing_side_segments(
            side_2_value,
            strands,
            segment_locations,
            f"{state_label} paired region {region_id} side_2",
        )
        same_strand = side_1_value["strand_id"] == side_2_value["strand_id"]
        if same_strand and architecture != "partially_duplex":
            raise GroundtruthValidationError(
                f"{state_label} paired region {region_id} may pair one strand "
                "with itself only for partially_duplex architecture"
            )
        side_1_ids = {item["segment_id"] for item in side_1}
        side_2_ids = {item["segment_id"] for item in side_2}
        if side_1_ids & side_2_ids:
            repeated = ", ".join(sorted(side_1_ids & side_2_ids))
            raise GroundtruthValidationError(
                f"{state_label} paired region {region_id} has overlapping sides: "
                f"{repeated}"
            )
        region_segment_ids = side_1_ids | side_2_ids
        if architecture != "mixed_population" and (
            paired_segment_ids & region_segment_ids
        ):
            repeated = ", ".join(sorted(paired_segment_ids & region_segment_ids))
            raise GroundtruthValidationError(
                f"{state_label} pairs segments more than once: {repeated}"
            )
        paired_segment_ids.update(region_segment_ids)
        for segment in side_1 + side_2:
            if segment["structural_role"] not in {
                "paired_region",
                "mixed",
                "unknown",
            }:
                raise GroundtruthValidationError(
                    f"{state_label} paired region {region_id} includes "
                    f"unpaired segment {segment['segment_id']}"
                )
        relationship = _required_field(
            region, "relationship", f"{state_label} paired region {region_id}"
        )
        if relationship == "reverse_complementary":
            sequence_1 = _explicit_segment_sequence(side_1)
            sequence_2 = _explicit_segment_sequence(side_2)
            normalized_1 = _normalized_nucleic_acid(sequence_1)
            normalized_2 = _normalized_nucleic_acid(sequence_2)
            if (
                normalized_1 is not None
                and normalized_2 is not None
                and _reverse_complement(normalized_1) != normalized_2
            ):
                raise GroundtruthValidationError(
                    f"{state_label} paired region {region_id} is not "
                    "reverse-complementary"
                )

    for segment_id, (_, _, segment) in segment_locations.items():
        if (
            segment["structural_role"] == "paired_region"
            and segment_id not in paired_segment_ids
        ):
            raise GroundtruthValidationError(
                f"{state_label} segment {segment_id} is marked paired but is "
                "not present in a paired region"
            )

    discontinuities = _required_list(state, "discontinuities", state_label)
    discontinuity_ids: set[str] = set()
    for discontinuity in discontinuities:
        discontinuity_id = _required_field(
            discontinuity, "discontinuity_id", state_label
        )
        _add_unique(
            discontinuity_ids,
            discontinuity_id,
            f"{state_label} discontinuity",
        )
        discontinuity_label = f"{state_label} discontinuity {discontinuity_id}"
        strand_id = _required_field(discontinuity, "strand_id", discontinuity_label)
        if strand_id not in strands:
            raise GroundtruthValidationError(
                f"{state_label} discontinuity {discontinuity_id} references "
                f"unknown strand {strand_id}"
            )
        after_id = _required_field(
            discontinuity, "after_segment_id", discontinuity_label
        )
        before_id = _required_field(
            discontinuity, "before_segment_id", discontinuity_label
        )
        after = segment_locations.get(after_id)
        before = segment_locations.get(before_id)
        if after is None or before is None:
            raise GroundtruthValidationError(
                f"{state_label} discontinuity {discontinuity_id} references "
                "an unknown segment"
            )
        if after[0] != strand_id or before[0] != strand_id:
            raise GroundtruthValidationError(
                f"{state_label} discontinuity {discontinuity_id} segment "
                "references must belong to its strand"
            )
        if before[1] != after[1] + 1:
            raise GroundtruthValidationError(
                f"{state_label} discontinuity {discontinuity_id} must lie "
                "between adjacent 5_to_3 segments"
            )

    _validate_strand_architecture_class(
        architecture=architecture,
        strands=strands_list,
        paired_regions=paired_regions,
        paired_segment_ids=paired_segment_ids,
        segment_locations=segment_locations,
        label=state_label,
    )


def _pairing_side_segments(
    side: dict[str, Any],
    strands: Mapping[str, dict[str, Any]],
    segment_locations: Mapping[str, tuple[str, int, dict[str, Any]]],
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(side, dict):
        raise GroundtruthValidationError(f"{label} must be an object")
    strand_id = _required_field(side, "strand_id", label)
    if strand_id not in strands:
        raise GroundtruthValidationError(
            f"{label} references unknown strand {strand_id}"
        )
    segment_ids = _required_list(side, "segment_ids", label)
    if not segment_ids:
        raise GroundtruthValidationError(f"{label} must reference at least one segment")
    positions: list[int] = []
    segments: list[dict[str, Any]] = []
    for segment_id in segment_ids:
        location = segment_locations.get(segment_id)
        if location is None or location[0] != strand_id:
            raise GroundtruthValidationError(
                f"{label} references segment {segment_id} outside strand {strand_id}"
            )
        positions.append(location[1])
        segments.append(location[2])
    expected_positions = list(range(positions[0], positions[0] + len(positions)))
    if positions != expected_positions:
        raise GroundtruthValidationError(
            f"{label} segment_ids must be contiguous and ordered 5_to_3"
        )
    return segments


def _validate_strand_architecture_class(
    *,
    architecture: str,
    strands: list[dict[str, Any]],
    paired_regions: list[dict[str, Any]],
    paired_segment_ids: set[str],
    segment_locations: Mapping[str, tuple[str, int, dict[str, Any]]],
    label: str,
) -> None:
    strand_count = len(strands)
    if architecture == "single_stranded":
        if strand_count != 1 or paired_regions:
            raise GroundtruthValidationError(
                f"{label} single_stranded architecture requires one unpaired strand"
            )
        return
    if architecture in {
        "double_stranded",
        "rna_dna_hybrid",
        "y_shaped_duplex",
    }:
        if strand_count < 2 or not paired_regions:
            raise GroundtruthValidationError(
                f"{label} {architecture} architecture requires at least two strands "
                "and at least one paired region"
            )
    if (
        architecture
        in {
            "double_stranded",
            "rna_dna_hybrid",
            "y_shaped_duplex",
        }
        and strand_count != 2
    ):
        raise GroundtruthValidationError(
            f"{label} {architecture} architecture requires exactly two logical strands"
        )
    if architecture == "double_stranded":
        if paired_segment_ids != set(segment_locations):
            raise GroundtruthValidationError(
                f"{label} double_stranded architecture cannot contain "
                "unpaired or overhanging segments"
            )
    elif architecture == "partially_duplex":
        if not paired_regions:
            raise GroundtruthValidationError(
                f"{label} partially_duplex architecture requires at least one "
                "paired region"
            )
        if paired_segment_ids == set(segment_locations):
            raise GroundtruthValidationError(
                f"{label} partially_duplex architecture requires an unpaired region"
            )
    elif architecture == "rna_dna_hybrid":
        molecule_types = {strand["molecule_type"] for strand in strands}
        if molecule_types != {"DNA", "RNA"}:
            raise GroundtruthValidationError(
                f"{label} rna_dna_hybrid architecture requires one DNA and one RNA strand"
            )
    elif architecture == "y_shaped_duplex":
        unpaired_strands = {
            strand_id
            for segment_id, (strand_id, _, _) in segment_locations.items()
            if segment_id not in paired_segment_ids
        }
        if unpaired_strands != {strand["strand_id"] for strand in strands}:
            raise GroundtruthValidationError(
                f"{label} y_shaped_duplex architecture requires an unpaired arm "
                "on both strands"
            )


def _explicit_segment_sequence(segments: list[dict[str, Any]]) -> str | None:
    values = [segment.get("sequence") for segment in segments]
    if not all(isinstance(value, str) for value in values):
        return None
    return "".join(values)


_IUPAC_COMPLEMENT = str.maketrans(
    "ACGTRYSWKMBDHVN",
    "TGCAYRSWMKVHDBN",
)

_ARCHITECTURE_PART_RE = re.compile(
    r"\[[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?::[1-9][0-9]*)?\]"
    r"|[ACGTURYSWKMBDHVN]+"
)

_IUPAC_BASES = {
    "A": frozenset("A"),
    "C": frozenset("C"),
    "G": frozenset("G"),
    "T": frozenset("T"),
    "R": frozenset("AG"),
    "Y": frozenset("CT"),
    "S": frozenset("CG"),
    "W": frozenset("AT"),
    "K": frozenset("GT"),
    "M": frozenset("AC"),
    "B": frozenset("CGT"),
    "D": frozenset("AGT"),
    "H": frozenset("ACT"),
    "V": frozenset("ACG"),
    "N": frozenset("ACGT"),
}


def _validate_ordered_sequence_assemblies(
    task: str, document: Mapping[str, Any]
) -> None:
    if task == "T1":
        for index, library in enumerate(document.get("libraries", [])):
            _validate_ordered_segment_projection(
                architecture=library.get("library_sequence"),
                segments=library.get("segments"),
                label=f"T1 library at index {index} library_sequence",
            )
        return
    if task != "T3":
        return
    for workflow in document.get("workflows", []):
        for state in workflow.get("states", []):
            for strand in state.get("strands", []):
                if "sequence_architecture" not in strand:
                    continue
                _validate_ordered_segment_projection(
                    architecture=strand.get("sequence_architecture"),
                    segments=strand.get("segments"),
                    label=(
                        f"T3 state {state.get('state_id')} strand "
                        f"{strand.get('strand_id')} sequence_architecture"
                    ),
                )


def _validate_ordered_segment_projection(
    *, architecture: Any, segments: Any, label: str
) -> None:
    if not isinstance(architecture, str) or not isinstance(segments, list):
        return
    patterns: list[tuple[str, tuple[str, ...] | int]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            return
        sequence = segment.get("sequence")
        placeholder = segment.get("placeholder")
        length = segment.get("length")
        if isinstance(sequence, str):
            patterns.append(("fixed", tuple(sequence_tokens(sequence))))
        elif isinstance(placeholder, str):
            tokens = tuple(sequence_tokens(placeholder))
            if len(tokens) == 1 and _is_opaque_projection_token(tokens[0]):
                patterns.append(
                    ("length", length)
                    if isinstance(length, int)
                    else ("variable", tokens)
                )
            else:
                patterns.append(("fixed", tokens))
        elif isinstance(length, int):
            patterns.append(("length", length))
        else:
            return

    actual_tokens = tuple(sequence_tokens(architecture))

    @lru_cache(maxsize=None)
    def matches(pattern_index: int, actual_index: int) -> bool:
        if pattern_index == len(patterns):
            # Some promoted records historically leave a terminal overhang out
            # of the segment ledger. That separate completeness defect must not
            # hide an omitted segment declared earlier in the ordered ledger.
            return True
        kind, value = patterns[pattern_index]
        if kind == "fixed":
            expected = value
            assert isinstance(expected, tuple)
            end = actual_index + len(expected)
            return end <= len(actual_tokens) and all(
                _assembly_token_matches(actual, wanted)
                for actual, wanted in zip(
                    actual_tokens[actual_index:end], expected, strict=True
                )
            ) and matches(pattern_index + 1, end)
        if kind == "variable":
            return any(
                matches(pattern_index + 1, end)
                for end in range(actual_index + 1, len(actual_tokens) + 1)
            )
        expected_length = value
        assert isinstance(expected_length, int)
        return any(
            _projection_span_can_have_length(
                actual_tokens[actual_index:end], expected_length
            )
            and matches(pattern_index + 1, end)
            for end in range(
                actual_index + 1,
                min(len(actual_tokens), actual_index + expected_length) + 1,
            )
        )

    if not matches(0, 0):
        raise GroundtruthValidationError(
            f"{label} disagrees with its ordered segment projection"
        )


def _projection_span_can_have_length(tokens: tuple[str, ...], length: int) -> bool:
    if len(tokens) > length:
        return False
    if all(_is_sequence_projection_token(token) for token in tokens):
        return len(tokens) == length or any(
            _is_opaque_projection_token(token) for token in tokens
        )
    return False


def _is_sequence_projection_token(token: str) -> bool:
    return (
        _sequence_token_bases(token) is not None
        or (token.startswith("<") and token.endswith(">"))
        or _is_opaque_projection_token(token)
    )


def _is_opaque_projection_token(token: str) -> bool:
    return token.startswith("[") and token.endswith("]")


def _assembly_token_matches(actual: str, expected: str | None) -> bool:
    if expected is None:
        return True
    if expected.startswith("<") and expected.endswith(">"):
        return actual == expected or _sequence_token_bases(actual) is not None
    if actual == expected:
        return True
    actual_bases = _sequence_token_bases(actual)
    expected_bases = _sequence_token_bases(expected)
    return (
        actual_bases is not None
        and expected_bases is not None
        and bool(actual_bases & expected_bases)
    )


def _sequence_token_bases(token: str) -> frozenset[str] | None:
    if len(token) == 1:
        return _IUPAC_BASES.get(token.upper())
    if len(token) == 2 and token[0] in {"r", "+"}:
        return _IUPAC_BASES.get(token[1].upper().replace("U", "T"))
    if token.lower() == "(du)":
        return _IUPAC_BASES["T"]
    if token.startswith("/") and token.endswith("/"):
        match = re.search(r"([ACGTU])$", token[1:-1], flags=re.IGNORECASE)
        if match is not None:
            return _IUPAC_BASES[match.group(1).upper().replace("U", "T")]
    return None


def _normalized_nucleic_acid(sequence: str | None) -> str | None:
    if not isinstance(sequence, str):
        return None
    normalized = "".join(sequence.split()).upper().replace("U", "T")
    if not normalized or any(base not in "ACGTRYSWKMBDHVN" for base in normalized):
        return None
    return normalized


def _reverse_complement(sequence: str) -> str:
    return sequence.translate(_IUPAC_COMPLEMENT)[::-1]


def _reverse_complement_architecture(sequence: str) -> str | None:
    """Reverse-complement bases while preserving placeholders as opaque units."""

    parts = _ARCHITECTURE_PART_RE.findall(sequence)
    if not parts or "".join(parts) != sequence:
        return None
    reversed_parts = []
    for part in reversed(parts):
        if part.startswith("["):
            reversed_parts.append(part)
        else:
            reversed_parts.append(_reverse_complement(part.replace("U", "T")))
    return "".join(reversed_parts)


def _validate_oligo_derivation_orientation(
    segment: dict[str, Any],
    oligo: dict[str, Any],
    derivation: dict[str, Any],
    label: str,
) -> None:
    """Check an explicit full-length segment/oligo orientation relationship."""

    orientation = derivation.get("orientation_to_source")
    if orientation not in {"same_orientation", "reverse_complement", "unknown"}:
        raise GroundtruthValidationError(
            f"{label} has invalid orientation_to_source {orientation!r}"
        )
    if orientation == "unknown":
        return
    segment_sequence = _normalized_nucleic_acid(segment.get("sequence"))
    source_sequence = _normalized_nucleic_acid(oligo.get("sequence"))
    if (
        segment_sequence is None
        or source_sequence is None
        or len(segment_sequence) != len(source_sequence)
    ):
        return
    expected = (
        source_sequence
        if orientation == "same_orientation"
        else _reverse_complement(source_sequence)
    )
    if not all(
        _IUPAC_BASES[left] & _IUPAC_BASES[right]
        for left, right in zip(segment_sequence, expected, strict=True)
    ):
        raise GroundtruthValidationError(
            f"{label} sequence disagrees with orientation_to_source {orientation!r}"
        )


def _validate_child_scope(
    child: dict[str, Any] | None,
    parent: dict[str, Any] | None,
    label: str,
) -> None:
    if child is None or parent is None:
        return
    if child["protocol_version"] != parent["protocol_version"]:
        raise GroundtruthValidationError(
            f"{label} protocol version disagrees with its parent"
        )
    parent_variants = set(parent["applicable_variants"])
    child_variants = set(child["applicable_variants"])
    if child_variants and not child_variants.issubset(parent_variants):
        raise GroundtruthValidationError(f"{label} variant scope exceeds its parent")


def _effective_variants(
    scope: dict[str, Any], document_scope: dict[str, Any] | None
) -> set[str]:
    variants = set(scope["applicable_variants"])
    if not variants and document_scope is not None:
        return set(document_scope["applicable_variants"])
    return variants


def _require_scope_compatibility(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    label: str,
    document_scope: dict[str, Any] | None,
) -> None:
    if left is None or right is None:
        return
    if left["protocol_version"] != right["protocol_version"]:
        raise GroundtruthValidationError(f"{label} protocol versions disagree")
    left_variants = _effective_variants(left, document_scope)
    right_variants = _effective_variants(right, document_scope)
    if left_variants and right_variants and not left_variants & right_variants:
        raise GroundtruthValidationError(f"{label} variant scopes do not overlap")


def _validate_terminal_libraries(
    *,
    terminal_states: list[tuple[dict[str, Any], dict[str, Any] | None, str]],
    libraries: list[dict[str, Any]],
    document_scope: dict[str, Any] | None,
) -> None:
    """Match terminal T3 states to T1 libraries without stored link IDs."""

    if len(terminal_states) != len(libraries):
        raise GroundtruthValidationError(
            "T3 final-state count must equal the T1 library count"
        )
    candidates: dict[int, list[int]] = {}
    for state_index, (state, workflow_scope, workflow_modality) in enumerate(
        terminal_states
    ):
        state_scope = _resolved_scope(state, workflow_scope)
        mismatch_reasons = [
            _terminal_library_error(state, library) for library in libraries
        ]
        candidates[state_index] = [
            library_index
            for library_index, library in enumerate(libraries)
            if mismatch_reasons[library_index] is None
            and _modality_key(library["modality"]) == _modality_key(workflow_modality)
            and _scopes_are_compatible(
                state_scope,
                _resolved_scope(library, document_scope),
                document_scope,
            )
        ]
        if not candidates[state_index]:
            details = "; ".join(
                f"library {index} ({libraries[index]['modality']}): "
                f"{reason or 'protocol scope or modality differs'}"
                for index, reason in enumerate(mismatch_reasons)
            )
            raise GroundtruthValidationError(
                f"T3 final state {state['state_id']} does not match any T1 library"
                + (f" ({details})" if details else "")
            )

    assignments: list[dict[int, int]] = []

    def search(
        remaining: tuple[int, ...], used: set[int], result: dict[int, int]
    ) -> None:
        if len(assignments) > 1:
            return
        if not remaining:
            assignments.append(dict(result))
            return
        state_index = min(
            remaining,
            key=lambda item: len(
                [candidate for candidate in candidates[item] if candidate not in used]
            ),
        )
        next_remaining = tuple(item for item in remaining if item != state_index)
        for library_index in candidates[state_index]:
            if library_index in used:
                continue
            result[state_index] = library_index
            search(next_remaining, used | {library_index}, result)
            result.pop(state_index, None)

    search(tuple(candidates), set(), {})
    if not assignments:
        raise GroundtruthValidationError(
            "T3 final states cannot be matched one-to-one with T1 libraries"
        )
    if len(assignments) > 1:
        raise GroundtruthValidationError(
            "T3 final-state matching is ambiguous; T1 libraries must have distinct "
            "structures or protocol scopes"
        )


def _terminal_library_error(
    state: dict[str, Any], library: dict[str, Any]
) -> str | None:
    reference_strand_id = state["reference_strand_id"]
    reference_strand = next(
        strand
        for strand in state["strands"]
        if strand["strand_id"] == reference_strand_id
    )
    state_orientation = reference_strand["orientation"]
    library_orientation = library.get("orientation")
    if (
        state_orientation not in {None, "unknown"}
        and library_orientation not in {None, "unknown"}
        and state_orientation != library_orientation
    ):
        return "reference-strand orientation differs"
    architecture = reference_strand.get("sequence_architecture")
    library_architecture = library["library_sequence"]
    reverse_architecture = _reverse_complement_architecture(library_architecture)
    expected = {library_architecture}
    if reverse_architecture is not None:
        expected.add(reverse_architecture)
    if isinstance(architecture, str):
        if architecture not in expected:
            return "sequence architecture differs"
        return None

    # T3 may intentionally use a simpler reference-strand segment model than
    # T1. Exact segment identity is therefore only the fallback when no
    # complete terminal sequence_architecture is supplied.
    state_segments = reference_strand.get("segments")
    library_segments = library.get("segments")
    if state_segments and library_segments:
        left = [
            _segment_signature(item, inherited_orientation=state_orientation)
            for item in state_segments
        ]
        right = [_segment_signature(item) for item in library_segments]
        reverse_right = [
            _reverse_complement_segment_signature(item)
            for item in reversed(library_segments)
        ]
        if left != right and (
            any(item is None for item in reverse_right) or left != reverse_right
        ):
            return "segment representation differs"
        return None
    return "no checkable sequence representation"


def _scopes_are_compatible(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    document_scope: dict[str, Any] | None,
) -> bool:
    if left is None or right is None:
        return True
    if left["protocol_version"] != right["protocol_version"]:
        return False
    left_variants = _effective_variants(left, document_scope)
    right_variants = _effective_variants(right, document_scope)
    return (
        not left_variants or not right_variants or bool(left_variants & right_variants)
    )


def _segment_signature(
    item: dict[str, Any], *, inherited_orientation: str | None = None
) -> tuple[Any, ...]:
    oligo_derivations = tuple(
        sorted(
            (
                derivation["oligo_id"],
                derivation["orientation_to_source"],
            )
            for derivation in item.get("oligo_derivations", [])
        )
    )
    return (
        item["role"],
        item.get("sequence"),
        item.get("length"),
        item.get("placeholder"),
        item.get("orientation", inherited_orientation),
        oligo_derivations,
    )


def _reverse_complement_segment_signature(
    item: dict[str, Any],
) -> tuple[Any, ...] | None:
    sequence = item.get("sequence")
    if isinstance(sequence, str):
        sequence = _reverse_complement_architecture(sequence)
        if sequence is None:
            return None
    oligo_derivations = tuple(
        sorted(
            (
                derivation["oligo_id"],
                {
                    "same_orientation": "reverse_complement",
                    "reverse_complement": "same_orientation",
                    "unknown": "unknown",
                }[derivation["orientation_to_source"]],
            )
            for derivation in item.get("oligo_derivations", [])
        )
    )
    return (
        item["role"],
        sequence,
        item.get("length"),
        item.get("placeholder"),
        item.get("orientation"),
        oligo_derivations,
    )


def _resolved_scope(
    item: Mapping[str, Any], parent: dict[str, Any] | None
) -> dict[str, Any] | None:
    scope = item.get("protocol_scope")
    return scope if isinstance(scope, dict) else parent


def _reachable(initial_ids: set[str], adjacency: Mapping[str, set[str]]) -> set[str]:
    seen = set(initial_ids)
    queue = deque(initial_ids)
    while queue:
        state_id = queue.popleft()
        for product_id in adjacency.get(state_id, set()):
            if product_id not in seen:
                seen.add(product_id)
                queue.append(product_id)
    return seen


def _require_weakly_connected_workflow(
    *,
    state_ids: set[str],
    transitions: list[dict[str, Any]],
    workflow_id: str,
) -> None:
    """Require one weak component in the state-transition incidence graph."""

    undirected: dict[str, set[str]] = defaultdict(set)
    for transition in transitions:
        incident = set(transition["substrate_state_ids"]) | set(
            transition["product_state_ids"]
        )
        if not incident:
            continue
        anchor = next(iter(incident))
        for state_id in incident - {anchor}:
            undirected[anchor].add(state_id)
            undirected[state_id].add(anchor)

    start = next(iter(state_ids))
    connected = _reachable({start}, undirected)
    disconnected = state_ids - connected
    if disconnected:
        raise GroundtruthValidationError(
            f"T3 workflow {workflow_id} must represent one weakly connected "
            "molecular process; disconnected states: " + ", ".join(sorted(disconnected))
        )


def _reject_cycles(
    state_ids: set[str], adjacency: Mapping[str, set[str]], workflow_id: str
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(state_id: str) -> None:
        if state_id in visiting:
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} contains an unintended graph cycle"
            )
        if state_id in visited:
            return
        visiting.add(state_id)
        for product_id in adjacency.get(state_id, set()):
            visit(product_id)
        visiting.remove(state_id)
        visited.add(state_id)

    for state_id in state_ids:
        visit(state_id)


def _require_unique_count(
    indexed: Mapping[str, Any], values: list[dict[str, Any]], label: str
) -> None:
    if len(indexed) != len(values):
        raise GroundtruthValidationError(f"{label} contain duplicate stable IDs")


def _add_unique(values: set[str], value: str, label: str) -> None:
    if value in values:
        raise GroundtruthValidationError(f"{label} ID is duplicated: {value}")
    values.add(value)


def _require_refs(
    references: list[str],
    targets: set[str],
    label: str,
    *,
    target_available: bool,
) -> None:
    if references and not target_available:
        raise GroundtruthValidationError(f"{label} require an available target task")
    missing = sorted(set(references) - targets)
    if missing:
        raise GroundtruthValidationError(
            f"{label} reference unknown IDs: {', '.join(missing)}"
        )
