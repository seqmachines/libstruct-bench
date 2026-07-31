from __future__ import annotations

import json
from pathlib import Path

import pytest

from libstruct_bench.audit.inventory import (
    DatasetReference,
    InventoryError,
    build_inventory,
    sha256_file,
)
from libstruct_bench.cli.build_audit_inventory import main


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "audit" / "audit_input_manifest.v1.schema.json"
NOW = "2026-07-30T12:00:00Z"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _corpus(tmp_path: Path, *, html_reference: str = "Example.html") -> tuple[Path, Path]:
    protocols_dir = tmp_path / "protocols"
    html_dir = tmp_path / "html"
    protocol_dir = protocols_dir / "example_protocol"
    protocol_dir.mkdir(parents=True)
    html_dir.mkdir()
    (protocol_dir / "paper.pdf").write_bytes(b"%PDF-primary")
    _write_json(
        protocol_dir / "groundtruth_final_lib_struct.json",
        {
            "schema_version": "example",
            "protocol_id": "example_protocol",
            "source_html_file": html_reference,
            "libraries": [],
        },
    )
    _write_json(
        protocol_dir / "groundtruth_oligos.json",
        {
            "schema_version": "example",
            "protocol_id": "example_protocol",
            "oligos": [],
        },
    )
    (html_dir / "Example.html").write_text("<html>legacy</html>", encoding="utf-8")
    return protocols_dir, html_dir


def _build(tmp_path: Path, protocols_dir: Path, html_dir: Path, **kwargs):
    return build_inventory(
        protocols_dir=protocols_dir,
        html_dir=html_dir,
        output_dir=tmp_path / "out",
        schema_path=SCHEMA_PATH,
        created_at=NOW,
        **kwargs,
    )


def test_builds_manifest_with_three_separate_source_roles(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path)
    result = _build(
        tmp_path,
        protocols_dir,
        html_dir,
        protocol_dataset=DatasetReference("protocol-repo", "a" * 40),
        groundtruth_dataset=DatasetReference("groundtruth-repo", "b" * 40),
        html_dataset=DatasetReference("html-repo", "c" * 40),
    )

    assert result.ready_count == 1
    assert result.blocked_count == 0
    manifest_path = result.manifest_dir / "example_protocol.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    roles = [source["role"] for source in manifest["sources"]]
    assert roles == [
        "primary_evidence",
        "legacy_curated_html",
        "current_benchmark_record",
        "current_benchmark_record",
    ]
    assert manifest["sources"][0]["path"] == "protocols/example_protocol/paper.pdf"
    assert manifest["sources"][1]["path"] == "legacy/scg_html/Example.html"
    assert manifest["sources"][0]["sha256"] == sha256_file(
        protocols_dir / "example_protocol" / "paper.pdf"
    )
    assert all(not source["path"].startswith("/") for source in manifest["sources"])


def test_pdf_legacy_reference_blocks_manifest_and_is_reported(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path, html_reference="Example.pdf")
    result = _build(tmp_path, protocols_dir, html_dir)

    assert result.ready_count == 0
    assert result.blocked_count == 1
    assert not (result.manifest_dir / "example_protocol.json").exists()
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    codes = {finding["code"] for finding in report["protocols"][0]["findings"]}
    assert "invalid_legacy_html_reference" in codes
    assert "missing_legacy_html_mapping" in codes


def test_explicit_html_map_resolves_invalid_embedded_reference(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path, html_reference="Example.pdf")
    html_map = tmp_path / "html-map.json"
    _write_json(
        html_map,
        {
            "schema_version": "libstruct.legacy_html_map.v1",
            "protocols": {"example_protocol": ["Example.html"]},
        },
    )

    result = _build(
        tmp_path,
        protocols_dir,
        html_dir,
        html_map_path=html_map,
    )

    assert result.ready_count == 1
    assert result.blocked_count == 0
    manifest = json.loads(
        (result.manifest_dir / "example_protocol.json").read_text(encoding="utf-8")
    )
    assert "reviewed inventory override" in manifest["notes"]
    assert sha256_file(html_map) in manifest["notes"]
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["configuration"]["html_map_sha256"] == sha256_file(html_map)
    assert (
        report["protocols"][0]["legacy_html_mapping_source"] == "reviewed_override"
    )


def test_fixed_timestamp_produces_identical_manifest(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path)
    first = build_inventory(
        protocols_dir=protocols_dir,
        html_dir=html_dir,
        output_dir=tmp_path / "first",
        schema_path=SCHEMA_PATH,
        created_at=NOW,
    )
    second = build_inventory(
        protocols_dir=protocols_dir,
        html_dir=html_dir,
        output_dir=tmp_path / "second",
        schema_path=SCHEMA_PATH,
        created_at=NOW,
    )

    assert (first.manifest_dir / "example_protocol.json").read_bytes() == (
        second.manifest_dir / "example_protocol.json"
    ).read_bytes()


def test_groundtruth_json_is_not_classified_as_primary_evidence(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path)
    result = _build(tmp_path, protocols_dir, html_dir)
    manifest = json.loads(
        (result.manifest_dir / "example_protocol.json").read_text(encoding="utf-8")
    )

    primary = [
        source for source in manifest["sources"] if source["role"] == "primary_evidence"
    ]
    assert [source["title"] for source in primary] == ["paper.pdf"]


def test_nonempty_output_requires_force(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "keep.txt").write_text("user data", encoding="utf-8")

    with pytest.raises(InventoryError, match="not empty"):
        build_inventory(
            protocols_dir=protocols_dir,
            html_dir=html_dir,
            output_dir=output_dir,
            schema_path=SCHEMA_PATH,
            created_at=NOW,
        )


def test_output_inside_code_repository_is_rejected(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path)

    with pytest.raises(InventoryError, match="private audit-data"):
        build_inventory(
            protocols_dir=protocols_dir,
            html_dir=html_dir,
            output_dir=REPO_ROOT / "generated-private-inventory",
            schema_path=SCHEMA_PATH,
            created_at=NOW,
        )


def test_cli_returns_one_when_a_protocol_is_blocked(tmp_path: Path) -> None:
    protocols_dir, html_dir = _corpus(tmp_path, html_reference="Example.pdf")
    exit_code = main(
        [
            "--protocols-dir",
            str(protocols_dir),
            "--html-dir",
            str(html_dir),
            "--out",
            str(tmp_path / "out"),
            "--schema",
            str(SCHEMA_PATH),
            "--created-at",
            NOW,
        ]
    )

    assert exit_code == 1
