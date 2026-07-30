from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "audit"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
COMMIT = "d" * 40
NOW = "2026-07-29T12:00:00Z"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate(name: str, document: dict) -> None:
    validator = Draft202012Validator(
        load_schema(name),
        format_checker=FormatChecker(),
    )
    validator.validate(document)


def input_manifest() -> dict:
    return {
        "schema_version": "libstruct.audit_input_manifest.v1",
        "manifest_id": "drop_seq:inputs:v1",
        "protocol_id": "drop_seq",
        "created_at": NOW,
        "sources": [
            {
                "source_id": "protocol-pdf",
                "role": "primary_evidence",
                "path": "protocols/drop_seq/protocol.pdf",
                "sha256": SHA_A,
                "media_type": "application/pdf",
                "title": "Drop-seq protocol",
                "document_version": "v1.1",
                "dataset_reference": {
                    "repository": "sequencing/scg-protocols-v1",
                    "revision": "commit-sha",
                    "path": "drop_seq/protocol.pdf",
                },
            },
            {
                "source_id": "legacy-html",
                "role": "legacy_curated_html",
                "path": "legacy/scg_html/Drop-seq.html",
                "sha256": SHA_B,
                "media_type": "text/html",
            },
            {
                "source_id": "current-final-library",
                "role": "current_benchmark_record",
                "path": "baselines/scg_lib_structs_v0/drop_seq/groundtruth_final_lib_struct.json",
                "sha256": SHA_C,
                "media_type": "application/json",
            },
        ],
    }


def protocol_audit() -> dict:
    return {
        "schema_version": "libstruct.protocol_audit.v1",
        "audit_id": "drop_seq:audit:001",
        "protocol_id": "drop_seq",
        "input_manifest_sha256": SHA_A,
        "baseline_artifacts": [
            {
                "source_id": "current-final-library",
                "sha256": SHA_C,
            }
        ],
        "run": {
            "agent": "claude-code",
            "provider": "anthropic",
            "model": "example-model",
            "tool_version": "1.0.0",
            "review_mode": "primary",
            "started_at": NOW,
            "completed_at": NOW,
            "prompt_sha256": SHA_A,
            "skill_sha256": SHA_B,
            "schema_sha256": SHA_C,
        },
        "disposition": "changes_proposed",
        "summary": "One barcode-length discrepancy requires human review.",
        "issues": [
            {
                "issue_id": "barcode-length-001",
                "category": "variable_region_length",
                "title": "Feature barcode flank is one base short",
                "target": {
                    "kind": "benchmark_record",
                    "artifact_source_id": "current-final-library",
                    "json_pointer": "/libraries/0/library_sequence",
                    "library_id": "feature",
                    "modality": "feature",
                },
                "assessment": {
                    "support": "primary_explicit",
                    "rationale": "The final PCR product and oligo table both print ten bases.",
                },
                "evidence": [
                    {
                        "source_id": "protocol-pdf",
                        "locator": {
                            "page": 42,
                            "section": "Final PCR product",
                        },
                        "supports": "proposed",
                        "observed_sequence": "NNNNNNNNNN",
                    }
                ],
                "transformations": [],
                "recommendation": "propose_change",
                "proposed_patch": [
                    {
                        "op": "test",
                        "path": "/libraries/0/library_sequence",
                        "value": "?????????",
                    },
                    {
                        "op": "replace",
                        "path": "/libraries/0/library_sequence",
                        "value": "??????????",
                    },
                ],
                "confidence": "high",
            }
        ],
    }


def review_decision() -> dict:
    return {
        "schema_version": "libstruct.review_decision.v1",
        "decision_id": "drop_seq:decision:001",
        "protocol_id": "drop_seq",
        "audit_id": "drop_seq:audit:001",
        "proposal_sha256": SHA_A,
        "baseline_artifacts": [
            {
                "source_id": "current-final-library",
                "sha256": SHA_C,
            }
        ],
        "reviewer": {
            "reviewer_id": "curator-001",
            "name": "Human Curator",
        },
        "decided_at": NOW,
        "overall_disposition": "accepted",
        "issue_decisions": [
            {
                "issue_id": "barcode-length-001",
                "disposition": "accepted",
                "rationale": "The explicit final product and oligo table agree.",
            }
        ],
    }


def release_manifest() -> dict:
    return {
        "schema_version": "libstruct.groundtruth_release_manifest.v1",
        "release_id": "libstruct-groundtruth-v1.0.0",
        "version": "v1.0.0",
        "release_status": "frozen",
        "created_at": NOW,
        "generated_by": {
            "tool_version": "libstruct-bench-0.2.0",
            "git_commit": COMMIT,
        },
        "policies": [
            {
                "path": "policies/evidence-policy.md",
                "sha256": SHA_A,
            },
            {
                "path": "policies/adjudication-policy.md",
                "sha256": SHA_B,
            },
        ],
        "schemas": [
            {
                "path": "schemas/protocol_audit.v1.schema.json",
                "sha256": SHA_A,
                "schema_version": "libstruct.protocol_audit.v1",
            },
            {
                "path": "schemas/review_decision.v1.schema.json",
                "sha256": SHA_B,
                "schema_version": "libstruct.review_decision.v1",
            },
            {
                "path": "schemas/groundtruth_release_manifest.v1.schema.json",
                "sha256": SHA_C,
                "schema_version": "libstruct.groundtruth_release_manifest.v1",
            },
        ],
        "source_datasets": [
            {
                "repository": "sequencing/scg-protocols-v1",
                "revision": "source-commit",
            },
            {
                "repository": "sequencing/scg-libstruct-groundtruth-audit",
                "revision": "audit-commit",
            },
        ],
        "protocols": [
            {
                "protocol_id": "drop_seq",
                "disposition": "included",
                "input_manifest_sha256": SHA_A,
                "audit_ids": [
                    "drop_seq:audit:001"
                ],
                "decision_ids": [
                    "drop_seq:decision:001"
                ],
                "artifacts": [
                    {
                        "path": "groundtruth/drop_seq/groundtruth_final_lib_struct.json",
                        "sha256": SHA_C,
                        "schema_version": "libstruct.final_library_groundtruth.v1",
                    }
                ],
                "unresolved_issue_ids": [],
                "limitations": [],
            }
        ],
    }


class AuditSchemaTests(unittest.TestCase):
    def test_all_audit_schemas_are_valid_draft_2020_12(self):
        names = sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json"))
        self.assertEqual(
            names,
            [
                "audit_input_manifest.v1.schema.json",
                "groundtruth_release_manifest.v1.schema.json",
                "protocol_audit.v1.schema.json",
                "review_decision.v1.schema.json",
            ],
        )
        for name in names:
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(load_schema(name))

    def test_input_manifest_requires_all_three_source_roles(self):
        document = input_manifest()
        validate("audit_input_manifest.v1.schema.json", document)

        without_html = copy.deepcopy(document)
        without_html["sources"] = [
            source
            for source in without_html["sources"]
            if source["role"] != "legacy_curated_html"
        ]
        with self.assertRaises(ValidationError):
            validate("audit_input_manifest.v1.schema.json", without_html)

    def test_proposed_change_requires_an_exact_patch(self):
        document = protocol_audit()
        validate("protocol_audit.v1.schema.json", document)

        without_patch = copy.deepcopy(document)
        without_patch["issues"][0]["proposed_patch"] = []
        with self.assertRaises(ValidationError):
            validate("protocol_audit.v1.schema.json", without_patch)

    def test_confirmed_audit_cannot_contain_issues(self):
        document = protocol_audit()
        document["disposition"] = "confirmed"
        with self.assertRaises(ValidationError):
            validate("protocol_audit.v1.schema.json", document)

    def test_review_decision_requires_a_human_reviewer(self):
        document = review_decision()
        validate("review_decision.v1.schema.json", document)

        without_reviewer = copy.deepcopy(document)
        del without_reviewer["reviewer"]["reviewer_id"]
        with self.assertRaises(ValidationError):
            validate("review_decision.v1.schema.json", without_reviewer)

    def test_confirmed_decision_has_no_issue_decisions(self):
        document = review_decision()
        document["overall_disposition"] = "confirmed"
        with self.assertRaises(ValidationError):
            validate("review_decision.v1.schema.json", document)

    def test_included_release_protocol_requires_artifacts(self):
        document = release_manifest()
        validate("groundtruth_release_manifest.v1.schema.json", document)

        without_artifact = copy.deepcopy(document)
        without_artifact["protocols"][0]["artifacts"] = []
        with self.assertRaises(ValidationError):
            validate("groundtruth_release_manifest.v1.schema.json", without_artifact)


if __name__ == "__main__":
    unittest.main()
