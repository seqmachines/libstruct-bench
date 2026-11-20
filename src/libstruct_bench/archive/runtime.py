"""Host configuration and durable post-run queue. Never load archive credentials in an agent."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .catalog import configured_catalog
from .discovery import Study
from .pipeline import Archive
from .storage import LocalStore, R2Store, canonical, digest, object_lock


def runs_root():
    repo = Path(os.environ.get("LSB_REPO_ROOT", Path.cwd()))
    return Path(os.environ.get("LSB_RUNS_ROOT", repo / "runs")).expanduser().resolve()


def settings():
    repo = Path(os.environ.get("LSB_REPO_ROOT", Path.cwd())).expanduser().resolve()
    runs = runs_root()
    data = (
        Path(os.environ.get("LIBSTRUCT_DATA_ROOT", repo.parent / "protocols-test"))
        .expanduser()
        .resolve()
    )
    extra = (
        Path(os.environ.get("LSB_IMPORTANT_RUNS", repo.parent / "important_runs"))
        .expanduser()
        .resolve()
    )
    state = (
        Path(
            os.environ.get(
                "LSB_ARCHIVE_STATE", Path.home() / ".local/state/libstruct-archive"
            )
        )
        .expanduser()
        .resolve()
    )
    if any(
        state.is_relative_to(p) or p.is_relative_to(state) for p in (runs, data, extra)
    ):
        raise ValueError("archive state must be separate from input directories")
    if (
        state.is_relative_to(repo)
        and state != repo / ".archive-state"
        and not state.is_relative_to(repo / "tmp")
    ):
        raise ValueError("use a private state directory outside tracked project files")
    return Study(repo, runs, data), state, extra


def target():
    endpoint = urlsplit(os.environ["R2_ENDPOINT_URL"])
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or endpoint.path not in ("", "/")
    ):
        raise ValueError("expected private R2 HTTPS endpoint")
    buckets = [os.environ["R2_EVIDENCE_BUCKET"], os.environ["R2_ARCHIVE_BUCKET"]]
    if buckets[0] == buckets[1]:
        raise ValueError("evidence and restricted archive require separate buckets")
    mongo = urlsplit(os.environ["MONGODB_URI"])
    if mongo.scheme not in {"mongodb", "mongodb+srv"} or not mongo.hostname:
        raise ValueError("invalid MongoDB target")
    return "/".join(
        [
            endpoint.netloc,
            *buckets,
            mongo.netloc.rsplit("@", 1)[-1],
            os.getenv("LSB_MONGO_DATABASE", "proofread"),
        ]
    )


def backend(study, state, *, write, confirm=None):
    mode = os.environ.get("LSB_ARCHIVE_MODE", "local")
    if mode not in {"local", "off", "r2"}:
        raise ValueError("unknown archive mode")
    if mode != "r2":
        cat = configured_catalog(state, False)
        return Archive(
            study,
            state,
            LocalStore(state / "evidence"),
            LocalStore(state / "restricted"),
            cat,
        )
    if write and (confirm or os.getenv("LSB_CONFIRMED_TARGET")) != target():
        raise ValueError(
            "confirm the exact endpoint, buckets and Mongo target before writing"
        )
    import botocore.session
    from botocore.config import Config

    session = botocore.session.get_session()
    client = session.create_client(
        "s3",
        region_name="auto",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            retries={"mode": "standard", "total_max_attempts": 5},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    stores = [
        R2Store(client, os.environ[b], state / "multipart")
        for b in ("R2_EVIDENCE_BUCKET", "R2_ARCHIVE_BUCKET")
    ]
    cat = configured_catalog(state, True)
    if write:
        cat.setup()
    return Archive(study, state, *stores, cat)


def queue_target():
    if os.getenv("LSB_ARCHIVE_MODE") != "r2":
        return "local"
    try:
        return "r2/" + target()
    except (KeyError, ValueError):
        return "r2/unconfigured"


def journal(state):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(state / "journal.sqlite", timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pending (job TEXT PRIMARY KEY, host TEXT, pid INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS migrated (batch TEXT, path TEXT, PRIMARY KEY(batch,path))"
    )
    return conn


def execution_context(repo):
    def git(*args):
        r = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, check=False
        )
        return r.stdout.decode().strip() if r.returncode == 0 else None

    status = git("status", "--porcelain", "--untracked-files=normal")
    return {
        "invoked_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
    }


def archive_job(job, study, state, *, confirm=None):
    root = Path(job).resolve()
    if not root.is_relative_to(study.runs) or root == study.runs:
        raise ValueError("job outside configured run root")
    with (
        object_lock(state / "locks" / digest(str(root).encode())),
        closing(journal(state)) as db,
    ):
        row = db.execute(
            "SELECT host FROM pending WHERE job=?", (str(root),)
        ).fetchone()
        host = json.loads(row[0]) if row else {}
        expected = host.get("archive_target")
        current = queue_target()
        if (
            expected
            and expected != current
            and not (expected == "r2/unconfigured" and current.startswith("r2/"))
        ):
            raise ValueError(
                "queued destination differs from the configured archive target"
            )
        records = list(study.inventory(root))
        snapshots = root.parent / ".libgen_telemetry" / root.name / "resume_snapshots"
        if snapshots.is_dir() and snapshots.resolve() == snapshots:
            records.extend(study.inventory(snapshots))
        trials = [r for r in records if r.get("eligible")]
        if trials:
            archive = backend(study, state, write=True, confirm=confirm)
            for trial in trials:
                archive.ingest(study.runs / trial["path"], host=host)
        elif not any(r["kind"] in {"trial", "direct-api"} for r in records):
            if study.job_protocols(root):
                backend(study, state, write=True, confirm=confirm).empty_job(root, host)
            elif not (root / "result.json").is_file():
                raise ValueError("no attributable job files yet; retained pending job")
        if any(
            r.get("reason") == "unknown-harness" and r.get("status") == "partial"
            for r in records
        ):
            raise ValueError("partial trial has no identifiable harness yet")
        with db:
            db.execute("DELETE FROM pending WHERE job=?", (str(root),))
        return len(trials)


def hook(job, *, starting=False):
    if os.environ.get("LSB_ARCHIVE_MODE", "off") not in {"local", "r2"}:
        return
    try:
        study, state, _ = settings()
        job = Path(job).resolve()
        if not job.is_relative_to(study.runs):
            raise ValueError("unconfigured job root")
        with closing(journal(state)) as db, db:
            if starting:
                host = {
                    **execution_context(study.repo),
                    "archive_target": queue_target(),
                }
                db.execute(
                    "INSERT INTO pending VALUES (?,?,?) ON CONFLICT(job) DO UPDATE SET pid=excluded.pid, host=excluded.host",
                    (str(job), canonical(host).decode(), os.getpid()),
                )
                return
            db.execute(
                "INSERT INTO pending VALUES (?, ?, 0) ON CONFLICT(job) DO UPDATE SET pid=0",
                (str(job), canonical({"archive_target": queue_target()}).decode()),
            )
        archive_job(job, study, state)
    except Exception as exc:
        # Provider messages and URLs can contain authentication material.
        print(
            f"Archive pending ({type(exc).__name__}). Use libstruct-archive reconcile.",
            file=sys.stderr,
        )


def live(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
