from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Mapping

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
    try:
        validate_document(
            document,
            schema_dir / TASK_ARTIFACTS[task]["schema"],
            label=f"{task} ground truth",
        )
    except AuditArtifactError as error:
        raise GroundtruthValidationError(str(error)) from error


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
    protocol_ids = {document.get("protocol_id") for document in documents.values()}
    if len(protocol_ids) != 1:
        raise GroundtruthValidationError("T1, T2, and T3 protocol IDs must agree")
    scopes = {
        _scope_key(document["protocol_scope"])
        for document in documents.values()
        if "protocol_scope" in document
    }
    if len(scopes) > 1:
        raise GroundtruthValidationError(
            "T1, T2, and T3 protocol versions and variant scopes must agree"
        )

    t1 = documents.get("T1")
    t2 = documents.get("T2")
    t3 = documents.get("T3")
    document_scope = next(
        (document["protocol_scope"] for document in documents.values()),
        None,
    )

    libraries = {
        item["library_id"]: item for item in (t1.get("libraries", []) if t1 else [])
    }
    _require_unique_count(
        libraries, t1.get("libraries", []) if t1 else [], "T1 libraries"
    )
    oligos = {
        item["oligo_id"]: item for item in (t2.get("oligos", []) if t2 else [])
    }
    _require_unique_count(oligos, t2.get("oligos", []) if t2 else [], "T2 oligos")

    if t1 is not None:
        segment_ids: set[str] = set()
        for library in t1["libraries"]:
            _validate_child_scope(library["protocol_scope"], document_scope, f"T1 library {library['library_id']}")
            for segment in library["segments"]:
                _add_unique(segment_ids, segment["segment_id"], "T1 segment")
                if "protocol_scope" in segment:
                    _validate_child_scope(segment["protocol_scope"], library["protocol_scope"], f"T1 segment {segment['segment_id']}")
                _require_refs(
                    segment.get("oligo_ids", []),
                    set(oligos),
                    f"T1 segment {segment['segment_id']} oligo_ids",
                    target_available=t2 is not None,
                )
                segment_scope = segment.get("protocol_scope", library["protocol_scope"])
                for oligo_id in segment.get("oligo_ids", []):
                    oligo = oligos.get(oligo_id)
                    if oligo is not None:
                        _require_scope_compatibility(
                            segment_scope,
                            oligo["protocol_scope"],
                            f"T1 segment {segment['segment_id']} and T2 oligo {oligo_id}",
                            document_scope,
                        )

    if t2 is not None:
        component_ids: set[str] = set()
        for oligo in t2["oligos"]:
            _validate_child_scope(oligo["protocol_scope"], document_scope, f"T2 oligo {oligo['oligo_id']}")
            for component in oligo["components"]:
                _add_unique(component_ids, component["component_id"], "T2 component")
                if "protocol_scope" in component:
                    _validate_child_scope(component["protocol_scope"], oligo["protocol_scope"], f"T2 component {component['component_id']}")

    if t3 is None:
        return
    workflow_ids: set[str] = set()
    all_state_ids: set[str] = set()
    all_transition_ids: set[str] = set()
    for workflow in t3["workflows"]:
        workflow_id = workflow["workflow_id"]
        _add_unique(workflow_ids, workflow_id, "T3 workflow")
        _validate_child_scope(workflow["protocol_scope"], document_scope, f"T3 workflow {workflow_id}")
        states = {state["state_id"]: state for state in workflow["states"]}
        _require_unique_count(states, workflow["states"], f"T3 workflow {workflow_id} states")
        for state in workflow["states"]:
            _add_unique(all_state_ids, state["state_id"], "T3 state")
            _validate_child_scope(state["protocol_scope"], workflow["protocol_scope"], f"T3 state {state['state_id']}")
            for segment in state.get("segments", []):
                _require_refs(
                    segment.get("oligo_ids", []), set(oligos),
                    f"T3 state {state['state_id']} segment {segment['segment_id']} oligo_ids",
                    target_available=t2 is not None,
                )
                for oligo_id in segment.get("oligo_ids", []):
                    oligo = oligos.get(oligo_id)
                    if oligo is not None:
                        _require_scope_compatibility(
                            state["protocol_scope"],
                            oligo["protocol_scope"],
                            f"T3 state {state['state_id']} and T2 oligo {oligo_id}",
                            document_scope,
                        )

        state_ids = set(states)
        initial_ids = set(workflow["initial_state_ids"])
        final_ids = set(workflow["final_state_ids"])
        _require_refs(workflow["initial_state_ids"], state_ids, f"T3 workflow {workflow_id} initial_state_ids", target_available=True)
        _require_refs(workflow["final_state_ids"], state_ids, f"T3 workflow {workflow_id} final_state_ids", target_available=True)

        links = workflow["final_library_links"]
        linked_state_ids = [link["state_id"] for link in links]
        if len(linked_state_ids) != len(set(linked_state_ids)):
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} links a final state more than once"
            )
        if set(linked_state_ids) != final_ids:
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} must link every and only final states to T1"
            )
        _require_refs(
            [link["library_id"] for link in links], set(libraries),
            f"T3 workflow {workflow_id} final_library_links",
            target_available=t1 is not None,
        )

        adjacency: dict[str, set[str]] = defaultdict(set)
        continuing_adjacency: dict[str, set[str]] = defaultdict(set)
        carried: set[str] = set()
        discarded_products: set[str] = set()
        downstream_substrates: set[str] = set()
        for transition in workflow["transitions"]:
            transition_id = transition["transition_id"]
            _add_unique(all_transition_ids, transition_id, "T3 transition")
            _validate_child_scope(transition["protocol_scope"], workflow["protocol_scope"], f"T3 transition {transition_id}")
            substrates = set(transition["substrate_state_ids"])
            products = set(transition["product_state_ids"])
            continuing = set(transition["carried_forward_product_ids"])
            discarded = set(transition["discarded_product_ids"])
            _require_refs(list(substrates | products), state_ids, f"T3 transition {transition_id} state references", target_available=True)
            _require_refs(transition["oligo_ids"], set(oligos), f"T3 transition {transition_id} oligo_ids", target_available=t2 is not None)
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
        reachable = _reachable(initial_ids, continuing_adjacency)
        unreachable = final_ids - reachable
        if unreachable:
            raise GroundtruthValidationError(
                f"T3 workflow {workflow_id} final states are unreachable: {', '.join(sorted(unreachable))}"
            )

        for link in links:
            state = states[link["state_id"]]
            library = libraries.get(link["library_id"])
            if library is None:
                continue
            _require_scope_compatibility(
                state["protocol_scope"], library["protocol_scope"],
                f"T3 state {state['state_id']} and T1 library {library['library_id']}",
                document_scope,
            )
            _validate_terminal_library(state, library)

        for transition in workflow["transitions"]:
            for oligo_id in transition["oligo_ids"]:
                oligo = oligos.get(oligo_id)
                if oligo is not None:
                    _require_scope_compatibility(
                        transition["protocol_scope"], oligo["protocol_scope"],
                        f"T3 transition {transition['transition_id']} and T2 oligo {oligo_id}",
                        document_scope,
                    )


def _scope_key(scope: dict[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    return scope["protocol_version"], tuple(sorted(scope["applicable_variants"]))


def _validate_child_scope(
    child: dict[str, Any], parent: dict[str, Any] | None, label: str
) -> None:
    if parent is None:
        return
    if child["protocol_version"] != parent["protocol_version"]:
        raise GroundtruthValidationError(f"{label} protocol version disagrees with its parent")
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
    left: dict[str, Any],
    right: dict[str, Any],
    label: str,
    document_scope: dict[str, Any] | None,
) -> None:
    if left["protocol_version"] != right["protocol_version"]:
        raise GroundtruthValidationError(f"{label} protocol versions disagree")
    left_variants = _effective_variants(left, document_scope)
    right_variants = _effective_variants(right, document_scope)
    if left_variants and right_variants and not left_variants & right_variants:
        raise GroundtruthValidationError(f"{label} variant scopes do not overlap")


def _validate_terminal_library(
    state: dict[str, Any], library: dict[str, Any]
) -> None:
    state_segments = state.get("segments")
    library_segments = library.get("segments")
    if state_segments and library_segments:
        left = [_segment_signature(item) for item in state_segments]
        right = [_segment_signature(item) for item in library_segments]
        if left != right:
            raise GroundtruthValidationError(
                f"terminal T3 state {state['state_id']} is inconsistent with T1 library {library['library_id']} segments"
            )
        return
    architecture = state.get("sequence_architecture")
    expected = {
        value
        for value in (
            library.get("library_sequence"),
            library.get("annotated_library_sequence"),
        )
        if isinstance(value, str)
    }
    if architecture not in expected:
        raise GroundtruthValidationError(
            f"terminal T3 state {state['state_id']} is inconsistent with T1 library {library['library_id']} sequence"
        )


def _segment_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["role"],
        item.get("sequence"),
        item.get("length"),
        item.get("placeholder"),
    )


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
