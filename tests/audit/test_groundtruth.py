from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.groundtruth import (
    GroundtruthValidationError,
    validate_cross_task_links,
    validate_molecular_state_architecture,
    validate_task_document,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = REPO_ROOT / "schemas" / "groundtruth"


def _scope(version: str = "paper") -> dict:
    return {"protocol_version": version, "applicable_variants": ["default"]}


def _documents() -> dict[str, dict]:
    t1 = {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "libraries": [
            {
                "modality": "rna",
                "protocol_scope": _scope(),
                "final_molecule": "DNA",
                "library_sequence": "AAA",
                "strand": "single",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "library-adapter",
                        "kind": "constant",
                        "role": "adapter",
                        "sequence": "AAA",
                        "orientation": "5_to_3",
                        "oligo_derivations": [
                            {
                                "oligo_id": "adapter",
                                "orientation_to_source": "same_orientation",
                            }
                        ],
                        "support_status": "explicit",
                    }
                ],
                "support_status": "explicit",
            }
        ],
    }
    t2 = {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "oligos": [
            {
                "oligo_id": "adapter",
                "canonical_oligo_id": "common:adapter",
                "family_id": "adapter-family",
                "name": "Adapter",
                "aliases": ["Assay adapter"],
                "role": "adapter",
                "kind": "single",
                "sequence": "AAA",
                "orientation": "5_to_3",
                "components": [],
                "modifications": [],
                "protocol_scope": _scope(),
                "support_status": "explicit",
            }
        ],
    }
    t3 = {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "modality": "rna",
        "protocol_scope": _scope(),
        "workflows": [
            {
                "workflow_id": "workflow",
                "protocol_scope": _scope(),
                "states": [
                    {
                        "state_id": "input",
                        "name": "Input",
                        "molecule_type": "RNA",
                        "strand_architecture": "single_stranded",
                        "reference_strand_id": "input-strand",
                        "physical_state": "solution",
                        "strands": [
                            {
                                "strand_id": "input-strand",
                                "name": "Input RNA",
                                "molecule_type": "RNA",
                                "orientation": "5_to_3",
                                "segments": [
                                    {
                                        "segment_id": "input-rna",
                                        "role": "input",
                                        "structural_role": "unpaired",
                                        "sequence": "GGG",
                                    }
                                ],
                                "support_status": "explicit",
                            }
                        ],
                        "paired_regions": [],
                        "discontinuities": [],
                        "properties": [],
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                    },
                    {
                        "state_id": "final",
                        "name": "Final library",
                        "molecule_type": "DNA",
                        "strand_architecture": "single_stranded",
                        "reference_strand_id": "final-strand",
                        "physical_state": "solution",
                        "strands": [
                            {
                                "strand_id": "final-strand",
                                "name": "Canonical final-library strand",
                                "molecule_type": "DNA",
                                "orientation": "5_to_3",
                                "segments": [
                                    {
                                        "segment_id": "final-adapter",
                                        "role": "adapter",
                                        "structural_role": "unpaired",
                                        "sequence": "AAA",
                                        "oligo_derivations": [
                                            {
                                                "oligo_id": "adapter",
                                                "orientation_to_source": "same_orientation",
                                            }
                                        ],
                                    }
                                ],
                                "support_status": "explicit",
                            }
                        ],
                        "paired_regions": [],
                        "discontinuities": [],
                        "properties": ["amplifiable"],
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                    },
                ],
                "transitions": [
                    {
                        "transition_id": "ligation",
                        "substrate_state_ids": ["input"],
                        "operation": "ligation",
                        "operation_detail": None,
                        "oligo_ids": ["adapter"],
                        "major_reagents": [{"name": "ligase", "role": "enzyme"}],
                        "product_state_ids": ["final"],
                        "carried_forward_product_ids": ["final"],
                        "discarded_product_ids": [],
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                    }
                ],
                "initial_state_ids": ["input"],
                "final_state_ids": ["final"],
            }
        ],
    }
    return {"T1": t1, "T2": t2, "T3": t3}


def _paired_state(
    *,
    architecture: str = "double_stranded",
    top_sequence: str = "AACG",
    bottom_sequence: str = "CGTT",
    top_overhang: str | None = None,
    bottom_overhang: str | None = None,
    relationship: str = "reverse_complementary",
    top_molecule: str = "DNA",
    bottom_molecule: str = "DNA",
) -> dict:
    def strand(
        strand_id: str,
        molecule_type: str,
        paired_sequence: str,
        overhang: str | None,
    ) -> dict:
        segments = [
            {
                "segment_id": f"{strand_id}-paired",
                "role": "duplex",
                "structural_role": "paired_region",
                "sequence": paired_sequence,
            }
        ]
        if overhang is not None:
            segments.append(
                {
                    "segment_id": f"{strand_id}-overhang",
                    "role": "adapter_arm",
                    "structural_role": "three_prime_overhang",
                    "sequence": overhang,
                }
            )
        return {
            "strand_id": strand_id,
            "name": strand_id,
            "molecule_type": molecule_type,
            "orientation": "5_to_3",
            "segments": segments,
            "support_status": "explicit",
        }

    return {
        "state_id": "duplex",
        "name": "Duplex",
        "molecule_type": "nucleic acid",
        "strand_architecture": architecture,
        "reference_strand_id": "top",
        "physical_state": "solution",
        "strands": [
            strand("top", top_molecule, top_sequence, top_overhang),
            strand("bottom", bottom_molecule, bottom_sequence, bottom_overhang),
        ],
        "paired_regions": [
            {
                "paired_region_id": "duplex-region",
                "side_1": {"strand_id": "top", "segment_ids": ["top-paired"]},
                "side_2": {
                    "strand_id": "bottom",
                    "segment_ids": ["bottom-paired"],
                },
                "relationship": relationship,
                "support_status": "explicit",
            }
        ],
        "discontinuities": [],
        "properties": [],
        "support_status": "explicit",
    }


def test_canonical_documents_validate_and_link() -> None:
    documents = _documents()
    for task, document in documents.items():
        validate_task_document(
            task,
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )
    validate_cross_task_links(documents)


def test_protocol_scope_is_optional_and_inherited() -> None:
    documents = _documents()
    for document in documents.values():
        document.pop("protocol_scope", None)
    for library in documents["T1"]["libraries"]:
        library.pop("protocol_scope", None)
    for oligo in documents["T2"]["oligos"]:
        oligo.pop("protocol_scope", None)
    for workflow in documents["T3"]["workflows"]:
        workflow.pop("protocol_scope", None)
        for state in workflow["states"]:
            state.pop("protocol_scope", None)
        for transition in workflow["transitions"]:
            transition.pop("protocol_scope", None)

    for task, document in documents.items():
        validate_task_document(
            task,
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )
    validate_cross_task_links(documents)


@pytest.mark.parametrize(
    ("task", "path", "field", "value"),
    [
        ("T1", ("libraries", 0), "evidence", []),
        ("T1", ("libraries", 0), "ground_truth_status", "included"),
        ("T1", ("libraries", 0), "library_id", "library"),
        ("T1", ("libraries", 0), "strands", []),
        ("T1", ("libraries", 0), "annotated_library_sequence", "AAA"),
        ("T2", (), "limitations", []),
        ("T2", ("oligos", 0), "baseline_lineage", []),
        ("T2", ("oligos", 0), "evidence", []),
        ("T2", ("oligos", 0), "ground_truth_status", "included"),
        ("T2", ("oligos", 0), "notes", "audit-only"),
        ("T3", (), "limitations", []),
        ("T3", ("workflows", 0), "ground_truth_status", "included"),
        ("T3", ("workflows", 0), "notes", "audit-only"),
        ("T3", ("workflows", 0), "evidence", []),
        ("T3", ("workflows", 0), "workflow_branch", "main"),
        ("T3", ("workflows", 0), "modality", "rna"),
        ("T3", ("workflows", 0, "states", 0), "modality", "rna"),
    ],
)
def test_removed_groundtruth_fields_are_rejected(
    task: str,
    path: tuple[str | int, ...],
    field: str,
    value: object,
) -> None:
    document = _documents()[task]
    target: object = document
    for key in path:
        target = target[key]  # type: ignore[index]
    assert isinstance(target, dict)
    target[field] = value

    with pytest.raises(GroundtruthValidationError, match=field):
        validate_task_document(
            task,
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )


def test_t3_modality_is_required_only_at_protocol_root() -> None:
    document = _documents()["T3"]
    del document["modality"]

    with pytest.raises(GroundtruthValidationError, match="modality"):
        validate_task_document(
            "T3",
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )


def test_legacy_shape_reports_missing_field_instead_of_keyerror() -> None:
    documents = _documents()
    del documents["T1"]["libraries"][0]["segments"]

    with pytest.raises(
        GroundtruthValidationError,
        match="T1 library at index 0 is missing required field 'segments'",
    ):
        validate_cross_task_links(documents)


def test_t2_rejects_redundant_source_name_fields() -> None:
    document = _documents()["T2"]
    document["oligos"][0]["source_name"] = "Assay adapter"

    with pytest.raises(GroundtruthValidationError, match="source_name"):
        validate_task_document(
            "T2",
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )


def test_t2_components_are_ordered_inline_without_ids() -> None:
    document = _documents()["T2"]
    component = {
        "name": "Adapter segment",
        "role": "adapter",
        "sequence": "AAA",
        "orientation": "5_to_3",
        "modifications": [],
        "support_status": "explicit",
    }
    document["oligos"][0]["components"] = [component]
    validate_task_document(
        "T2",
        document,
        protocol_id="example_protocol",
        schema_dir=SCHEMAS,
    )

    component["component_id"] = "removed-component-id"
    with pytest.raises(GroundtruthValidationError, match="component_id"):
        validate_task_document(
            "T2",
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )


def test_t1_single_sequence_retains_biological_insert_and_links_to_t3() -> None:
    documents = _documents()
    library = documents["T1"]["libraries"][0]
    library["library_sequence"] = "AAA[CDNA]"
    final_strand = documents["T3"]["workflows"][0]["states"][1]["strands"][0]
    final_strand["sequence_architecture"] = "AAA[CDNA]"

    validate_task_document(
        "T1",
        documents["T1"],
        protocol_id="example_protocol",
        schema_dir=SCHEMAS,
    )
    validate_cross_task_links(documents)


def test_t3_requires_explicit_five_to_three_strands() -> None:
    document = _documents()["T3"]
    strand = document["workflows"][0]["states"][0]["strands"][0]
    del strand["orientation"]

    with pytest.raises(GroundtruthValidationError, match="orientation"):
        validate_task_document(
            "T3",
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )

    document = _documents()["T3"]
    document["workflows"][0]["states"][0]["strands"][0][
        "orientation"
    ] = "3_to_5"

    with pytest.raises(GroundtruthValidationError, match="5_to_3"):
        validate_task_document(
            "T3",
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )


def test_double_stranded_regions_must_be_reverse_complementary() -> None:
    validate_molecular_state_architecture(_paired_state())

    state = _paired_state(bottom_sequence="AAAA")
    with pytest.raises(GroundtruthValidationError, match="reverse-complementary"):
        validate_molecular_state_architecture(state)


def test_partially_duplex_state_allows_an_unpaired_overhang() -> None:
    state = _paired_state(
        architecture="partially_duplex",
        top_overhang="TT",
    )

    validate_molecular_state_architecture(state)


def test_rna_dna_hybrid_pairing_treats_uracil_as_thymine() -> None:
    state = _paired_state(
        architecture="rna_dna_hybrid",
        bottom_sequence="CGUU",
        bottom_molecule="RNA",
    )

    validate_molecular_state_architecture(state)


def test_documented_noncanonical_pairing_allows_a_supported_mismatch() -> None:
    state = _paired_state(
        bottom_sequence="AAAA",
        relationship="documented_noncanonical",
    )

    validate_molecular_state_architecture(state)


def test_nick_is_preserved_between_adjacent_strand_segments() -> None:
    state = _paired_state()
    top = state["strands"][0]
    top["segments"] = [
        {
            "segment_id": "top-paired-1",
            "role": "duplex",
            "structural_role": "paired_region",
            "sequence": "AA",
        },
        {
            "segment_id": "top-paired-2",
            "role": "duplex",
            "structural_role": "paired_region",
            "sequence": "CG",
        },
    ]
    state["paired_regions"][0]["side_1"]["segment_ids"] = [
        "top-paired-1",
        "top-paired-2",
    ]
    state["discontinuities"] = [
        {
            "discontinuity_id": "top-nick",
            "strand_id": "top",
            "after_segment_id": "top-paired-1",
            "before_segment_id": "top-paired-2",
            "kind": "nick",
            "support_status": "explicit",
        }
    ]

    validate_molecular_state_architecture(state)


def test_t3_oligo_derivation_orientation_is_controlled() -> None:
    document = _documents()["T3"]
    derivation = document["workflows"][0]["states"][1]["strands"][0][
        "segments"
    ][0]["oligo_derivations"][0]
    derivation["orientation_to_source"] = "forward"

    with pytest.raises(GroundtruthValidationError, match="orientation_to_source"):
        validate_task_document(
            "T3",
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )


def test_t3_oligo_reference_must_resolve() -> None:
    documents = _documents()
    documents["T3"]["workflows"][0]["transitions"][0]["oligo_ids"] = ["missing"]
    with pytest.raises(GroundtruthValidationError, match="unknown IDs"):
        validate_cross_task_links(documents)


def test_t3_segment_oligo_derivation_must_resolve() -> None:
    documents = _documents()
    derivation = documents["T3"]["workflows"][0]["states"][1]["strands"][0][
        "segments"
    ][0]["oligo_derivations"][0]
    derivation["oligo_id"] = "missing"

    with pytest.raises(GroundtruthValidationError, match="oligo derivations.*unknown IDs"):
        validate_cross_task_links(documents)


def test_t1_segment_oligo_derivation_must_resolve() -> None:
    documents = _documents()
    derivation = documents["T1"]["libraries"][0]["segments"][0][
        "oligo_derivations"
    ][0]
    derivation["oligo_id"] = "missing"

    with pytest.raises(GroundtruthValidationError, match="oligo derivations.*unknown IDs"):
        validate_cross_task_links(documents)


def test_t1_segment_orientation_to_source_matches_exact_sequences() -> None:
    documents = _documents()
    segment = documents["T1"]["libraries"][0]["segments"][0]
    oligo = documents["T2"]["oligos"][0]
    segment["sequence"] = "CGCGGTTC"
    segment["length"] = 8
    segment["placeholder"] = "[TN5_INDEX:8]"
    segment["oligo_derivations"][0][
        "orientation_to_source"
    ] = "reverse_complement"
    oligo["sequence"] = "GAACCGCG"

    validate_cross_task_links({"T1": documents["T1"], "T2": documents["T2"]})

    segment["oligo_derivations"][0][
        "orientation_to_source"
    ] = "same_orientation"
    with pytest.raises(
        GroundtruthValidationError,
        match="sequence disagrees with orientation_to_source",
    ):
        validate_cross_task_links({"T1": documents["T1"], "T2": documents["T2"]})


@pytest.mark.parametrize("task", ["T1", "T2", "T3"])
def test_placeholder_roles_cannot_encode_orientation(task: str) -> None:
    documents = _documents()
    if task == "T1":
        segment = documents["T1"]["libraries"][0]["segments"][0]
        segment["placeholder"] = "[TN5_INDEX_RC:8]"
    elif task == "T2":
        component = {
            "name": "Tn5 index",
            "role": "tn5_index",
            "placeholder": "[TN5_INDEX_RC:8]",
            "orientation": "5_to_3",
            "modifications": [],
            "support_status": "explicit",
        }
        documents["T2"]["oligos"][0]["components"] = [component]
    else:
        segment = documents["T3"]["workflows"][0]["states"][1]["strands"][0][
            "segments"
        ][0]
        segment["placeholder"] = "[TN5_INDEX_RC:8]"

    with pytest.raises(GroundtruthValidationError, match="placeholder"):
        validate_task_document(
            task,
            documents[task],
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )


@pytest.mark.parametrize("task", ["T1", "T2", "T3"])
def test_canonical_placeholder_roles_are_valid(task: str) -> None:
    documents = _documents()
    if task == "T1":
        documents["T1"]["libraries"][0]["segments"][0]["placeholder"] = (
            "[TN5_INDEX:8]"
        )
    elif task == "T2":
        component = {
            "name": "Tn5 index",
            "role": "tn5_index",
            "placeholder": "[TN5_INDEX:8]",
            "orientation": "5_to_3",
            "modifications": [],
            "support_status": "explicit",
        }
        documents["T2"]["oligos"][0]["components"] = [component]
    else:
        documents["T3"]["workflows"][0]["states"][1]["strands"][0][
            "segments"
        ][0]["placeholder"] = "[TN5_INDEX:8]"

    validate_task_document(
        task,
        documents[task],
        protocol_id="example_protocol",
        schema_dir=SCHEMAS,
    )


def test_nonfinal_carried_product_must_continue() -> None:
    documents = _documents()
    workflow = documents["T3"]["workflows"][0]
    unused = copy.deepcopy(workflow["states"][0])
    unused["state_id"] = "unused"
    workflow["states"].append(unused)
    transition = workflow["transitions"][0]
    transition["product_state_ids"].append("unused")
    transition["carried_forward_product_ids"].append("unused")
    with pytest.raises(GroundtruthValidationError, match="not downstream substrates"):
        validate_cross_task_links(documents)


def test_final_state_must_be_reachable() -> None:
    documents = _documents()
    documents["T3"]["workflows"][0]["transitions"] = []
    with pytest.raises(GroundtruthValidationError, match="unreachable"):
        validate_cross_task_links(documents)


def test_graph_cycles_are_rejected() -> None:
    documents = _documents()
    workflow = documents["T3"]["workflows"][0]
    workflow["transitions"].append(
        {
            "transition_id": "cycle",
            "substrate_state_ids": ["final"],
            "operation": "other",
            "operation_detail": "invalid cycle",
            "oligo_ids": [],
            "major_reagents": [],
            "product_state_ids": ["input"],
            "carried_forward_product_ids": ["input"],
            "discarded_product_ids": [],
            "protocol_scope": _scope(),
            "support_status": "explicit",
        }
    )
    with pytest.raises(GroundtruthValidationError, match="graph cycle"):
        validate_cross_task_links(documents)


def test_terminal_state_must_match_t1() -> None:
    documents = _documents()
    segment = documents["T3"]["workflows"][0]["states"][1]["strands"][0][
        "segments"
    ][0]
    segment["sequence"] = "CCC"
    segment["oligo_derivations"][0]["orientation_to_source"] = "unknown"
    with pytest.raises(GroundtruthValidationError, match="does not match any"):
        validate_cross_task_links(documents)


def _add_second_library_and_terminal_state(documents: dict[str, dict]) -> None:
    library = copy.deepcopy(documents["T1"]["libraries"][0])
    library["library_sequence"] = "CCC"
    library["segments"][0]["segment_id"] = "library-adapter-2"
    library["segments"][0]["sequence"] = "CCC"
    library["segments"][0]["oligo_derivations"] = []
    documents["T1"]["libraries"].append(library)

    workflow = documents["T3"]["workflows"][0]
    state = copy.deepcopy(workflow["states"][1])
    state["state_id"] = "final-2"
    state["reference_strand_id"] = "final-strand-2"
    strand = state["strands"][0]
    strand["strand_id"] = "final-strand-2"
    strand["sequence_architecture"] = "CCC"
    strand["segments"][0]["segment_id"] = "final-adapter-2"
    strand["segments"][0]["sequence"] = "CCC"
    strand["segments"][0]["oligo_derivations"] = []
    workflow["states"].append(state)
    workflow["transitions"][0]["product_state_ids"].append("final-2")
    workflow["transitions"][0]["carried_forward_product_ids"].append("final-2")
    workflow["final_state_ids"].append("final-2")
    workflow["states"][1]["strands"][0]["sequence_architecture"] = "AAA"


def test_terminal_states_match_multiple_t1_libraries_without_stored_ids() -> None:
    documents = _documents()
    _add_second_library_and_terminal_state(documents)

    validate_cross_task_links(documents)


def test_terminal_matching_rejects_ambiguous_duplicate_libraries() -> None:
    documents = _documents()
    _add_second_library_and_terminal_state(documents)
    second_library = documents["T1"]["libraries"][1]
    second_library["library_sequence"] = "AAA"
    documents["T3"]["workflows"][0]["states"][2]["strands"][0][
        "sequence_architecture"
    ] = "AAA"

    with pytest.raises(GroundtruthValidationError, match="matching is ambiguous"):
        validate_cross_task_links(documents)


def test_matching_terminal_architecture_allows_simplified_t3_segments() -> None:
    documents = _documents()
    final_state = documents["T3"]["workflows"][0]["states"][1]
    reference_strand = final_state["strands"][0]
    reference_strand["sequence_architecture"] = "AAA"
    reference_strand["segments"] = [
        {
            "segment_id": "simplified-final-library",
            "role": "simplified_library",
            "structural_role": "unpaired",
            "length": 3,
        }
    ]

    validate_cross_task_links(documents)


def test_terminal_architecture_mismatch_is_not_hidden_by_matching_segments() -> None:
    documents = _documents()
    documents["T3"]["workflows"][0]["states"][1]["strands"][0][
        "sequence_architecture"
    ] = "CCC"

    with pytest.raises(GroundtruthValidationError, match="does not match any"):
        validate_cross_task_links(documents)


def test_duplex_terminal_state_links_its_reference_strand_to_t1() -> None:
    documents = _documents()
    final_state = _paired_state(top_sequence="AAA", bottom_sequence="TTT")
    final_state["state_id"] = "final"
    final_state["strands"][0]["sequence_architecture"] = "AAA"
    documents["T3"]["workflows"][0]["states"][1] = final_state

    validate_cross_task_links(documents)

    final_state["reference_strand_id"] = "bottom"
    with pytest.raises(GroundtruthValidationError, match="does not match any"):
        validate_cross_task_links(documents)


def test_terminal_state_orientation_must_match_t1() -> None:
    documents = _documents()
    documents["T1"]["libraries"][0]["orientation"] = "3_to_5"

    with pytest.raises(GroundtruthValidationError, match="orientation"):
        validate_cross_task_links(documents)


def test_task_scopes_must_agree() -> None:
    documents = _documents()
    documents["T2"]["protocol_scope"] = _scope("other")
    with pytest.raises(GroundtruthValidationError, match="scopes must agree"):
        validate_cross_task_links(documents)
