from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.packets import PacketError, build_phase_packet


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _source(
    *, source_id: str, role: str, kind: str, logical_path: str,
    actual_path: Path, dataset: str, row_filter: dict | None = None,
) -> dict:
    value = {
        "source_id": source_id,
        "role": role,
        "source_kind": kind,
        "approval_status": "included",
        "task_relevance": ["T1", "T2", "T3"],
        "path": logical_path,
        "sha256": hashlib.sha256(actual_path.read_bytes()).hexdigest(),
        "size_bytes": actual_path.stat().st_size,
        "media_type": "text/tab-separated-values" if row_filter else "text/plain",
        "dataset_reference": {
            "provider": "local_fixture",
            "repository": f"local/{dataset}",
            "revision": "a" * 64,
            "path": logical_path,
        },
        "review": {
            "reviewer_id": "reviewer",
            "reviewed_at": "2026-08-01T12:00:00Z",
            "reason": "approved for test",
        },
    }
    if row_filter:
        value["row_filter"] = row_filter
    return value


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source_root = tmp_path / "sources"
    groundtruth_root = tmp_path / "groundtruth"
    run_root = tmp_path / "runs"
    primary = source_root / "protocols/example/paper.txt"
    legacy = source_root / "scg_html/example.html"
    t1 = groundtruth_root / "baselines/example/groundtruth_final_lib_struct.json"
    t2 = groundtruth_root / "baselines/example/groundtruth_oligos.json"
    tsv = groundtruth_root / "groundtruth_oligos.tsv"
    run = run_root / "example/prediction.json"
    _write(primary, b"primary evidence")
    _write(legacy, b"<html>curation</html>")
    _write(t1, b'{"protocol_id":"example","libraries":[]}')
    _write(t2, b'{"protocol_id":"example","oligos":[]}')
    _write(tsv, b"protocol\toligo_name\nexample\tP5\nother\tP7\n")
    _write(run, b'{"score":0.5}')
    sources = [
        _source(source_id="primary:paper", role="primary_evidence", kind="original_paper", logical_path="protocols/example/paper.txt", actual_path=primary, dataset="sources"),
        _source(source_id="legacy:html", role="legacy_curated_html", kind="legacy_html", logical_path="scg_html/example.html", actual_path=legacy, dataset="sources"),
        _source(source_id="current:t1", role="current_benchmark_record", kind="current_t1", logical_path="baselines/example/groundtruth_final_lib_struct.json", actual_path=t1, dataset="groundtruth"),
        _source(source_id="current:t2", role="current_benchmark_record", kind="current_t2", logical_path="baselines/example/groundtruth_oligos.json", actual_path=t2, dataset="groundtruth"),
        _source(source_id="current:tsv", role="current_benchmark_record", kind="oligo_tsv_baseline", logical_path="groundtruth_oligos.tsv", actual_path=tsv, dataset="groundtruth", row_filter={"column": "protocol", "value": "example", "include_source_row_number": True}),
        _source(source_id="run:prediction", role="benchmark_run_artifact", kind="benchmark_prediction", logical_path="example/prediction.json", actual_path=run, dataset="runs"),
    ]
    manifest = {
        "manifest_id": "example:inputs:test",
        "protocol_id": "example",
        "created_at": "2026-08-01T12:00:00Z",
        "source_catalog_sha256": "b" * 64,
        "checkpoint": {"checkpoint_id": "checkpoint-0", "protocol_ordinal": 1, "reviewed_protocol_count": 0},
        "sources": sources,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"evidence_id": "example:evidence:run"}), encoding="utf-8")
    return manifest_path, source_root, groundtruth_root, run_root


def _build(tmp_path: Path, *, phase: str, evidence: Path | None = None):
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    return build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / f"{phase}-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase=phase,
        evidence_artifact_path=evidence,
    )


def test_evidence_packet_is_primary_only(tmp_path: Path) -> None:
    result = _build(tmp_path, phase="evidence")
    packet = json.loads(result.packet_path.read_text())
    assert "schema_version" not in packet
    assert {item["role"] for item in packet["files"]} == {"primary_evidence"}
    assert not (result.output_dir / "legacy_curated_html").exists()
    assert not (result.output_dir / "current_benchmark_record").exists()


def test_comparison_freezes_evidence_and_includes_all_comparison_inputs(tmp_path: Path) -> None:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    evidence = tmp_path / "evidence.json"
    result = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / "comparison-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase="comparison",
        evidence_artifact_path=evidence,
    )
    packet = json.loads(result.packet_path.read_text())
    assert {item["role"] for item in packet["files"]} == {
        "legacy_curated_html", "current_benchmark_record", "benchmark_run_artifact"
    }
    assert "primary_evidence" not in {item["role"] for item in packet["files"]}
    assert (result.output_dir / packet["frozen_evidence"]["path"]).read_bytes() == evidence.read_bytes()
    projected_tsv = next(item for item in packet["files"] if item["source_kind"] == "oligo_tsv_baseline")
    assert "other" not in (result.output_dir / projected_tsv["path"]).read_text()
    assert "source_row_number" in (result.output_dir / projected_tsv["path"]).read_text()


def test_comparison_requires_frozen_evidence(tmp_path: Path) -> None:
    with pytest.raises(PacketError, match="requires --evidence-artifact"):
        _build(tmp_path, phase="comparison")


def test_packet_rejects_stale_source_hash(tmp_path: Path) -> None:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    (source_root / "protocols/example/paper.txt").write_text("changed")
    with pytest.raises(PacketError, match="stale hash"):
        build_phase_packet(
            manifest_path=manifest,
            source_dataset_dir=source_root,
            groundtruth_dataset_dir=groundtruth_root,
            run_artifact_dir=run_root,
            output_dir=tmp_path / "packet",
            manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
            packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
            phase="evidence",
        )
