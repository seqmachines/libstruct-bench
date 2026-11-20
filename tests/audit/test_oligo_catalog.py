from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.oligo_catalog import (
    OligoCatalogError,
    build_oligo_outputs,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
T2_SCHEMA = REPO_ROOT / "schemas" / "groundtruth" / "oligo_groundtruth.schema.json"
CATALOG_SCHEMA = REPO_ROOT / "schemas" / "groundtruth" / "oligo_catalog.schema.json"
METADATA_SCHEMA = REPO_ROOT / "schemas" / "audit" / "oligo_output_build.schema.json"
NOW = "2026-08-01T12:00:00Z"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _oligo(
    *,
    oligo_id: str,
    source_name: str,
    canonical_id: str | None,
    orientation: str = "5_to_3",
) -> dict:
    return {
        "oligo_id": oligo_id,
        "canonical_oligo_id": canonical_id,
        "family_id": None,
        "name": "Illumina P5 adapter" if canonical_id else source_name,
        "aliases": [source_name, "P5"] if canonical_id else [],
        "role": "sequencing_adapter" if canonical_id else "assay_primer",
        "kind": "single",
        "sequence": "AATGATACGGCGACCACCGAGATCTACAC"
        if canonical_id
        else "ACGT",
        "orientation": orientation,
        "components": [],
        "modifications": [],
        "protocol_scope": {"protocol_version": None, "applicable_variants": []},
        "support_status": "explicit",
    }


def _t2(protocol_id: str, oligos: list[dict]) -> dict:
    return {
        "protocol_id": protocol_id,
        "protocol_name": protocol_id.replace("_", " ").title(),
        "protocol_scope": {"protocol_version": None, "applicable_variants": []},
        "oligos": oligos,
    }


def test_builds_canonical_catalog_and_minimal_tsv(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_json(
        first,
        _t2(
            "protocol_a",
            [
                _oligo(
                    oligo_id="p5", source_name="Assay A P5", canonical_id="illumina_p5"
                ),
                _oligo(
                    oligo_id="assay_primer",
                    source_name="Protocol A primer",
                    canonical_id=None,
                ),
            ],
        ),
    )
    _write_json(
        second,
        _t2(
            "protocol_b",
            [
                _oligo(
                    oligo_id="p5", source_name="Assay B P5", canonical_id="illumina_p5"
                )
            ],
        ),
    )
    decisions = {
        "protocol_a": ["protocol_a:decision:001"],
        "protocol_b": ["protocol_b:decision:001"],
    }
    result = build_oligo_outputs(
        t2_paths=[second, first],
        decision_ids_by_protocol=decisions,
        output_dir=tmp_path / "outputs",
        t2_schema_path=T2_SCHEMA,
        catalog_schema_path=CATALOG_SCHEMA,
        metadata_schema_path=METADATA_SCHEMA,
        created_at=NOW,
    )
    catalog = json.loads(result.catalog_path.read_text(encoding="utf-8"))
    shared = next(
        item
        for item in catalog["oligos"]
        if item["canonical_oligo_id"] == "illumina_p5"
    )
    assert shared["canonical_name"] == "Illumina P5 adapter"
    assert shared["aliases"] == ["Assay A P5", "Assay B P5", "P5"]
    assert shared["protocol_refs"] == ["protocol_a:p5", "protocol_b:p5"]
    assert shared["decision_ids"] == [
        "protocol_a:decision:001",
        "protocol_b:decision:001",
    ]
    assert any(
        item["canonical_oligo_id"] == "protocol_a:assay_primer"
        for item in catalog["oligos"]
    )
    with result.tsv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert [(row["protocol_id"], row["oligo_id"]) for row in rows] == [
        ("protocol_a", "assay_primer"),
        ("protocol_a", "p5"),
        ("protocol_b", "p5"),
    ]
    assert "baseline_lineage_json" not in rows[0]
    assert "ground_truth_status" not in rows[0]
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["catalog"]["sha256"]
    assert metadata["tsv"]["sha256"]

    repeated = build_oligo_outputs(
        t2_paths=[first, second],
        decision_ids_by_protocol=decisions,
        output_dir=tmp_path / "outputs-repeat",
        t2_schema_path=T2_SCHEMA,
        catalog_schema_path=CATALOG_SCHEMA,
        metadata_schema_path=METADATA_SCHEMA,
        created_at=NOW,
    )
    assert result.catalog_path.read_bytes() == repeated.catalog_path.read_bytes()
    assert result.tsv_path.read_bytes() == repeated.tsv_path.read_bytes()


def test_shared_canonical_oligo_conflict_requires_human_resolution(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_json(
        first,
        _t2(
            "protocol_a",
            [_oligo(oligo_id="p5", source_name="A P5", canonical_id="illumina_p5")],
        ),
    )
    _write_json(
        second,
        _t2(
            "protocol_b",
            [
                _oligo(
                    oligo_id="p5",
                    source_name="B P5",
                    canonical_id="illumina_p5",
                    orientation="3_to_5",
                )
            ],
        ),
    )
    with pytest.raises(OligoCatalogError, match="conflicting orientation"):
        build_oligo_outputs(
            t2_paths=[first, second],
            decision_ids_by_protocol={
                "protocol_a": ["protocol_a:decision:001"],
                "protocol_b": ["protocol_b:decision:001"],
            },
            output_dir=tmp_path / "outputs",
            t2_schema_path=T2_SCHEMA,
            catalog_schema_path=CATALOG_SCHEMA,
            metadata_schema_path=METADATA_SCHEMA,
            created_at=NOW,
        )
