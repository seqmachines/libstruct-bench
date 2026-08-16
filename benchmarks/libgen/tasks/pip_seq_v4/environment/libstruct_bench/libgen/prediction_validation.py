from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


class LibgenPredictionValidationError(ValueError):
    """Raised when an agent-visible linked T2/T3 prediction is invalid."""


def default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas"


def validate_t2_prediction(
    document: Any,
    *,
    protocol_id: str,
    schema_root: Path | None = None,
) -> None:
    root = schema_root or default_schema_root()
    _validate_schema(
        document,
        root / "benchmark" / "oligo_prediction.schema.json",
        "T2 prediction",
    )
    _require_protocol(document, protocol_id, "T2 prediction")
    _unique_ids(document["oligos"], "oligo_id", "T2 oligos")


def validate_t3_prediction(
    document: Any,
    *,
    protocol_id: str,
    schema_root: Path | None = None,
) -> None:
    root = schema_root or default_schema_root()
    _validate_schema(
        document,
        root / "benchmark" / "library_generation_workflow_prediction.schema.json",
        "T3 prediction",
    )
    _require_protocol(document, protocol_id, "T3 prediction")


def validate_prediction_links(
    t2_document: Mapping[str, Any],
    t3_document: Mapping[str, Any],
) -> None:
    """Check generic linked-prediction integrity without benchmark answers."""

    if t2_document.get("protocol_id") != t3_document.get("protocol_id"):
        raise LibgenPredictionValidationError("linked T2/T3 protocol IDs must agree")
    oligo_ids = _unique_ids(t2_document["oligos"], "oligo_id", "T2 oligos")
    workflow_ids: set[str] = set()
    for workflow in t3_document["workflows"]:
        workflow_id = workflow["workflow_id"]
        _add_unique(workflow_ids, workflow_id, "T3 workflow")
        states = {state["state_id"]: state for state in workflow["states"]}
        if len(states) != len(workflow["states"]):
            raise LibgenPredictionValidationError(
                f"T3 workflow {workflow_id} states contain duplicate IDs"
            )
        state_ids = set(states)
        for state in workflow["states"]:
            _validate_molecular_state(state, label=f"T3 state {state['state_id']}")
            for strand in state["strands"]:
                for segment in strand["segments"]:
                    derivations = segment.get("oligo_derivations", [])
                    references = [item["oligo_id"] for item in derivations]
                    _require_refs(
                        references,
                        oligo_ids,
                        f"T3 state {state['state_id']} strand {strand['strand_id']} "
                        f"segment {segment['segment_id']} oligo derivations",
                    )
                    if len(references) != len(set(references)):
                        raise LibgenPredictionValidationError(
                            f"T3 state {state['state_id']} segment "
                            f"{segment['segment_id']} repeats a T2 oligo derivation"
                        )

        initial_ids = set(workflow["initial_state_ids"])
        final_state_ids = [item["state_id"] for item in workflow["final_outputs"]]
        if len(final_state_ids) != len(set(final_state_ids)):
            raise LibgenPredictionValidationError(
                f"T3 workflow {workflow_id} lists a terminal state more than once"
            )
        final_ids = set(final_state_ids)
        _require_refs(
            workflow["initial_state_ids"],
            state_ids,
            f"T3 workflow {workflow_id} initial_state_ids",
        )
        _require_refs(
            final_state_ids,
            state_ids,
            f"T3 workflow {workflow_id} final_outputs",
        )

        adjacency: dict[str, set[str]] = defaultdict(set)
        continuing_adjacency: dict[str, set[str]] = defaultdict(set)
        carried: set[str] = set()
        discarded: set[str] = set()
        substrates: set[str] = set()
        transition_ids: set[str] = set()
        for transition in workflow["transitions"]:
            transition_id = transition["transition_id"]
            _add_unique(
                transition_ids,
                transition_id,
                f"T3 workflow {workflow_id} transition",
            )
            transition_substrates = set(transition["substrate_state_ids"])
            products = set(transition["product_state_ids"])
            continuing = set(transition["carried_forward_product_ids"])
            transition_discarded = set(transition["discarded_product_ids"])
            _require_refs(
                list(transition_substrates | products),
                state_ids,
                f"T3 transition {transition_id} state references",
            )
            _require_refs(
                transition["oligo_ids"],
                oligo_ids,
                f"T3 transition {transition_id} oligo_ids",
            )
            if not continuing.issubset(products) or not transition_discarded.issubset(
                products
            ):
                raise LibgenPredictionValidationError(
                    f"T3 transition {transition_id} carried/discarded products "
                    "must be product states"
                )
            if continuing & transition_discarded:
                raise LibgenPredictionValidationError(
                    f"T3 transition {transition_id} cannot both carry and discard "
                    "a product"
                )
            if continuing | transition_discarded != products:
                raise LibgenPredictionValidationError(
                    f"T3 transition {transition_id} must classify every product"
                )
            carried.update(continuing)
            discarded.update(transition_discarded)
            substrates.update(transition_substrates)
            for substrate in transition_substrates:
                adjacency[substrate].update(products)
                continuing_adjacency[substrate].update(continuing)

        reused_discarded = discarded & substrates
        if reused_discarded:
            raise LibgenPredictionValidationError(
                f"T3 workflow {workflow_id} uses discarded products downstream: "
                + ", ".join(sorted(reused_discarded))
            )
        unproduced_substrates = substrates - initial_ids - carried
        if unproduced_substrates:
            raise LibgenPredictionValidationError(
                f"T3 workflow {workflow_id} has substrates not carried forward: "
                + ", ".join(sorted(unproduced_substrates))
            )
        unused_carried = carried - final_ids - substrates
        if unused_carried:
            raise LibgenPredictionValidationError(
                f"T3 workflow {workflow_id} has unused carried products: "
                + ", ".join(sorted(unused_carried))
            )
        _reject_cycles(state_ids, adjacency, workflow_id)
        _require_weakly_connected(state_ids, workflow["transitions"], workflow_id)
        unreachable = final_ids - _reachable(initial_ids, continuing_adjacency)
        if unreachable:
            raise LibgenPredictionValidationError(
                f"T3 workflow {workflow_id} final states are unreachable: "
                + ", ".join(sorted(unreachable))
            )


def _validate_schema(document: Any, path: Path, label: str) -> None:
    if not path.exists():
        raise LibgenPredictionValidationError(f"missing {label} schema: {path}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return
    rendered = []
    for error in errors[:12]:
        location = "/" + "/".join(str(item) for item in error.absolute_path)
        rendered.append(f"{location or '/'}: {error.message}")
    suffix = f"; {len(errors) - 12} more" if len(errors) > 12 else ""
    raise LibgenPredictionValidationError(
        f"{label} schema errors: {'; '.join(rendered)}{suffix}"
    )


def _require_protocol(document: Any, protocol_id: str, label: str) -> None:
    if not isinstance(document, dict) or document.get("protocol_id") != protocol_id:
        actual = document.get("protocol_id") if isinstance(document, dict) else None
        raise LibgenPredictionValidationError(
            f"{label} protocol_id {actual!r} does not match {protocol_id!r}"
        )


def _validate_molecular_state(state: Mapping[str, Any], *, label: str) -> None:
    strands = {item["strand_id"]: item for item in state["strands"]}
    if len(strands) != len(state["strands"]):
        raise LibgenPredictionValidationError(f"{label} has duplicate strand IDs")
    if state["reference_strand_id"] not in strands:
        raise LibgenPredictionValidationError(
            f"{label} reference_strand_id {state['reference_strand_id']!r} "
            "does not resolve"
        )
    segments: dict[str, tuple[str, int, Mapping[str, Any]]] = {}
    for strand in state["strands"]:
        for position, segment in enumerate(strand["segments"]):
            segment_id = segment["segment_id"]
            if segment_id in segments:
                raise LibgenPredictionValidationError(
                    f"{label} has duplicate segment ID {segment_id}"
                )
            segments[segment_id] = (strand["strand_id"], position, segment)

    paired_ids: set[str] = set()
    paired_segments: set[str] = set()
    for region in state["paired_regions"]:
        _add_unique(paired_ids, region["paired_region_id"], f"{label} paired region")
        sides = []
        for side_name in ("side_1", "side_2"):
            side = region[side_name]
            strand_id = side["strand_id"]
            if strand_id not in strands:
                raise LibgenPredictionValidationError(
                    f"{label} paired region references unknown strand {strand_id}"
                )
            locations = []
            for segment_id in side["segment_ids"]:
                location = segments.get(segment_id)
                if location is None or location[0] != strand_id:
                    raise LibgenPredictionValidationError(
                        f"{label} paired region references unknown segment {segment_id}"
                    )
                locations.append(location)
            positions = [item[1] for item in locations]
            if positions != list(range(positions[0], positions[0] + len(positions))):
                raise LibgenPredictionValidationError(
                    f"{label} paired-region segments must be contiguous and ordered"
                )
            sides.append([item[2] for item in locations])
        side_ids = [{item["segment_id"] for item in side} for side in sides]
        if side_ids[0] & side_ids[1]:
            raise LibgenPredictionValidationError(
                f"{label} paired-region sides overlap"
            )
        if state["strand_architecture"] != "mixed_population" and paired_segments & (
            side_ids[0] | side_ids[1]
        ):
            raise LibgenPredictionValidationError(
                f"{label} pairs a segment more than once"
            )
        paired_segments.update(side_ids[0] | side_ids[1])
        if region["relationship"] == "reverse_complementary":
            left = _explicit_sequence(sides[0])
            right = _explicit_sequence(sides[1])
            if (
                left is not None
                and right is not None
                and _reverse_complement(left) != right
            ):
                raise LibgenPredictionValidationError(
                    f"{label} paired region is not reverse-complementary"
                )

    for discontinuity in state["discontinuities"]:
        strand_id = discontinuity["strand_id"]
        after = segments.get(discontinuity["after_segment_id"])
        before = segments.get(discontinuity["before_segment_id"])
        if after is None or before is None:
            raise LibgenPredictionValidationError(
                f"{label} discontinuity references an unknown segment"
            )
        if after[0] != strand_id or before[0] != strand_id:
            raise LibgenPredictionValidationError(
                f"{label} discontinuity segments must belong to its strand"
            )
        if before[1] != after[1] + 1:
            raise LibgenPredictionValidationError(
                f"{label} discontinuity must lie between adjacent segments"
            )

    architecture = state["strand_architecture"]
    strand_count = len(strands)
    if architecture == "single_stranded" and (
        strand_count != 1 or state["paired_regions"]
    ):
        raise LibgenPredictionValidationError(
            f"{label} single_stranded architecture requires one unpaired strand"
        )
    if architecture in {
        "double_stranded",
        "rna_dna_hybrid",
        "y_shaped_duplex",
    } and (strand_count != 2 or not state["paired_regions"]):
        raise LibgenPredictionValidationError(
            f"{label} {architecture} architecture requires two paired strands"
        )
    if architecture == "double_stranded" and paired_segments != set(segments):
        raise LibgenPredictionValidationError(
            f"{label} double_stranded architecture cannot contain unpaired segments"
        )
    if architecture == "partially_duplex" and (
        not state["paired_regions"] or paired_segments == set(segments)
    ):
        raise LibgenPredictionValidationError(
            f"{label} partially_duplex architecture requires paired and unpaired regions"
        )
    if architecture == "rna_dna_hybrid" and {
        item["molecule_type"] for item in state["strands"]
    } != {"DNA", "RNA"}:
        raise LibgenPredictionValidationError(
            f"{label} rna_dna_hybrid requires one DNA and one RNA strand"
        )


def _explicit_sequence(segments: list[Mapping[str, Any]]) -> str | None:
    values = [item.get("sequence") for item in segments]
    if not all(isinstance(item, str) for item in values):
        return None
    normalized = "".join("".join(values).split()).upper().replace("U", "T")
    if not normalized or any(item not in "ACGTRYSWKMBDHVN" for item in normalized):
        return None
    return normalized


def _reverse_complement(sequence: str) -> str:
    return sequence.translate(str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN"))[::-1]


def _unique_ids(items: list[Mapping[str, Any]], field: str, label: str) -> set[str]:
    values = {item[field] for item in items}
    if len(values) != len(items):
        raise LibgenPredictionValidationError(f"{label} contain duplicate IDs")
    return values


def _add_unique(values: set[str], value: str, label: str) -> None:
    if value in values:
        raise LibgenPredictionValidationError(f"{label} ID is duplicated: {value}")
    values.add(value)


def _require_refs(references: list[str], targets: set[str], label: str) -> None:
    missing = sorted(set(references) - targets)
    if missing:
        raise LibgenPredictionValidationError(
            f"{label} reference unknown IDs: {', '.join(missing)}"
        )


def _reachable(initial_ids: set[str], adjacency: Mapping[str, set[str]]) -> set[str]:
    seen = set(initial_ids)
    queue = deque(initial_ids)
    while queue:
        current = queue.popleft()
        for destination in adjacency.get(current, set()):
            if destination not in seen:
                seen.add(destination)
                queue.append(destination)
    return seen


def _reject_cycles(
    state_ids: set[str], adjacency: Mapping[str, set[str]], workflow_id: str
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(state_id: str) -> None:
        if state_id in visiting:
            raise LibgenPredictionValidationError(
                f"T3 workflow {workflow_id} contains an unintended graph cycle"
            )
        if state_id in visited:
            return
        visiting.add(state_id)
        for destination in adjacency.get(state_id, set()):
            visit(destination)
        visiting.remove(state_id)
        visited.add(state_id)

    for state_id in state_ids:
        visit(state_id)


def _require_weakly_connected(
    state_ids: set[str],
    transitions: list[Mapping[str, Any]],
    workflow_id: str,
) -> None:
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
    disconnected = state_ids - _reachable({next(iter(state_ids))}, undirected)
    if disconnected:
        raise LibgenPredictionValidationError(
            f"T3 workflow {workflow_id} must be one connected process; "
            "disconnected states: " + ", ".join(sorted(disconnected))
        )
