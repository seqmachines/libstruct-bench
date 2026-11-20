"""Prepare both public task layouts and exercise the generated verifier entry points."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from libstruct_bench import paper_groundtruth
from libstruct_bench.cli.generate_libgen_tasks import main as build_workflow
from libstruct_bench.cli.generate_library_v0_tasks import main as build_final_library
from tests.libgen_fixtures import t1_groundtruth, t2_prediction, t3_prediction
from tests.test_generate_libgen_tasks import _fixture_release


@pytest.mark.parametrize("layout", ["task1", "tasks2_3"])
@pytest.mark.parametrize("selection", ["inventory", "documents", "bundle", "bundle-with-truth"])
def test_scored_local_harbor_task(tmp_path, layout, selection, capsys):
    local_documents = selection != "inventory"
    linked_inventory, sources, truth = _fixture_release(tmp_path)
    protocol = "example_protocol"
    out = tmp_path / "prepared"
    args = [
        "--protocol-id",
        protocol,
        "--source-root",
        str(sources),
        "--groundtruth-root",
        str(truth),
        "--out",
        str(out),
    ]
    if layout == "task1":
        inventory = tmp_path / "final-library.json"
        inventory.write_text(
            json.dumps(
                {
                    "protocols": [
                        {
                            "protocol_id": protocol,
                            "input_paths": [f"{protocol}/source.pdf"],
                        },
                        {
                            "protocol_id": "unselected",
                            "input_path": "unselected/manual.pdf",
                        },
                    ]
                }
            )
        )
        build = build_final_library
        predictions = {"prediction.json": t1_groundtruth()}
    else:
        inventory = linked_inventory
        build = build_workflow
        predictions = {
            "t2_prediction.json": t2_prediction(),
            "t3_prediction.json": t3_prediction(),
        }
    if local_documents:
        supplement = sources / protocol / "supplement.tsv"
        supplement.write_text("synthetic supplement")
        args += ["--document", "source.pdf", "--document", supplement.name]
        if layout == "tasks2_3":
            predictions = {
                "oligo_family_prediction.json": t2_prediction(),
                "molecular_workflow_prediction.json": t3_prediction(),
            }
    else:
        args += ["--protocols", str(inventory)]
    if selection.startswith("bundle"):
        folder = sources / protocol
        (folder / "SOURCE_MANIFEST.tsv").touch()
        (folder / "~$unopened.xlsx").touch()
        (folder / ".metadata.txt").touch()
        args = [str(folder), "--out", str(out)]
        if selection == "bundle":
            truth.rename(tmp_path / "ground_truth")
        else:
            references = tmp_path / "private references"
            (truth / protocol).rename(references)
            args += ["--truth", str(references)]
    assert build(args) == 0
    if selection.startswith("bundle"):
        assert capsys.readouterr().out.strip() == str((out / protocol).resolve())

    task = out / protocol
    env = task / "environment"
    verifier = task / "tests"
    bundle = json.loads((env / "input_manifest.json").read_text())
    documents = bundle["sources"]
    expected_names = {"source.pdf", "supplement.tsv"} if local_documents else {"source.pdf"}
    assert {doc["local_path"] for doc in documents} == expected_names
    for doc in documents:
        source = (sources / doc["path"]).read_bytes()
        assert (env / "protocol_sources" / doc["local_path"]).read_bytes() == source
        assert hashlib.sha256(source).hexdigest() == doc["sha256"]
    instruction = (task / "instruction.md").read_text()
    name = "Final library structure extraction" if layout == "task1" else "Molecular workflow reconstruction"
    assert instruction.startswith("# " + name)
    shared = Path(__file__).resolve().parents[1] / "benchmarks/shared/protocol_processing_rules.md"
    assert (task / shared.name).read_bytes() == (env / shared.name).read_bytes() == shared.read_bytes()
    assert shared.name in instruction
    assert f"COPY {shared.name} /workspace/{shared.name}" in (env / "Dockerfile").read_text()
    if layout == "tasks2_3":
        assert "/workspace/rules.md" in instruction
        assert (env / "rules.md").read_bytes() == (task / "rules.md").read_bytes()
    if local_documents:
        rules = "protocol_processing_rules.md" if layout == "task1" else "rules.md"
        assert not re.search(r"\b(?:T[123]|Tasks? [123])\b", instruction + (task / rules).read_text())
    assert list(out.iterdir()) == [task]
    assert not list(env.rglob("groundtruth*.json"))
    assert not (verifier / "libstruct_bench/archive").exists()
    assert not (verifier / "libstruct_bench/improvement").exists()
    assert "HF_TOKEN" not in (task / "task.toml").read_text()
    schemas = {p.name for p in (env / "schemas/benchmark").iterdir()}
    if layout == "task1":
        assert '"schema_version"' not in instruction
        assert "fetch_input.py" not in (task / "instruction.md").read_text()
        assert schemas == {"final_library_structure_prediction.schema.json"}
    else:
        assert schemas == {"oligo_prediction.schema.json", "library_generation_workflow_prediction.schema.json"}

    logs = tmp_path / "logs"
    artifacts = logs / "artifacts"
    artifacts.mkdir(parents=True)
    for filename, doc in predictions.items():
        (artifacts / filename).write_text(json.dumps(doc))
    # Run the same shell entry point with its container mount paths mapped to this fixture.
    script = (
        (verifier / "test.sh")
        .read_text()
        .replace("/logs/", f"{logs}/")
        .replace("/tests/", f"{verifier}/")
    )
    assert "--groundtruth-repo" not in script
    subprocess.run(
        ["bash", "-e"],
        input=script,
        text=True,
        check=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        },
    )
    metrics = json.loads((logs / "verifier/reward.json").read_text())
    assert metrics["reward"] == pytest.approx(1)
    if layout == "task1":
        assert metrics["exact_match"] == 1
    else:
        assert json.loads((logs / "verifier/details.json").read_text())["benchmark_version"] == "4.7.0"

    # A failed rebuild must preserve the previously usable task.
    before = (task / "task.toml").read_bytes()
    (sources / protocol / "source.pdf").unlink()
    if selection.startswith("bundle"):
        (sources / protocol / "supplement.tsv").unlink()
    with pytest.raises((ValueError, FileNotFoundError, SystemExit)):
        build([*args, "--force"])
    assert (task / "task.toml").read_bytes() == before
    assert (env / "protocol_sources" / "source.pdf").is_file()


@pytest.fixture
def paper_release(tmp_path, monkeypatch):
    _, sources, truth = _fixture_release(tmp_path)
    protocol = "example_protocol"
    files = {p.name: p.read_bytes() for p in (truth / protocol).iterdir()}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "panels": [{"protocol_id": protocol, "panel": "fig3a"}],
        "inputs": {f"external/audit-data/ground_truth/{protocol}/{k}": hashlib.sha256(v).hexdigest()
                   for k, v in files.items()},
    }))
    monkeypatch.setattr(paper_groundtruth, "PAPER_MANIFEST", manifest)
    requests = []

    def download(*, repo_id, revision, path):
        assert (repo_id, revision) == (paper_groundtruth.REPOSITORY, paper_groundtruth.REVISION)
        folder, filename = path.split("/")
        assert folder == protocol
        requests.append(filename)
        return files[filename]

    monkeypatch.setattr(paper_groundtruth, "download_hf_dataset_file", download)
    return sources / protocol, files, requests


def test_prepare_with_paper_truth_fetches_only_missing_files(tmp_path, paper_release):
    bundle, files, requests = paper_release
    cache = tmp_path / "ground_truth" / bundle.name
    final = "groundtruth_final_lib_struct.json"
    assert build_final_library([str(bundle), "--paper-truth", "--out", str(tmp_path / "library")]) == 0
    assert requests == [final]
    assert {p.name for p in cache.iterdir()} == {final}
    assert (tmp_path / "library" / bundle.name / "tests/groundtruth.json").read_bytes() == files[final]

    out = tmp_path / "workflow"
    args = [str(bundle), "--paper-truth", "--truth", str(cache), "--out", str(out)]
    assert build_workflow(args) == 0
    assert sorted(requests) == sorted(files)
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == files
    assert not list((out / bundle.name / "environment").rglob("groundtruth*.json"))
    assert build_workflow([*args, "--force"]) == 0
    assert len(requests) == len(files)


def test_paper_truth_mismatch_preserves_existing_files(tmp_path, paper_release):
    bundle, files, requests = paper_release
    cache = tmp_path / "references"
    cache.mkdir()
    final = cache / "groundtruth_final_lib_struct.json"
    final.write_bytes(b"custom curation")
    with pytest.raises(ValueError, match="existing file does not match"):
        paper_groundtruth.setup_paper_truth(bundle.name, cache, tuple(files))
    assert final.read_bytes() == b"custom curation"
    assert list(cache.iterdir()) == [final]
    assert requests == []


def test_paper_truth_corrupt_download_can_be_retried(tmp_path, paper_release):
    bundle, files, requests = paper_release
    cache = tmp_path / "cache"
    names = tuple(sorted(files))
    original = files[names[-1]]
    files[names[-1]] = b"corrupt"
    with pytest.raises(ValueError, match="download does not match"):
        paper_groundtruth.setup_paper_truth(bundle.name, cache, names)
    assert not list(cache.iterdir())
    files[names[-1]] = original
    paper_groundtruth.setup_paper_truth(bundle.name, cache, names)
    assert (cache / names[-1]).read_bytes() == original
    assert requests == list(names) * 2


def test_paper_truth_rejects_unknown_protocol_and_overlapping_output(tmp_path, paper_release):
    bundle, files, requests = paper_release
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="outside the paper panel"):
        paper_groundtruth.setup_paper_truth("unknown", cache, tuple(files))
    with pytest.raises(SystemExit):
        build_final_library([str(bundle), "--paper-truth", "--truth", str(cache / bundle.name), "--out", str(cache)])
    assert not cache.exists()
    assert requests == []


@pytest.mark.parametrize("filename", ["../outside.pdf", "groundtruth.tsv", "link.pdf", "unknown.json"])
def test_explicit_documents_reject_non_evidence(tmp_path, filename):
    from libstruct_bench.harbor_task import document_inventory

    folder = tmp_path / "example"
    folder.mkdir()
    (tmp_path / "outside.pdf").write_text("private fixture")
    (folder / "link.pdf").symlink_to(tmp_path / "outside.pdf")
    (folder / "groundtruth.tsv").touch()
    (folder / "unknown.json").touch()
    with pytest.raises(ValueError, match="primary document"):
        document_inventory(tmp_path, ["example"], [filename])
