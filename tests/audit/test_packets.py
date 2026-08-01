from __future__ import annotations

import json
from pathlib import Path

import pytest

from libstruct_bench.audit.packets import PacketError, build_phase_packet


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA = (
    REPO_ROOT / "schemas" / "audit" / "audit_input_manifest.v2.schema.json"
)
PACKET_SCHEMA = REPO_ROOT / "schemas" / "audit" / "audit_packet.v2.schema.json"
NOW = "2026-07-30T12:00:00Z"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _phase_manifest(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_root = tmp_path / "source-dataset"
    groundtruth_root = tmp_path / "groundtruth-dataset"
    protocol_root = source_root / "example_protocol"
    html_root = source_root / "scg_html"
    baseline_root = groundtruth_root / "example_protocol"
    protocol_root.mkdir(parents=True)
    html_root.mkdir(parents=True)
    baseline_root.mkdir(parents=True)
    paper = protocol_root / "paper.pdf"
    legacy = html_root / "Example.html"
    baseline = baseline_root / "groundtruth_final_lib_struct.json"
    paper.write_bytes(b"%PDF primary evidence")
    legacy.write_text("<html>legacy curation</html>", encoding="utf-8")
    _write_json(baseline, {"protocol_id": "example_protocol", "libraries": []})
    review = {
        "reviewer_id": "curator-001",
        "reviewed_at": NOW,
        "reason": "Approved for the pilot.",
    }

    def source(
        source_id: str,
        role: str,
        kind: str,
        path: str,
        actual: Path,
        repository: str,
    ) -> dict:
        return {
            "source_id": source_id,
            "role": role,
            "source_kind": kind,
            "approval_status": "included",
            "task_relevance": ["T1", "T2", "T3"],
            "path": path,
            "sha256": _sha(actual),
            "size_bytes": actual.stat().st_size,
            "media_type": "application/octet-stream",
            "dataset_reference": {
                "provider": "huggingface",
                "repository": repository,
                "revision": "a" * 40,
                "path": path,
            },
            "review": review,
        }

    manifest = {
        "schema_version": "libstruct.audit_input_manifest.v2",
        "manifest_id": "example_protocol:inputs:v2",
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
                "primary-paper",
                "primary_evidence",
                "original_paper",
                "example_protocol/paper.pdf",
                paper,
                "seqmachines/all-protocol-sources",
            ),
            source(
                "legacy-html",
                "legacy_curated_html",
                "legacy_html",
                "scg_html/Example.html",
                legacy,
                "seqmachines/all-protocol-sources",
            ),
            source(
                "current-t1",
                "current_benchmark_record",
                "current_t1",
                "example_protocol/groundtruth_final_lib_struct.json",
                baseline,
                "seqmachines/libstruct-groundtruth-audit",
            ),
        ],
    }
    manifest_path = tmp_path / "manifest-v2.json"
    _write_json(manifest_path, manifest)
    return manifest_path, source_root, groundtruth_root


def _build_phase(
    tmp_path: Path,
    *,
    phase: str,
    output_name: str,
    evidence: Path | None = None,
):
    manifest, source_root, groundtruth_root = _phase_manifest(tmp_path)
    return build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        output_dir=tmp_path / output_name,
        manifest_schema_path=MANIFEST_SCHEMA,
        packet_schema_path=PACKET_SCHEMA,
        phase=phase,
        evidence_artifact_path=evidence,
    )


def test_evidence_phase_physically_hides_legacy_and_current_records(
    tmp_path: Path,
) -> None:
    result = _build_phase(
        tmp_path, phase="evidence", output_name="evidence-packet"
    )
    packet = json.loads(result.packet_path.read_text(encoding="utf-8"))
    projected = json.loads(
        (result.output_dir / "manifest.json").read_text(encoding="utf-8")
    )

    assert result.file_count == 1
    assert {item["role"] for item in packet["files"]} == {"primary_evidence"}
    assert {item["role"] for item in projected["sources"]} == {"primary_evidence"}
    all_text = b"\n".join(
        path.read_bytes()
        for path in result.output_dir.rglob("*")
        if path.is_file()
    )
    assert b"legacy curation" not in all_text
    assert b"groundtruth_final_lib_struct" not in all_text


def test_comparison_phase_requires_and_freezes_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    _write_json(evidence, {"schema_version": "libstruct.protocol_evidence.v1"})

    result = _build_phase(
        tmp_path,
        phase="comparison",
        output_name="comparison-packet",
        evidence=evidence,
    )
    packet = json.loads(result.packet_path.read_text(encoding="utf-8"))
    assert {item["role"] for item in packet["files"]} == {
        "primary_evidence",
        "legacy_curated_html",
        "current_benchmark_record",
    }
    assert (result.output_dir / "frozen_evidence" / "evidence.json").read_bytes() == evidence.read_bytes()


def test_comparison_phase_without_evidence_is_rejected(tmp_path: Path) -> None:
    manifest, source_root, groundtruth_root = _phase_manifest(tmp_path)
    with pytest.raises(PacketError, match="requires --evidence-artifact"):
        build_phase_packet(
            manifest_path=manifest,
            source_dataset_dir=source_root,
            groundtruth_dataset_dir=groundtruth_root,
            output_dir=tmp_path / "comparison-packet",
            manifest_schema_path=MANIFEST_SCHEMA,
            packet_schema_path=PACKET_SCHEMA,
            phase="comparison",
        )


def test_phase_packet_rejects_stale_dataset_content(tmp_path: Path) -> None:
    manifest, source_root, groundtruth_root = _phase_manifest(tmp_path)
    (source_root / "example_protocol" / "paper.pdf").write_bytes(b"changed")
    with pytest.raises(PacketError, match="stale hash"):
        build_phase_packet(
            manifest_path=manifest,
            source_dataset_dir=source_root,
            groundtruth_dataset_dir=groundtruth_root,
            output_dir=tmp_path / "evidence-packet",
            manifest_schema_path=MANIFEST_SCHEMA,
            packet_schema_path=PACKET_SCHEMA,
            phase="evidence",
        )


def test_comparison_packet_projects_global_oligo_tsv_to_one_protocol(
    tmp_path: Path,
) -> None:
    manifest_path, source_root, groundtruth_root = _phase_manifest(tmp_path)
    tsv = groundtruth_root / "groundtruth_oligos.tsv"
    tsv.write_text(
        "protocol_id\toligo_name\toligo_sequence\n"
        "other_protocol\tOther\tCCCC\n"
        "example_protocol\tPrimer A\tAAAA\n"
        "example_protocol\tPrimer B\tGGGG\n",
        encoding="utf-8",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"].append(
        {
            "source_id": "oligo-tsv-baseline",
            "role": "current_benchmark_record",
            "source_kind": "oligo_tsv_baseline",
            "approval_status": "included",
            "task_relevance": ["T2"],
            "path": "groundtruth_oligos.tsv",
            "sha256": _sha(tsv),
            "size_bytes": tsv.stat().st_size,
            "media_type": "text/tab-separated-values",
            "row_filter": {
                "column": "protocol_id",
                "value": "example_protocol",
                "include_source_row_number": True,
            },
            "dataset_reference": {
                "provider": "huggingface",
                "repository": "seqmachines/libstruct-groundtruth-audit",
                "revision": "a" * 40,
                "path": "groundtruth_oligos.tsv",
            },
            "review": {
                "reviewer_id": "curator-001",
                "reviewed_at": NOW,
                "reason": "Human-reviewed T2 baseline.",
            },
        }
    )
    _write_json(manifest_path, manifest)
    evidence = tmp_path / "evidence.json"
    _write_json(evidence, {"schema_version": "libstruct.protocol_evidence.v1"})
    result = build_phase_packet(
        manifest_path=manifest_path,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        output_dir=tmp_path / "comparison-packet",
        manifest_schema_path=MANIFEST_SCHEMA,
        packet_schema_path=PACKET_SCHEMA,
        phase="comparison",
        evidence_artifact_path=evidence,
    )
    packet = json.loads(result.packet_path.read_text(encoding="utf-8"))
    entry = next(
        item for item in packet["files"] if item["source_id"] == "oligo-tsv-baseline"
    )
    projected = (result.output_dir / entry["path"]).read_text(encoding="utf-8")
    assert entry["transformation"] == "tsv_row_filter"
    assert entry["source_sha256"] == _sha(tsv)
    assert "source_row_number\tprotocol_id" in projected
    assert "3\texample_protocol\tPrimer A\tAAAA" in projected
    assert "4\texample_protocol\tPrimer B\tGGGG" in projected
    assert "other_protocol" not in projected
