from __future__ import annotations

import json
from pathlib import Path

import pytest

from libstruct_bench.audit.inventory import build_inventory
from libstruct_bench.audit.packets import PacketError, build_packet
from libstruct_bench.cli.prepare_audit_packet import main


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA = (
    REPO_ROOT / "schemas" / "audit" / "audit_input_manifest.v1.schema.json"
)
PACKET_SCHEMA = REPO_ROOT / "schemas" / "audit" / "audit_packet.v1.schema.json"
NOW = "2026-07-30T12:00:00Z"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _manifest(tmp_path: Path) -> tuple[Path, Path, Path]:
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
            "source_html_file": "Example.html",
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

    inventory = build_inventory(
        protocols_dir=protocols_dir,
        html_dir=html_dir,
        output_dir=tmp_path / "inventory",
        schema_path=MANIFEST_SCHEMA,
        created_at=NOW,
    )
    return (
        inventory.manifest_dir / "example_protocol.json",
        protocols_dir,
        html_dir,
    )


def _build_packet(
    tmp_path: Path,
    manifest_path: Path,
    protocols_dir: Path,
    html_dir: Path,
    *,
    output_name: str = "packet",
):
    return build_packet(
        manifest_path=manifest_path,
        protocols_dir=protocols_dir,
        html_dir=html_dir,
        output_dir=tmp_path / output_name,
        manifest_schema_path=MANIFEST_SCHEMA,
        packet_schema_path=PACKET_SCHEMA,
    )


def test_builds_verified_role_separated_packet(tmp_path: Path) -> None:
    manifest_path, protocols_dir, html_dir = _manifest(tmp_path)
    source_mode = (protocols_dir / "example_protocol" / "paper.pdf").stat().st_mode

    result = _build_packet(
        tmp_path,
        manifest_path,
        protocols_dir,
        html_dir,
    )

    assert result.file_count == 4
    packet = json.loads(result.packet_path.read_text(encoding="utf-8"))
    assert packet["schema_version"] == "libstruct.audit_packet.v1"
    assert [entry["role"] for entry in packet["files"]] == [
        "primary_evidence",
        "legacy_curated_html",
        "current_benchmark_record",
        "current_benchmark_record",
    ]
    for entry in packet["files"]:
        materialized = result.output_dir / entry["path"]
        assert materialized.is_file()
        assert materialized.stat().st_mode & 0o222 == 0
    assert (protocols_dir / "example_protocol" / "paper.pdf").stat().st_mode == source_mode


def test_rejects_stale_source_hash_without_creating_packet(tmp_path: Path) -> None:
    manifest_path, protocols_dir, html_dir = _manifest(tmp_path)
    (protocols_dir / "example_protocol" / "paper.pdf").write_bytes(b"changed")

    with pytest.raises(PacketError, match="stale hash"):
        _build_packet(
            tmp_path,
            manifest_path,
            protocols_dir,
            html_dir,
        )

    assert not (tmp_path / "packet").exists()


def test_rejects_path_traversal_in_manifest(tmp_path: Path) -> None:
    manifest_path, protocols_dir, html_dir = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["path"] = (
        "protocols/example_protocol/../example_protocol/paper.pdf"
    )
    unsafe_manifest = tmp_path / "unsafe-manifest.json"
    _write_json(unsafe_manifest, manifest)

    with pytest.raises(PacketError, match="unsafe portable source path"):
        _build_packet(
            tmp_path,
            unsafe_manifest,
            protocols_dir,
            html_dir,
        )


def test_same_manifest_produces_identical_packet_files(tmp_path: Path) -> None:
    manifest_path, protocols_dir, html_dir = _manifest(tmp_path)
    first = _build_packet(
        tmp_path,
        manifest_path,
        protocols_dir,
        html_dir,
        output_name="first-packet",
    )
    second = _build_packet(
        tmp_path,
        manifest_path,
        protocols_dir,
        html_dir,
        output_name="second-packet",
    )

    first_files = {
        path.relative_to(first.output_dir): path.read_bytes()
        for path in first.output_dir.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second.output_dir): path.read_bytes()
        for path in second.output_dir.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files


def test_cli_materializes_packet(tmp_path: Path) -> None:
    manifest_path, protocols_dir, html_dir = _manifest(tmp_path)
    output_dir = tmp_path / "cli-packet"

    exit_code = main(
        [
            "--manifest",
            str(manifest_path),
            "--protocols-dir",
            str(protocols_dir),
            "--html-dir",
            str(html_dir),
            "--out",
            str(output_dir),
            "--manifest-schema",
            str(MANIFEST_SCHEMA),
            "--packet-schema",
            str(PACKET_SCHEMA),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "packet.json").is_file()
