from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.audit.release import SCHEMA_FILES, ReleaseError, build_release_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT = REPO_ROOT / "schemas" / "audit"


def _spec() -> dict:
    return {
        "release_id": "groundtruth-snapshot",
        "release_status": "candidate",
        "created_at": "2026-08-01T12:00:00Z",
        "expected_protocol_count": 1,
        "reviewed_protocol_count": 0,
        "generated_by": {"tool_version": "audit-builder", "git_commit": "a" * 40},
        "source_datasets": [{"provider": "git", "repository": "example/data", "revision": "b" * 40}],
        "policy_paths": ["docs/audit/evidence-policy.md"],
        "schema_paths": [f"schemas/{name}" for name in sorted(set(SCHEMA_FILES.values()))],
        "checkpoint_paths": ["checkpoints/checkpoint-0.json"],
        "independent_audit": {"seed": "pilot", "sample_fraction": 0.1, "selected_protocol_ids": []},
        "protocols": [],
        "oligo_outputs": {"catalog_path": "oligos/catalog.json", "tsv_path": "oligos/groundtruth_oligos.tsv", "build_metadata_path": "oligos/build.json"},
    }


def test_release_spec_uses_hashable_paths_without_version_labels() -> None:
    schema = json.loads((AUDIT / "release_spec.schema.json").read_text())
    Draft202012Validator(schema).validate(_spec())
    encoded = json.dumps(_spec())
    assert "schema_version" not in encoded
    assert '"version"' not in encoded
    assert all(".v1." not in name and ".v2." not in name for name in SCHEMA_FILES.values())


def test_release_builder_rejects_old_schema_and_release_labels(tmp_path: Path) -> None:
    spec = _spec() | {"schema_version": "old", "version": "old"}
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ReleaseError, match="Additional properties"):
        build_release_manifest(
            spec_path=spec_path,
            artifact_root=tmp_path,
            output_path=tmp_path / "manifest.json",
            spec_schema_path=AUDIT / "release_spec.schema.json",
            release_schema_path=AUDIT / "groundtruth_release_manifest.schema.json",
        )
