from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    _schema_validator_from_bytes,
    validate_document,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "audit"
GROUNDTRUTH_SCHEMA_DIR = REPO_ROOT / "schemas" / "groundtruth"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def property_names(value: object) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.extend(properties)
        for child in value.values():
            names.extend(property_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(property_names(child))
    return names


class AuditSchemaTests(unittest.TestCase):
    def test_schema_validator_cache_is_bound_to_path_and_exact_schema_bytes(self):
        _schema_validator_from_bytes.cache_clear()
        self.addCleanup(_schema_validator_from_bytes.cache_clear)
        with tempfile.TemporaryDirectory() as directory:
            schema_path = Path(directory) / "mutable.schema.json"
            same_bytes_path = Path(directory) / "same-bytes.schema.json"
            integer_schema = json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                }
            )
            schema_path.write_text(integer_schema, encoding="utf-8")
            same_bytes_path.write_text(integer_schema, encoding="utf-8")
            original_check_schema = Draft202012Validator.check_schema
            with patch.object(
                Draft202012Validator,
                "check_schema",
                side_effect=original_check_schema,
            ) as check_schema:
                validate_document({"value": 1}, schema_path, label="fixture")
                validate_document({"value": 1}, schema_path, label="fixture")
                self.assertEqual(check_schema.call_count, 1)

                validate_document({"value": 1}, same_bytes_path, label="fixture")
                self.assertEqual(check_schema.call_count, 2)

                schema_path.write_text(
                    json.dumps(
                        {
                            "$schema": "https://json-schema.org/draft/2020-12/schema",
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(AuditArtifactError, "schema error"):
                    validate_document({"value": 1}, schema_path, label="fixture")
                self.assertEqual(check_schema.call_count, 3)

    def test_all_audit_schemas_are_valid_draft_2020_12(self):
        names = sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json"))
        self.assertEqual(
            names,
            [
                "accepted_correction_regression.schema.json",
                "application_log.schema.json",
                "audit_input_manifest.schema.json",
                "audit_packet.schema.json",
                "checkpoint_report.schema.json",
                "connected_process_final_approval.schema.json",
                "connected_process_migration_plan.schema.json",
                "connected_process_policy_decision.schema.json",
                "connected_process_policy_proposal.schema.json",
                "connected_process_preview.schema.json",
                "connected_process_source_check.schema.json",
                "groundtruth_release_manifest.schema.json",
                "legacy_conversion.schema.json",
                "oligo_output_build.schema.json",
                "promotion_log.schema.json",
                "protocol_audit.schema.json",
                "regression_results.schema.json",
                "release_spec.schema.json",
                "rendition_bundle.schema.json",
                "review_decision.schema.json",
                "source_catalog.schema.json",
            ],
        )
        for name in names:
            with self.subTest(schema=name):
                schema = load_schema(name)
                Draft202012Validator.check_schema(schema)
                self.assertNotIn("schema_version", json.dumps(schema))

    def test_groundtruth_schemas_omit_audit_only_fields(self):
        forbidden = {
            "final_library_groundtruth.schema.json": {
                "evidence",
                "ground_truth_status",
                "library_id",
                "strands",
            },
            "oligo_groundtruth.schema.json": {
                "limitations",
                "baseline_lineage",
                "evidence",
                "ground_truth_status",
                "notes",
            },
            "library_generation_workflow.schema.json": {
                "limitations",
                "ground_truth_status",
                "notes",
                "evidence",
                "workflow_branch",
            },
        }
        for name, removed in forbidden.items():
            with self.subTest(schema=name):
                schema = json.loads(
                    (GROUNDTRUTH_SCHEMA_DIR / name).read_text(encoding="utf-8")
                )
                self.assertTrue(removed.isdisjoint(property_names(schema)))

        t3 = json.loads(
            (
                GROUNDTRUTH_SCHEMA_DIR / "library_generation_workflow.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(property_names(t3).count("modality"), 1)
        self.assertNotIn("modality", t3["required"])
        self.assertNotIn("modality", t3["$defs"]["workflow"]["required"])
        self.assertNotIn("final_state_ids", t3["$defs"]["workflow"]["properties"])
        self.assertIn("final_outputs", t3["$defs"]["workflow"]["required"])

    def test_groundtruth_schemas_are_valid_draft_2020_12(self):
        names = sorted(
            path.name for path in GROUNDTRUTH_SCHEMA_DIR.glob("*.schema.json")
        )
        self.assertEqual(
            names,
            [
                "final_library_groundtruth.schema.json",
                "library_generation_workflow.schema.json",
                "oligo_catalog.schema.json",
                "oligo_groundtruth.schema.json",
            ],
        )
        for name in names:
            with self.subTest(schema=name):
                schema = json.loads(
                    (GROUNDTRUTH_SCHEMA_DIR / name).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                self.assertNotIn("schema_version", json.dumps(schema))


if __name__ == "__main__":
    unittest.main()
