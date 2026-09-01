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
    _write(
        t1,
        json.dumps(
            {
                "protocol_id": "example",
                "protocol_name": "Example",
                "libraries": [
                    {
                        "modality": "gene expression",
                        "library_sequence": "A",
                        "strand": "single",
                        "orientation": "5_to_3",
                        "support_status": "explicit",
                        "segments": [
                            {
                                "segment_id": "library-segment",
                                "kind": "constant",
                                "role": "adapter",
                                "sequence": "A",
                                "orientation": "5_to_3",
                                "support_status": "explicit",
                            }
                        ],
                    }
                ],
            }
        ).encode(),
    )
    _write(
        t2,
        json.dumps(
            {
                "protocol_id": "example",
                "protocol_name": "Example",
                "oligos": [],
            }
        ).encode(),
    )
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
    return manifest_path, source_root, groundtruth_root, run_root


def _build(tmp_path: Path):
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    return build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / "comparison-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase="comparison",
    )


def test_comparison_packet_contains_conversion_and_primary_inputs(tmp_path: Path) -> None:
    result = _build(tmp_path)
    packet = json.loads(result.packet_path.read_text())
    assert "schema_version" not in packet
    assert {item["role"] for item in packet["files"]} == {
        "primary_evidence",
        "legacy_curated_html",
        "current_benchmark_record",
        "benchmark_run_artifact",
    }
    roles = [item["role"] for item in packet["files"]]
    assert roles[0] == "legacy_curated_html"
    assert roles.index("primary_evidence") > max(
        index
        for index, role in enumerate(roles)
        if role == "current_benchmark_record"
    )
    assert "frozen_evidence" not in packet
    projected_tsv = next(item for item in packet["files"] if item["source_kind"] == "oligo_tsv_baseline")
    assert "other" not in (result.output_dir / projected_tsv["path"]).read_text()
    assert "source_row_number" in (result.output_dir / projected_tsv["path"]).read_text()


def test_comparison_packet_can_share_dataset_root_with_oligo_tsv(tmp_path: Path) -> None:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    output_dir = groundtruth_root / "ground_truth_audit/packets/example/comparison"

    result = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=output_dir,
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase="comparison",
    )

    assert result.output_dir == output_dir
    assert (output_dir / "packet.json").is_file()


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
            phase="comparison",
        )


def _conversion_artifact() -> dict:
    return {
        "conversion_id": "example:legacy-conversion:conversion-001",
        "protocol_id": "example",
        "packet_sha256": "1" * 64,
        "input_manifest_sha256": "2" * 64,
        "status": "unapproved_legacy_conversion",
        "run": {
            "run_id": "conversion-001",
            "agent": "claude-code",
            "provider": "anthropic",
            "model": "claude-opus-5",
            "tool_version": "2.1.0",
            "harness_version": "test",
            "review_mode": "primary",
            "started_at": "2026-08-01T12:00:00Z",
            "completed_at": "2026-08-01T12:01:00Z",
            "prompt_sha256": "3" * 64,
            "skill_sha256": "4" * 64,
            "policy_sha256": "5" * 64,
            "schema_sha256": "6" * 64,
            "skills": ["audit-protocol"],
            "tools": ["Read"],
            "permission_mode": "plan",
            "checkpoint_id": "checkpoint-0",
        },
        "candidates": {"T1": {}, "T2": {}, "T3": {}},
        "candidate_sha256s": {
            "T1": "7" * 64,
            "T2": "8" * 64,
            "T3": "9" * 64,
        },
        "lineage": [
            {"task": task, "source_ids": ["legacy:html"], "summary": "converted"}
            for task in ("T1", "T2", "T3")
        ],
        "notes": [],
    }


def test_legacy_conversion_packet_excludes_primary_and_run_inputs(
    tmp_path: Path,
) -> None:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    result = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / "legacy-conversion-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase="legacy_conversion",
    )

    packet = json.loads(result.packet_path.read_text(encoding="utf-8"))
    assert packet["phase"] == "legacy_conversion"
    assert {item["role"] for item in packet["files"]} == {
        "legacy_curated_html",
        "current_benchmark_record",
    }
    assert "renditions" not in packet


def test_comparison_packet_hash_pins_staged_legacy_conversion(
    tmp_path: Path,
) -> None:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    conversion_path = tmp_path / "conversion.json"
    conversion_path.write_text(json.dumps(_conversion_artifact()), encoding="utf-8")
    result = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / "comparison-with-conversion",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        legacy_conversion_path=conversion_path,
        legacy_conversion_schema_path=AUDIT_SCHEMAS
        / "legacy_conversion.schema.json",
        phase="comparison",
    )

    packet = json.loads(result.packet_path.read_text(encoding="utf-8"))
    staged = next(
        item
        for item in packet["files"]
        if item["role"] == "legacy_conversion_candidate"
    )
    assert staged["source_id"] == _conversion_artifact()["conversion_id"]
    assert hashlib.sha256(conversion_path.read_bytes()).hexdigest() == staged["sha256"]
    assert (result.output_dir / staged["path"]).read_bytes() == conversion_path.read_bytes()
