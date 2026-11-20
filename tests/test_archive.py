import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pytest

from libstruct_bench.archive import cli, runtime
from libstruct_bench.archive.catalog import LocalCatalog, MongoCatalog
from libstruct_bench.archive.discovery import Study
from libstruct_bench.archive.hooks import agent_env
from libstruct_bench.archive.pipeline import Archive
from libstruct_bench.archive.storage import (
    IntegrityError,
    LocalStore,
    R2Store,
    canonical,
    files,
    sha256,
)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runroot = repo / "runs"
    data = tmp_path / "private"
    state = tmp_path / "state"
    extra = tmp_path / "important_runs"
    for k, v in {
        "LSB_REPO_ROOT": repo,
        "LSB_RUNS_ROOT": runroot,
        "LIBSTRUCT_DATA_ROOT": data,
        "LSB_ARCHIVE_STATE": state,
        "LSB_IMPORTANT_RUNS": extra,
        "LSB_ARCHIVE_MODE": "local",
    }.items():
        monkeypatch.setenv(k, str(v))
    source = data / "protocols/test/source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-test")
    table = repo / "docs/release/source_inventory.csv"
    table.parent.mkdir(parents=True)
    table.write_text(
        "protocol_id,source_path,sha256,rights_class\ntest,test/source.pdf,"
        + sha256(source)
        + ",private\n"
    )
    job = runroot / "libgen/codex/base"
    trial = job / "test__abc"
    cfg = {
        "agent": {
            "name": "codex",
            "model_name": "requested",
            "kwargs": {"reasoning_effort": "high"},
        },
        "task": {"path": "benchmarks/tasks/test"},
        "job_id": "job-one",
    }
    put(job / "config.json", {"agents": [cfg["agent"]], "tasks": [cfg["task"]]})
    put(trial / "config.json", cfg)
    task = repo / cfg["task"]["path"]
    task.mkdir(parents=True)
    (task / "task.toml").write_text(
        '[metadata]\nprotocol_id="test"\ninput_revision="source-revision"\n'
    )
    put(
        task / "environment/input_manifest.json",
        {
            "protocol_id": "test",
            "sources": [{"path": "test/source.pdf", "sha256": sha256(source)}],
        },
    )
    from dirhash import dirhash

    put(
        trial / "result.json",
        {
            "id": "execution",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:01:00Z",
            "task_checksum": dirhash(task, "sha256"),
            "agent_info": {
                "version": "cli-1",
                "model_info": {"name": "observed", "version": "model-1"},
            },
            "agent_result": {"n_output_tokens": 10},
        },
    )
    put(trial / "verifier/details.json", {"benchmark_version": "4.5.0"})
    for v in ["4.6.0", "4.10.0"]:
        put(
            trial / "verifier/rescore" / ("libgen-" + v) / "details.json",
            {"benchmark_version": v},
        )
        put(
            trial / "verifier/rescore" / ("libgen-" + v) / "reward.json",
            {"reward": 0.75},
        )
    put(trial / "agent/auth.json", {"session": "must-stay-local"})
    put(
        trial / "agent/trajectory.json",
        {
            "steps": [
                {"output": "Authorization: Bearer phrase-never-transfer"},
                {"output": "ordinary event"},
            ]
        },
    )
    put(trial / "artifacts/t2_prediction.json", {"prediction": []})
    study = Study(repo, runroot, data)
    archive = Archive(
        study,
        state,
        LocalStore(state / "e"),
        LocalStore(state / "r"),
        LocalCatalog(state / "test.sqlite"),
    )
    return study, archive, trial


def test_local_roundtrip_and_latest(setup, tmp_path):
    study, archive, trial = setup
    before = {p: sha256(p) for p in files(study.runs)}
    m, ref = archive.ingest(trial)
    assert m["metadata"]["evaluation_schema_version"] == "4.10.0"
    assert m["metadata"]["duration_seconds"] == 60 and m["metadata"]["metrics"] == {
        "reward": 0.75
    }
    assert m["source_bundle"]["status"] == "verified" and m["metadata"]["seed"] is None
    assert (
        m["metadata"]["model_version"] == "model-1"
        and m["metadata"]["harness_version"] == "cli-1"
    )
    assert archive.ingest(trial)[1] == ref
    assert len(archive.catalog.find("snapshots", run_id=m["run_id"])) == 1
    out = tmp_path / "restore"
    archive.recover(m["run_id"], out)
    restored = out / m["trial_id"] / ref["sha256"]
    assert (restored / "protocols/test/source.pdf").read_bytes() == b"%PDF-test"
    assert not (restored / "trial/verifier/rescore/libgen-4.6.0").exists()
    assert not (restored / "trial/agent/auth.json").exists()
    trace = (restored / "trial/agent/trajectory.json").read_text()
    assert "phrase-never-transfer" not in trace and "ordinary event" in trace
    assert b"must-stay-local" not in b"".join(
        p.read_bytes() for p in files(archive.restricted.root)
    )
    assert {p: sha256(p) for p in files(study.runs)} == before
    assert all("protocols/" in k for k in archive.evidence.list())


def test_restore_and_database_failure(setup, tmp_path):
    _, archive, trial = setup
    with patch.object(archive.catalog, "register", side_effect=ConnectionError):
        with pytest.raises(ConnectionError):
            archive.ingest(trial)
    keys = set(archive.restricted.list())
    m, _ = archive.ingest(trial)
    assert keys.issubset(archive.restricted.list())
    item = next(a for a in m["artifacts"] if a["role"] == "output")
    archive.restricted.path(item["key"]).write_bytes(b"damaged")
    with pytest.raises(IntegrityError):
        archive.recover(m["run_id"], tmp_path / "restore")


def test_failed_incomplete_alias_and_source_drift(setup):
    study, archive, trial = setup
    import shutil

    copy = trial.parent / "test__copy"
    shutil.copytree(trial, copy)
    m, _ = archive.ingest(trial)
    duplicate, _ = archive.ingest(copy)
    assert duplicate["trial_id"] == m["trial_id"]
    retry = trial.parent / "test__retry"
    shutil.copytree(trial, retry)
    put(
        retry / "result.json",
        {
            "id": "new-attempt",
            "exception_info": {"exception_type": "AgentTimeoutError"},
        },
    )
    failed, _ = archive.ingest(retry)
    assert failed["trial_id"] != m["trial_id"] and failed["status"] == "failed"
    (retry / "result.json").unlink()
    (study.data / "protocols/test/source.pdf").write_bytes(b"new document")
    partial, _ = archive.ingest(retry)
    assert (
        partial["status"] == "partial" and partial["metadata"]["model_version"] is None
    )
    assert not partial["source_bundle"]["documents"][0]["available"]
    assert archive.ingest(retry)[0]["trial_id"] == partial["trial_id"]


def test_cli_inventory_bulk_and_no_writes_on_dry_run(setup, tmp_path, capsys):
    study, _, trial = setup
    important = (
        tmp_path / "important_runs/parser_control_text5/harbor/job/test__x/config.json"
    )
    put(important, json.loads((trial / "config.json").read_bytes()))
    (study.runs / "by_setting_view").symlink_to(trial.parent)
    inv = tmp_path / "inventory.json"
    assert cli.cli(["inventory", "--output", str(inv)]) == 0
    rows = json.loads(inv.read_bytes())["records"]
    assert sum(r["eligible"] for r in rows) == 1 and any(
        r["reason"] == "parser-control" for r in rows
    )
    assert cli.cli(["migrate", str(inv), "--dry-run"]) == 0
    assert not (tmp_path / "state/journal.sqlite").exists()
    assert cli.main(["migrate", str(inv), "--apply"]) == 1
    args = ["migrate", str(inv), "--apply", "--authorize-bulk", sha256(inv)]
    assert cli.cli(args) == 0 and cli.cli(args) == 0
    assert "already_registered" in capsys.readouterr().out
    with pytest.raises(ValueError):
        cli.cli(["inventory", "--output", str(trial / "new.json")])


def test_cloud_confirmation_and_isolation(setup, monkeypatch):
    study, _, _ = setup
    for k, v in {
        "LSB_ARCHIVE_MODE": "r2",
        "R2_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
        "MONGODB_URI": "mongodb://user:pass@host",
        "R2_EVIDENCE_BUCKET": "evidence",
        "R2_ARCHIVE_BUCKET": "restricted",
        "R2_SECRET_ACCESS_KEY": "fake",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LSB_CONFIRMED_TARGET", raising=False)
    assert (
        runtime.target()
        == "account.r2.cloudflarestorage.com/evidence/restricted/host/proofread"
    )
    with pytest.raises(ValueError, match="confirm"):
        runtime.backend(study, Path("/unused"), write=True)
    assert (
        not {"R2_SECRET_ACCESS_KEY", "LSB_ARCHIVE_MODE", "MONGODB_URI"}
        & agent_env().keys()
    )


def test_future_hook_queues_then_recovers(setup, tmp_path):
    study, _, trial = setup
    with patch.object(runtime, "backend", side_effect=ConnectionError):
        runtime.hook(trial.parent, starting=True)
        runtime.hook(trial.parent)
    with sqlite3.connect(tmp_path / "state/journal.sqlite") as db:
        assert db.execute("select count(*) from pending").fetchone()[0] == 1
    assert cli.cli(["reconcile"]) == 0
    assert runtime.backend(study, tmp_path / "state", write=False).catalog.find(
        "snapshots"
    )


def test_cloud_completion_hook_retries_after_catalog_outage(
    setup, tmp_path, monkeypatch
):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    mongo = pytest.importorskip("mongomock")
    study, _, trial = setup
    for key, value in {
        "LSB_ARCHIVE_MODE": "r2",
        "R2_ENDPOINT_URL": "https://s3.amazonaws.com",
        "R2_EVIDENCE_BUCKET": "test-evidence",
        "R2_ARCHIVE_BUCKET": "test-runs",
        "R2_ACCESS_KEY_ID": "testing",
        "R2_SECRET_ACCESS_KEY": "testing",
        "MONGODB_URI": "mongodb://localhost",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LSB_CONFIRMED_TARGET", runtime.target())
    catalog = MongoCatalog(mongo.MongoClient().proofread)
    monkeypatch.setattr(runtime, "configured_catalog", lambda state, cloud: catalog)
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        for bucket in ("test-evidence", "test-runs"):
            s3.create_bucket(Bucket=bucket)
        runtime.hook(trial.parent, starting=True)
        with patch.object(catalog, "register", side_effect=ConnectionError):
            runtime.hook(trial.parent)
        assert not catalog.find("snapshots")
        assert cli.cli(["reconcile"]) == 0
        (snapshot,) = catalog.find("snapshots")
        assert snapshot["locations"] == {
            "evidence": "test-evidence",
            "restricted": "test-runs",
        }
        archive = runtime.backend(study, tmp_path / "state", write=False)
        assert archive.recover(snapshot["run_id"], tmp_path / "download") > 0
        assert cli.cli(["reconcile"]) == 0
        assert catalog.find("snapshots") == [snapshot]


def test_host_launcher_archives_failed_run_from_fresh_shell(
    setup, tmp_path, monkeypatch
):
    study, _, trial = setup
    launcher = study.repo / "tools/libstruct-archive"
    launcher.parent.mkdir()
    shutil.copy2(
        Path(__file__).resolve().parents[1] / "tools/libstruct-archive", launcher
    )
    bin_dir = study.repo / ".venv/bin"
    bin_dir.mkdir(parents=True)
    entry = bin_dir / "libstruct-archive"
    entry.write_text(
        f"#!{sys.executable}\nfrom libstruct_bench.archive.cli import main\nraise SystemExit(main())\n"
    )
    entry.chmod(0o755)
    harbor = bin_dir / "harbor"
    harbor.write_text(
        f"#!{sys.executable}\n"
        "import json, os, shutil, sys\n"
        "from pathlib import Path\n"
        "assert not any(k.startswith(('LSB_', 'R2_')) or k == 'MONGODB_URI' for k in os.environ)\n"
        "assert sys.argv[1:] == ['run', '-c', 'new.json', '--job-name', 'fresh']\n"
        "cfg = json.loads(Path('new.json').read_text())\n"
        "job = Path(cfg['jobs_dir']) / 'fresh'\n"
        "shutil.copytree(Path(cfg['jobs_dir']) / 'base', job)\n"
        "result = job / 'test__abc/result.json'\n"
        "record = json.loads(result.read_text())\n"
        "record['exception_info'] = {'exception_type': 'RunnerFailed'}\n"
        "result.write_text(json.dumps(record))\n"
        "raise SystemExit(17)\n"
    )
    harbor.chmod(0o755)
    (study.repo / ".env.archive.local").write_text(
        "LSB_ARCHIVE_MODE='local'\nR2_SECRET_ACCESS_KEY='testsecret'\nMONGODB_URI='testuri'\n"
    )
    put(
        study.repo / "new.json",
        {"job_name": "base", "jobs_dir": str(trial.parent.parent)},
    )
    monkeypatch.delenv("LSB_ARCHIVE_MODE")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    command = [str(launcher), "launch", "-c", "new.json"]
    conflict = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert conflict.returncode == 2 and "fresh name" in conflict.stderr
    launched = subprocess.run([*command, "--job-name", "fresh"], cwd=tmp_path)
    assert launched.returncode == 17
    # Each new invocation reloads the private host profile.
    query = subprocess.check_output([str(launcher), "query"], cwd=tmp_path)
    assert json.loads(query)["status"] == "failed"
    retry = subprocess.check_output([str(launcher), "reconcile"], cwd=tmp_path)
    assert json.loads(retry) == {"examined": 0, "pending_errors": []}


def test_excluded_trial_never_opens_cloud_backend(setup, tmp_path):
    study, _, trial = setup
    put(
        trial / "config.json",
        {"agent": {"name": "mini-swe-agent"}, "task": {"path": "test"}},
    )
    runtime.hook(trial.parent, starting=True)
    with patch.object(runtime, "backend") as backend:
        assert runtime.archive_job(trial.parent, study, tmp_path / "state") == 0
        backend.assert_not_called()


def test_empty_job_respects_dataset_selection(setup):
    study, _, trial = setup
    tasks = study.repo / "benchmarks/tasks"
    (tasks / "outside-scg").mkdir()
    (tasks / "outside-scg/task.toml").touch()
    dataset = {"path": str(tasks)}
    config = {"agents": [{"name": "codex"}], "datasets": [dataset]}
    put(trial.parent / "config.json", config)
    assert study.job_protocols(trial.parent) is None
    dataset["task_names"] = ["test"]
    put(trial.parent / "config.json", config)
    assert study.job_protocols(trial.parent) == ["test"]


def test_empty_job_and_symlink_safety(setup, tmp_path):
    _, archive, trial = setup
    job = trial.parent.with_name("partial")
    put(
        job / "config.json",
        {"tasks": [{"path": "tasks/test"}], "agents": [{"name": "codex"}]},
    )
    m = archive.empty_job(job)
    assert archive.recover(m["run_id"], tmp_path / "recovered") == 1
    with pytest.raises(ValueError):
        archive.restricted.exists("../escape")
    (trial / "symlink.log").symlink_to(tmp_path)
    manifest, _ = archive.ingest(trial)
    assert not any("symlink.log" in a["path"] for a in manifest["artifacts"])


def test_mock_s3_mongo_multipart_resume_and_retrieval(setup, tmp_path):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    mongo = pytest.importorskip("mongomock")
    study, _, trial = setup
    with moto.mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        buckets = ["test-evidence", "test-restricted"]
        for bucket in buckets:
            client.create_bucket(Bucket=bucket)
        stores = [
            R2Store(client, b, tmp_path / "journal", part_size=5 * 1024**2)
            for b in buckets
        ]
        catalog = MongoCatalog(mongo.MongoClient().proofread)
        catalog.setup()
        archive = Archive(study, tmp_path / "state", *stores, catalog)
        data = tmp_path / "multipart.bin"
        data.write_bytes(b"A" * (5 * 1024**2) + b"B" * 1024)
        key = "large/" + sha256(data)
        api = client.upload_part
        numbers = []

        def broken(**kwargs):
            numbers.append(kwargs["PartNumber"])
            if numbers == [1, 2]:
                raise ConnectionError("injected")
            return api(**kwargs)

        with patch.object(client, "upload_part", side_effect=broken):
            with pytest.raises(ConnectionError):
                stores[1].upload(key, data)
            stores[1].upload(key, data)
        assert numbers == [1, 2, 2]
        stores[1].download(key, tmp_path / "out.bin", sha256(data))
        m, ref = archive.ingest(trial)
        assert archive.ingest(trial)[1] == ref and len(catalog.find("snapshots")) == 1
        assert archive.recover(m["run_id"], tmp_path / "mongo-restore") > 0


def test_latest_snapshot_view_and_pending_destination(setup, tmp_path, monkeypatch):
    study, archive, trial = setup
    first, old = archive.ingest(trial)
    put(trial / "verifier/rescore/libgen-5.0.0/reward.json", {"reward": 0.9})
    newer, new = archive.ingest(trial)
    assert first["trial_id"] == newer["trial_id"] and old != new
    root = tmp_path / "latest"
    archive.recover(first["run_id"], root)
    assert [p.name for p in (root / first["trial_id"]).iterdir()] == [new["sha256"]]
    with runtime.journal(tmp_path / "state") as db:
        db.execute(
            "INSERT INTO pending VALUES (?,?,0)",
            (str(trial.parent), '{"archive_target":"r2/unconfigured"}'),
        )
    with pytest.raises(ValueError, match="destination"):
        runtime.archive_job(trial.parent, study, tmp_path / "state")


def test_scg_source_directory_and_hash_fallback(setup):
    study, archive, trial = setup
    original = study.data / "protocols"
    original.rename(study.data / "protocols_scg_all")
    m, _ = archive.ingest(trial)
    assert m["source_bundle"]["documents"][0]["available"]
    # The legacy layout may coexist with an unrelated newer file.
    file = original / "test/source.pdf"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"different")
    assert archive.ingest(trial)[0]["source_bundle_id"] == m["source_bundle_id"]


def test_real_native_pilot_with_mock_cloud(tmp_path):
    import os

    if os.getenv("LSB_TEST_REAL_PILOT") != "1":
        pytest.skip("enable locally with the private source collection and saved pilot")
    import boto3
    import mongomock
    from moto import mock_aws

    study, _, _ = runtime.settings()
    trial = (
        study.runs
        / "libgen/codex/libgen-gpt-5-6-sol/10x_chromium_3_feature_barcoding__bN2Lxic"
    )
    before = {p: sha256(p) for p in files(trial)}
    assert before
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        stores = []
        for bucket in ("pilot-evidence", "pilot-restricted"):
            client.create_bucket(Bucket=bucket)
            stores.append(R2Store(client, bucket, tmp_path / "parts"))
        catalog = MongoCatalog(mongomock.MongoClient().proofread)
        catalog.setup()
        archive = Archive(study, tmp_path, *stores, catalog)
        manifest, ref = archive.ingest(trial)
        source_docs = [a for a in manifest["artifacts"] if a["role"] == "source"]
        assert len(source_docs) == 5
        assert manifest["metadata"]["evaluation_schema_version"] == "4.7.0"
        counts = [len(list(store.list())) for store in stores]
        dest = tmp_path / "restore"
        restored = archive.recover(manifest["run_id"], dest)
        assert archive.ingest(trial)[1] == ref and counts == [
            len(list(store.list())) for store in stores
        ]
        assert len(catalog.find("snapshots")) == 1
        root = dest / manifest["trial_id"] / ref["sha256"]
        for art in manifest["artifacts"]:
            if art["status"] == "original":
                assert sha256(root / art["path"]) == art["original_sha256"]
        assert before == {p: sha256(p) for p in files(trial)}
        print(
            json.dumps(
                {
                    "run_id": manifest["run_id"],
                    "manifest_sha256": ref["sha256"],
                    "original_trial_files_verified": len(before),
                    "source_documents": len(source_docs),
                    "verified_retrievals": restored,
                    "archive_counts": {
                        status: sum(
                            a["status"] == status for a in manifest["artifacts"]
                        )
                        for status in ("original", "sanitized", "withheld")
                    },
                    "object_counts": counts,
                    "registered_snapshots_after_repeat": 1,
                    "source_status": manifest["source_bundle"]["status"],
                }
            )
        )
