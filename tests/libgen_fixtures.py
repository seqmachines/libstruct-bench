from __future__ import annotations

import copy
from typing import Any


def t2_prediction() -> dict[str, Any]:
    return {
        "protocol_id": "example_protocol",
        "oligos": [
            {
                "oligo_id": "oligo_rt",
                "name": "RT primer",
                "aliases": ["RT-1"],
                "role": "reverse transcription primer",
                "kind": "single",
                "sequence": "ACGT[UMI:4]",
                "orientation": "5_to_3",
                "components": [],
                "modifications": [],
            }
        ],
    }


def t2_groundtruth() -> dict[str, Any]:
    result = copy.deepcopy(t2_prediction())
    result["protocol_name"] = "Example protocol"
    for oligo in result["oligos"]:
        oligo["support_status"] = "explicit"
    return result


def t1_groundtruth() -> dict[str, Any]:
    return {
        "protocol_id": "example_protocol",
        "protocol_name": "Example protocol",
        "libraries": [
            {
                "modality": "gene expression",
                "library_sequence": "ACGT[UMI:4][CDNA]",
                "strand": "single",
                "orientation": "5_to_3",
                "support_status": "explicit",
                "segments": [
                    {
                        "segment_id": "final_architecture",
                        "kind": "unknown",
                        "role": "final library architecture",
                        "sequence": "ACGT[UMI:4][CDNA]",
                        "orientation": "5_to_3",
                        "support_status": "explicit",
                    }
                ],
            }
        ],
    }


def t3_prediction() -> dict[str, Any]:
    return {
        "protocol_id": "example_protocol",
        "workflows": [
            {
                "workflow_id": "workflow_main",
                "modality": "RNA",
                "states": [
                    _single_state("state_input", "RNA input", "RNA", "[RNA]"),
                    _single_state(
                        "state_cdna",
                        "cDNA product",
                        "DNA",
                        "ACGT[UMI:4][CDNA]",
                        oligo_id="oligo_rt",
                    ),
                ],
                "transitions": [
                    {
                        "transition_id": "transition_rt",
                        "substrate_state_ids": ["state_input"],
                        "operation": "reverse_transcription",
                        "operation_detail": "Reverse transcription with the RT primer.",
                        "oligo_ids": ["oligo_rt"],
                        "major_reagents": [
                            {"name": "reverse transcriptase", "role": "polymerase"}
                        ],
                        "product_state_ids": ["state_cdna"],
                        "carried_forward_product_ids": ["state_cdna"],
                        "discarded_product_ids": [],
                    }
                ],
                "initial_state_ids": ["state_input"],
                "final_state_ids": ["state_cdna"],
            }
        ],
    }


def t3_groundtruth() -> dict[str, Any]:
    result = copy.deepcopy(t3_prediction())
    result["workflows"][0]["modality"] = "gene expression"
    result["protocol_name"] = "Example protocol"
    for workflow in result["workflows"]:
        for state in workflow["states"]:
            state["support_status"] = "explicit"
            for strand in state["strands"]:
                strand["support_status"] = "explicit"
            for region in state["paired_regions"]:
                region["support_status"] = "explicit"
            for discontinuity in state["discontinuities"]:
                discontinuity["support_status"] = "explicit"
        for transition in workflow["transitions"]:
            transition["support_status"] = "explicit"
    return result


def renamed_predictions() -> tuple[dict[str, Any], dict[str, Any]]:
    t2 = t2_prediction()
    t3 = t3_prediction()
    t2["oligos"][0]["oligo_id"] = "predicted_oligo"
    workflow = t3["workflows"][0]
    workflow["workflow_id"] = "predicted_workflow"
    workflow["states"].reverse()
    replacements = {
        "state_input": "predicted_input",
        "state_cdna": "predicted_product",
    }
    for state in workflow["states"]:
        old_state_id = state["state_id"]
        state["state_id"] = replacements[old_state_id]
        old_strand_id = state["strands"][0]["strand_id"]
        state["strands"][0]["strand_id"] = f"renamed_{old_strand_id}"
        state["reference_strand_id"] = f"renamed_{old_strand_id}"
        for segment in state["strands"][0]["segments"]:
            segment["segment_id"] = f"renamed_{segment['segment_id']}"
            for derivation in segment.get("oligo_derivations", []):
                derivation["oligo_id"] = "predicted_oligo"
    transition = workflow["transitions"][0]
    transition["transition_id"] = "predicted_transition"
    transition["substrate_state_ids"] = [replacements[item] for item in transition["substrate_state_ids"]]
    transition["product_state_ids"] = [replacements[item] for item in transition["product_state_ids"]]
    transition["carried_forward_product_ids"] = [
        replacements[item] for item in transition["carried_forward_product_ids"]
    ]
    transition["oligo_ids"] = ["predicted_oligo"]
    workflow["initial_state_ids"] = [replacements[item] for item in workflow["initial_state_ids"]]
    workflow["final_state_ids"] = [replacements[item] for item in workflow["final_state_ids"]]
    return t2, t3


def _single_state(
    state_id: str,
    name: str,
    molecule_type: str,
    architecture: str,
    *,
    oligo_id: str | None = None,
) -> dict[str, Any]:
    strand_id = f"strand_{state_id}"
    segment: dict[str, Any] = {
        "segment_id": f"segment_{state_id}",
        "role": "molecular architecture",
        "structural_role": "unpaired",
        "placeholder": architecture if architecture.startswith("[") and architecture.endswith("]") else "[CDNA]",
    }
    if not (architecture.startswith("[") and architecture.endswith("]")):
        segment["sequence"] = architecture
        segment.pop("placeholder")
    if oligo_id:
        segment["oligo_derivations"] = [
            {"oligo_id": oligo_id, "orientation_to_source": "unknown"}
        ]
    return {
        "state_id": state_id,
        "name": name,
        "molecule_type": molecule_type,
        "strand_architecture": "single_stranded",
        "reference_strand_id": strand_id,
        "physical_state": "solution",
        "strands": [
            {
                "strand_id": strand_id,
                "name": "reference strand",
                "molecule_type": molecule_type,
                "orientation": "5_to_3",
                "sequence_architecture": architecture,
                "segments": [segment],
            }
        ],
        "paired_regions": [],
        "discontinuities": [],
        "properties": [],
    }
