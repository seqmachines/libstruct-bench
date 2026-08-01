from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "audit"
GROUNDTRUTH_SCHEMA_DIR = REPO_ROOT / "schemas" / "groundtruth"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


class AuditSchemaTests(unittest.TestCase):
    def test_all_audit_schemas_are_valid_draft_2020_12(self):
        names = sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json"))
        self.assertEqual(
            names,
            [
                "accepted_correction_regression.v1.schema.json",
                "application_log.v1.schema.json",
                "audit_input_manifest.v2.schema.json",
                "audit_packet.v2.schema.json",
                "checkpoint_report.v1.schema.json",
                "groundtruth_release_manifest.v2.schema.json",
                "oligo_output_build.v1.schema.json",
                "protocol_audit.v2.schema.json",
                "protocol_evidence.v1.schema.json",
                "regression_results.v1.schema.json",
                "release_spec.v1.schema.json",
                "rendition_bundle.v1.schema.json",
                "review_decision.v2.schema.json",
                "source_catalog.v1.schema.json",
            ],
        )
        for name in names:
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(load_schema(name))

    def test_groundtruth_schemas_are_valid_draft_2020_12(self):
        names = sorted(path.name for path in GROUNDTRUTH_SCHEMA_DIR.glob("*.schema.json"))
        self.assertEqual(
            names,
            [
                "final_library_groundtruth.v1.schema.json",
                "library_generation_workflow.v1.schema.json",
                "oligo_catalog.v1.schema.json",
                "oligo_groundtruth.v1.schema.json",
            ],
        )
        for name in names:
            with self.subTest(schema=name):
                schema = json.loads(
                    (GROUNDTRUTH_SCHEMA_DIR / name).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)


if __name__ == "__main__":
    unittest.main()
