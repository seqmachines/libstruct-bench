from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from libstruct_bench.audit.packets import PacketError, build_phase_packet
from libstruct_bench.audit.renditions import RenditionError, build_rendition_bundle


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "audit"
MANIFEST_SCHEMA = SCHEMA_DIR / "audit_input_manifest.schema.json"
PACKET_SCHEMA = SCHEMA_DIR / "audit_packet.schema.json"
RENDITION_SCHEMA = SCHEMA_DIR / "rendition_bundle.schema.json"
NOW = "2026-08-01T12:00:00Z"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
            <sheets><sheet name="Oligos" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Target="worksheets/sheet1.xml"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <si><t>ACGTNNNN</t></si></sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Oligo A</t></is></c>
            <c r="B1" t="s"><v>0</v></c></row></sheetData></worksheet>""",
        )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_root = tmp_path / "source-dataset"
    groundtruth_root = tmp_path / "groundtruth-dataset"
    protocol_root = source_root / "example_protocol"
    html_root = source_root / "scg_html"
    baseline_root = groundtruth_root / "example_protocol"
    protocol_root.mkdir(parents=True)
    html_root.mkdir()
    baseline_root.mkdir(parents=True)
    workbook = protocol_root / "oligos.xlsx"
    legacy = html_root / "Example.html"
    baseline = baseline_root / "groundtruth.json"
    _xlsx(workbook)
    legacy.write_text("<html>legacy</html>", encoding="utf-8")
    _write_json(baseline, {"protocol_id": "example_protocol"})
    review = {
        "reviewer_id": "curator-001",
        "reviewed_at": NOW,
        "reason": "Approved for pilot.",
    }

    def source(
        source_id: str,
        role: str,
        source_kind: str,
        path: str,
        actual: Path,
        media_type: str,
        repository: str,
    ) -> dict:
        return {
            "source_id": source_id,
            "role": role,
            "source_kind": source_kind,
            "approval_status": "included",
            "task_relevance": ["T1", "T2", "T3"],
            "path": path,
            "sha256": _sha(actual),
            "size_bytes": actual.stat().st_size,
            "media_type": media_type,
            "dataset_reference": {
                "provider": "huggingface",
                "repository": repository,
                "revision": "a" * 40,
                "path": path,
            },
            "review": review,
        }

    manifest = {
        "manifest_id": "example_protocol:inputs:test",
        "protocol_id": "example_protocol",
        "created_at": NOW,
        "source_catalog_sha256": "b" * 64,
        "checkpoint": {
            "checkpoint_id": "pilot-0",
            "protocol_ordinal": 1,
            "reviewed_protocol_count": 0,
        },
        "sources": [
            source(
                "primary-workbook",
                "primary_evidence",
                "oligo_table",
                "example_protocol/oligos.xlsx",
                workbook,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "seqmachines/all-protocol-sources",
            ),
            source(
                "legacy-html",
                "legacy_curated_html",
                "legacy_html",
                "scg_html/Example.html",
                legacy,
                "text/html",
                "seqmachines/all-protocol-sources",
            ),
            source(
                "current-t1",
                "current_benchmark_record",
                "current_t1",
                "example_protocol/groundtruth.json",
                baseline,
                "application/json",
                "seqmachines/libstruct-groundtruth-audit",
            ),
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path, source_root, groundtruth_root


def test_spreadsheet_rendition_is_content_addressed_and_packet_visible(
    tmp_path: Path,
) -> None:
    manifest, source_root, groundtruth_root = _fixture(tmp_path)
    result = build_rendition_bundle(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        output_dir=tmp_path / "renditions",
        manifest_schema_path=MANIFEST_SCHEMA,
        rendition_schema_path=RENDITION_SCHEMA,
        created_at=NOW,
    )
    bundle = json.loads(result.bundle_path.read_text(encoding="utf-8"))
    table_path = result.output_dir / bundle["sources"][0]["artifacts"][0]["path"]
    table = table_path.read_text(encoding="utf-8")
    assert "SHEET Oligos" in table
    assert "A1\tOligo A" in table
    assert "B1\tACGTNNNN" in table

    packet = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        output_dir=tmp_path / "comparison-packet",
        manifest_schema_path=MANIFEST_SCHEMA,
        packet_schema_path=PACKET_SCHEMA,
        rendition_bundle_dir=result.output_dir,
        rendition_schema_path=RENDITION_SCHEMA,
        phase="comparison",
    )
    metadata = json.loads(packet.packet_path.read_text(encoding="utf-8"))
    assert packet.rendition_count == 1
    assert metadata["renditions"][0]["source_id"] == "primary-workbook"
    assert (packet.output_dir / metadata["renditions"][0]["path"]).is_file()


def test_document_packet_is_blocked_without_renditions(tmp_path: Path) -> None:
    manifest, source_root, groundtruth_root = _fixture(tmp_path)
    with pytest.raises(PacketError, match="require --rendition-bundle-dir"):
        build_phase_packet(
            manifest_path=manifest,
            source_dataset_dir=source_root,
            groundtruth_dataset_dir=groundtruth_root,
            output_dir=tmp_path / "comparison-packet",
            manifest_schema_path=MANIFEST_SCHEMA,
            packet_schema_path=PACKET_SCHEMA,
            phase="comparison",
        )


def test_packet_rejects_renditions_from_a_stale_manifest(tmp_path: Path) -> None:
    manifest, source_root, groundtruth_root = _fixture(tmp_path)
    result = build_rendition_bundle(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        output_dir=tmp_path / "renditions",
        manifest_schema_path=MANIFEST_SCHEMA,
        rendition_schema_path=RENDITION_SCHEMA,
        created_at=NOW,
    )
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["notes"] = "Reviewed manifest metadata changed."
    _write_json(manifest, value)
    with pytest.raises(PacketError, match="stale input manifest"):
        build_phase_packet(
            manifest_path=manifest,
            source_dataset_dir=source_root,
            groundtruth_dataset_dir=groundtruth_root,
            output_dir=tmp_path / "comparison-packet",
            manifest_schema_path=MANIFEST_SCHEMA,
            packet_schema_path=PACKET_SCHEMA,
            rendition_bundle_dir=result.output_dir,
            rendition_schema_path=RENDITION_SCHEMA,
            phase="comparison",
        )


def test_renditions_allow_a_sibling_audit_directory_under_a_common_root(
    tmp_path: Path,
) -> None:
    manifest, _source_root, _groundtruth_root = _fixture(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    primary = next(
        source for source in value["sources"] if source["role"] == "primary_evidence"
    )
    primary["dataset_reference"]["path"] = (
        f"source-dataset/{primary['dataset_reference']['path']}"
    )
    _write_json(manifest, value)

    result = build_rendition_bundle(
        manifest_path=manifest,
        source_dataset_dir=tmp_path,
        output_dir=tmp_path / "ground_truth_audit" / "renditions" / "example_protocol",
        manifest_schema_path=MANIFEST_SCHEMA,
        rendition_schema_path=RENDITION_SCHEMA,
        created_at=NOW,
    )

    assert result.bundle_path.is_file()


def test_renditions_reject_output_inside_an_actual_source_directory(
    tmp_path: Path,
) -> None:
    manifest, source_root, _groundtruth_root = _fixture(tmp_path)
    with pytest.raises(RenditionError, match="overlaps an input source directory"):
        build_rendition_bundle(
            manifest_path=manifest,
            source_dataset_dir=source_root,
            output_dir=source_root / "example_protocol" / "renditions",
            manifest_schema_path=MANIFEST_SCHEMA,
            rendition_schema_path=RENDITION_SCHEMA,
            created_at=NOW,
        )
