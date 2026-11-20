from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import sha256_file
from libstruct_bench.improvement.artifacts import CapabilityImprovementError
from libstruct_bench.improvement.validator_review import (
    PROHIBITED_ACTIONS,
    validate_validator_review_draft,
)


def _comparison(tmp_path: Path) -> dict[str, object]:
    analysis_path = tmp_path / "error_analysis.json"
    analysis_path.write_text(
        json.dumps(
            {
                "protocol_id": "demo_protocol",
                "observations": [
                    {"error_id": "err_0001", "substantive": True},
                    {"error_id": "err_0002", "substantive": True},
                    {"error_id": "err_0003", "substantive": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "protocol_id": "demo_protocol",
        "comparison_digest": "a" * 64,
        "conditions": {
            "C30": {
                "artifacts": {
                    "error_analysis": {
                        "path": analysis_path.as_posix(),
                        "sha256": sha256_file(analysis_path),
                    }
                }
            }
        },
    }


def _draft() -> dict[str, object]:
    return {
        "schema_version": "libstruct.libgen_kb_validator_review_proposal_draft.v1",
        "protocol_id": "demo_protocol",
        "comparison_digest": "a" * 64,
        "revision_round": 0,
        "summary": "One possible evaluator defect and one valid difference.",
        "findings": [
            {
                "finding_id": "F001",
                "observation_refs": [{"condition": "C30", "error_id": "err_0001"}],
                "tasks": ["T2"],
                "classification": "evaluator_defect",
                "validator_issue_candidate": True,
                "diagnosis": "Equivalent structured values received a penalty.",
                "conditions_affected": ["C30"],
                "source_evidence": [
                    {
                        "artifact_path": "inputs/sources/001-source.pdf",
                        "locator": "p. 3",
                        "summary": "The source establishes equivalence.",
                    }
                ],
                "verifier_evidence": [
                    {
                        "artifact_path": "inputs/C30/error_analysis.json",
                        "locator": "observations/err_0001",
                        "summary": "The verifier emitted a penalty.",
                    }
                ],
                "trajectory_evidence": [],
                "score_effect": "false_penalty",
                "confidence": "high",
            },
            {
                "finding_id": "F002",
                "observation_refs": [{"condition": "C30", "error_id": "err_0002"}],
                "tasks": ["T3"],
                "classification": "valid_agent_difference",
                "validator_issue_candidate": False,
                "diagnosis": "The predicted transition is genuinely absent.",
                "conditions_affected": ["C0", "C30"],
                "source_evidence": [],
                "verifier_evidence": [
                    {
                        "artifact_path": "inputs/C30/verifier_details.json",
                        "locator": "scoring/t3",
                        "summary": "The scored mismatch is direct.",
                    }
                ],
                "trajectory_evidence": [],
                "score_effect": "neutral",
                "confidence": "medium",
            },
        ],
        "no_validator_issue_rationale": None,
    }


def test_validator_draft_partitions_only_c30_substantive_observations(
    tmp_path: Path,
) -> None:
    validate_validator_review_draft(_draft(), _comparison(tmp_path))


def test_validator_draft_rejects_inconsistent_issue_flag(tmp_path: Path) -> None:
    draft = copy.deepcopy(_draft())
    draft["findings"][0]["validator_issue_candidate"] = False  # type: ignore[index]
    with pytest.raises(CapabilityImprovementError, match="inconsistent"):
        validate_validator_review_draft(draft, _comparison(tmp_path))


def test_validator_draft_rejects_incomplete_observation_partition(
    tmp_path: Path,
) -> None:
    draft = copy.deepcopy(_draft())
    draft["findings"] = draft["findings"][:1]  # type: ignore[index]
    with pytest.raises(CapabilityImprovementError, match="exactly partition"):
        validate_validator_review_draft(draft, _comparison(tmp_path))


def test_validator_review_contract_forbids_every_mutation_channel() -> None:
    assert set(PROHIBITED_ACTIONS) == {
        "agent_rerun",
        "pact_update",
        "prompt_update",
        "knowledge_base_update",
        "ground_truth_update",
        "benchmark_promotion",
        "verifier_update",
        "capability_synthesis",
    }
