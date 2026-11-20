from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.source_catalog import (
    SourceCatalogError,
    build_manifests_from_catalog,
    build_source_catalog,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_SCHEMA = REPO_ROOT / "schemas" / "audit" / "source_catalog.schema.json"
MANIFEST_SCHEMA = (
    REPO_ROOT / "schemas" / "audit" / "audit_input_manifest.schema.json"
)
NOW = "2026-08-01T12:00:00Z"
SOURCE_REVISION = "a" * 40
GROUNDTRUTH_REVISION = "b" * 40


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    protocols = tmp_path / "protocols"
    protocol = protocols / "example_protocol"
    baselines = tmp_path / "ground_truth_audit" / "baselines"
    baseline = baselines / "example_protocol"
    html = tmp_path / "scg_html"
    protocol.mkdir(parents=True)
    baseline.mkdir(parents=True)
    html.mkdir()
    (protocol / "paper.pdf").write_bytes(b"%PDF primary")
    _write_json(
        baseline / "groundtruth_final_lib_struct.json",
        {
            "protocol_id": "example_protocol",
            "source_html_file": "Example.html",
            "libraries": [],
        },
    )
    _write_json(
        baseline / "groundtruth_oligos.json",
        {"protocol_id": "example_protocol", "oligos": []},
    )
    _write_json(
        baseline / "groundtruth_library_generation_workflow.json",
        {"protocol_id": "example_protocol", "workflows": []},
    )
    (html / "Example.html").write_text(
        '<html><img src="missing/diagram.png"></html>', encoding="utf-8"
    )
    ledger = protocols / "SOURCE_MANIFEST.tsv"
    with ledger.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["local_file", "kind", "title", "sha256", "notes"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(
            {
                "local_file": "example_protocol/paper.pdf",
                "kind": "paper",
                "title": "Primary paper",
                "sha256": "f" * 64,
                "notes": "",
            }
        )
        writer.writerow(
            {
                "local_file": "example_protocol/missing-supplement.pdf",
                "kind": "supplement",
                "title": "Expected supplement",
                "sha256": "e" * 64,
                "notes": "Not present in snapshot",
            }
        )
    return protocols, baselines, html, ledger


def _build_catalog(
    tmp_path: Path,
    *,
    output_name: str,
    previous: Path | None = None,
) -> Path:
    protocols, baselines, html, ledger = _fixture(tmp_path) if not (tmp_path / "protocols").exists() else (
        tmp_path / "protocols",
        tmp_path / "ground_truth_audit" / "baselines",
        tmp_path / "scg_html",
        tmp_path / "protocols" / "SOURCE_MANIFEST.tsv",
    )
    result = build_source_catalog(
        protocols_dir=protocols,
        baseline_dir=baselines,
        html_dir=html,
        output_path=tmp_path / output_name,
        schema_path=CATALOG_SCHEMA,
        source_repository="seqmachines/all-protocol-sources",
        source_revision=SOURCE_REVISION,
        groundtruth_repository="seqmachines/libstruct-groundtruth-audit",
        groundtruth_revision=GROUNDTRUTH_REVISION,
        source_manifest_tsv=ledger,
        previous_catalog_path=previous,
        created_at=NOW,
    )
    return result.catalog_path


def test_catalog_preserves_discovered_expected_and_missing_sources(tmp_path: Path) -> None:
    catalog_path = _build_catalog(tmp_path, output_name="catalog.json")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    sources = catalog["protocols"][0]["sources"]

    by_path = {source["path"]: source for source in sources}
    assert all(
        source["approval_status"]
        == ("included" if "sha256" in source else "unavailable")
        for source in sources
    )
    assert by_path["protocols/example_protocol/paper.pdf"]["source_kind"] == "original_paper"
    assert by_path["protocols/example_protocol/paper.pdf"]["integrity_status"] == "mismatch"
    assert by_path["protocols/example_protocol/missing-supplement.pdf"]["source_kind"] == "supplementary_methods"
    assert "sha256" not in by_path["protocols/example_protocol/missing-supplement.pdf"]
    assert by_path["protocols/example_protocol/missing-supplement.pdf"]["integrity_status"] == "missing"
    prefix = "ground_truth_audit/baselines/example_protocol"
    assert f"{prefix}/groundtruth_final_lib_struct.json" in by_path
    assert by_path[
        f"{prefix}/groundtruth_final_lib_struct.json"
    ]["task_relevance"] == ["T1"]
    assert by_path[
        f"{prefix}/groundtruth_oligos.json"
    ]["task_relevance"] == ["T2"]
    assert by_path[
        f"{prefix}/groundtruth_library_generation_workflow.json"
    ]["source_kind"] == "current_t3"
    assert by_path[
        f"{prefix}/groundtruth_library_generation_workflow.json"
    ]["task_relevance"] == ["T3"]
    assert "scg_html/missing/diagram.png" in by_path


def test_manifest_automatically_resolves_archived_pending_catalog(tmp_path: Path) -> None:
    catalog_path = _build_catalog(tmp_path, output_name="catalog.json")
    archived = json.loads(catalog_path.read_text(encoding="utf-8"))
    for protocol in archived["protocols"]:
        for source in protocol["sources"]:
            source["approval_status"] = "pending"
            source.pop("review", None)
    archived_path = tmp_path / "archived-pending.json"
    _write_json(archived_path, archived)

    ready = build_manifests_from_catalog(
        catalog_path=archived_path,
        output_dir=tmp_path / "ready-manifests",
        catalog_schema_path=CATALOG_SCHEMA,
        manifest_schema_path=MANIFEST_SCHEMA,
        checkpoint_id="pilot-0",
        reviewed_protocol_count=0,
        created_at=NOW,
    )
    assert ready.ready_count == 1
    manifest = json.loads(
        (ready.manifest_dir / "example_protocol.json").read_text(encoding="utf-8")
    )
    unavailable = [
        source for source in manifest["sources"] if source["approval_status"] == "unavailable"
    ]
    assert unavailable
    assert all("dataset_reference" not in source for source in unavailable)
    assert all(source["approval_status"] != "pending" for source in manifest["sources"])
    assert all("review" not in source for source in manifest["sources"])


def test_changed_available_source_remains_automatically_included(tmp_path: Path) -> None:
    catalog_path = _build_catalog(tmp_path, output_name="catalog.json")
    (tmp_path / "protocols" / "example_protocol" / "paper.pdf").write_bytes(
        b"%PDF changed"
    )

    refreshed = _build_catalog(
        tmp_path,
        output_name="refreshed.json",
        previous=catalog_path,
    )
    document = json.loads(refreshed.read_text(encoding="utf-8"))
    paper = next(
        source
        for source in document["protocols"][0]["sources"]
        if source["path"] == "protocols/example_protocol/paper.pdf"
    )
    assert paper["approval_status"] == "included"
    assert "review" not in paper


def test_catalog_requires_immutable_hugging_face_revisions(tmp_path: Path) -> None:
    protocols, baselines, html, ledger = _fixture(tmp_path)
    with pytest.raises(SourceCatalogError, match="immutable"):
        build_source_catalog(
            protocols_dir=protocols,
            baseline_dir=baselines,
            html_dir=html,
            output_path=tmp_path / "catalog.json",
            schema_path=CATALOG_SCHEMA,
            source_repository="seqmachines/all-protocol-sources",
            source_revision="main",
            groundtruth_repository="seqmachines/libstruct-groundtruth-audit",
            groundtruth_revision=GROUNDTRUTH_REVISION,
            source_manifest_tsv=ledger,
            created_at=NOW,
        )


def test_global_oligo_tsv_is_cataloged_with_a_protocol_row_filter(
    tmp_path: Path,
) -> None:
    protocols, baselines, html, ledger = _fixture(tmp_path)
    oligo_tsv = tmp_path / "groundtruth_oligos.tsv"
    oligo_tsv.write_text(
        "protocol_id\toligo_name\nexample_protocol\tPrimer\nother\tOther\n",
        encoding="utf-8",
    )
    result = build_source_catalog(
        protocols_dir=protocols,
        baseline_dir=baselines,
        html_dir=html,
        output_path=tmp_path / "catalog.json",
        schema_path=CATALOG_SCHEMA,
        source_repository="seqmachines/all-protocol-sources",
        source_revision=SOURCE_REVISION,
        groundtruth_repository="seqmachines/libstruct-groundtruth-audit",
        groundtruth_revision=GROUNDTRUTH_REVISION,
        source_manifest_tsv=ledger,
        oligo_tsv_path=oligo_tsv,
        created_at=NOW,
    )
    document = json.loads(result.catalog_path.read_text(encoding="utf-8"))
    source = next(
        item
        for item in document["protocols"][0]["sources"]
        if item["source_kind"] == "oligo_tsv_baseline"
    )
    assert source["task_relevance"] == ["T2"]
    assert source["row_filter"] == {
        "column": "protocol_id",
        "value": "example_protocol",
        "include_source_row_number": True,
    }
    assert source["path"] == "groundtruth_oligos.tsv"


def test_catalog_dataset_prefixes_are_configurable(tmp_path: Path) -> None:
    protocols, baselines, html, ledger = _fixture(tmp_path)
    result = build_source_catalog(
        protocols_dir=protocols,
        baseline_dir=baselines,
        html_dir=html,
        output_path=tmp_path / "catalog.json",
        schema_path=CATALOG_SCHEMA,
        source_repository="seqmachines/all-protocol-sources",
        source_revision=SOURCE_REVISION,
        groundtruth_repository="seqmachines/libstruct-groundtruth-audit",
        groundtruth_revision=GROUNDTRUTH_REVISION,
        source_manifest_tsv=ledger,
        source_protocols_prefix="",
        groundtruth_protocols_prefix="",
        html_prefix="legacy",
        created_at=NOW,
    )
    document = json.loads(result.catalog_path.read_text(encoding="utf-8"))
    paths = {
        source["path"] for source in document["protocols"][0]["sources"]
    }
    assert "example_protocol/paper.pdf" in paths
    assert "example_protocol/groundtruth_oligos.json" in paths
    assert "legacy/Example.html" in paths


def test_catalog_rejects_unsafe_dataset_prefix(tmp_path: Path) -> None:
    protocols, baselines, html, ledger = _fixture(tmp_path)
    with pytest.raises(SourceCatalogError, match="unsafe"):
        build_source_catalog(
            protocols_dir=protocols,
            baseline_dir=baselines,
            html_dir=html,
            output_path=tmp_path / "catalog.json",
            schema_path=CATALOG_SCHEMA,
            source_repository="seqmachines/all-protocol-sources",
            source_revision=SOURCE_REVISION,
            groundtruth_repository="seqmachines/libstruct-groundtruth-audit",
            groundtruth_revision=GROUNDTRUTH_REVISION,
            source_manifest_tsv=ledger,
            source_protocols_prefix="../protocols",
            created_at=NOW,
        )


def test_catalog_supports_content_addressed_local_snapshots(tmp_path: Path) -> None:
    protocols, baselines, html, ledger = _fixture(tmp_path)
    result = build_source_catalog(
        protocols_dir=protocols,
        baseline_dir=baselines,
        html_dir=html,
        output_path=tmp_path / "catalog.json",
        schema_path=CATALOG_SCHEMA,
        local_snapshots=True,
        source_manifest_tsv=ledger,
        created_at=NOW,
    )
    document = json.loads(result.catalog_path.read_text(encoding="utf-8"))
    assert {dataset["provider"] for dataset in document["datasets"]} == {
        "local_fixture"
    }
    assert all(
        len(dataset["revision"]) == 64 for dataset in document["datasets"]
    )
    assert {dataset["repository"] for dataset in document["datasets"]} == {
        "local/protocol_sources",
        "local/benchmark_baselines",
    }


def test_catalog_rejects_ground_truth_json_inside_protocol_sources(
    tmp_path: Path,
) -> None:
    protocols, baselines, html, ledger = _fixture(tmp_path)
    _write_json(
        protocols / "example_protocol" / "groundtruth_oligos.json",
        {"protocol_id": "example_protocol", "oligos": []},
    )
    with pytest.raises(SourceCatalogError, match="--baseline-dir"):
        build_source_catalog(
            protocols_dir=protocols,
            baseline_dir=baselines,
            html_dir=html,
            output_path=tmp_path / "catalog.json",
            schema_path=CATALOG_SCHEMA,
            local_snapshots=True,
            source_manifest_tsv=ledger,
            created_at=NOW,
        )
