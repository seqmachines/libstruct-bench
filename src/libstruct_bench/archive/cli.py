"""Host-side CLI for discovery, archival and verified recovery."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

from .catalog import current_snapshots
from .discovery import read_json, scalar
from .hooks import agent_env, enabled
from .pipeline import Archive, trial_stamp
from .runtime import (
    archive_job,
    backend,
    hook,
    journal,
    live,
    runs_root,
    settings,
    target,
)
from .versions import latest, version
from .storage import canonical, digest, files, key_path


def extra_inventory(root, study):
    if not root.is_dir():
        return
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        candidates = [p for p in files(folder) if p.name == "config.json"]
        trials = 0
        for config in candidates:
            cfg = read_json(config)
            if "agent" not in cfg:
                continue
            trials += 1
            task = cfg.get("task") or {}
            result = read_json(config.with_name("result.json"))
            yield {
                "root": "important_runs",
                "path": str(config.parent.relative_to(root)),
                "experiment_id": folder.name,
                "kind": "trial",
                "eligible": False,
                "reason": "parser-control"
                if folder.name in {"parser_control_text5", "oligo_summary_text5"}
                else "unclassified",
                "protocol_id": Path(task.get("path") or "").name,
                "model": scalar(cfg["agent"].get("model_name")),
                "executor": scalar(cfg["agent"].get("name")),
                "execution_id": scalar(result.get("id")),
            }
        if not trials:
            yield {
                "root": "important_runs",
                "path": folder.name,
                "kind": "analysis",
                "eligible": False,
                "reason": "local-analysis",
                "file_count": sum(1 for _ in files(folder)),
            }


def inventory_stamp(directory):
    return digest(
        canonical(
            [
                [str(path.relative_to(directory)), size, mtime]
                for path, (_, size, mtime) in sorted(trial_stamp(directory).items())
            ]
        )
    )


def inventory(study, extra):
    records = [{"root": "runs", **r} for r in study.inventory()]
    parents = {
        p
        for row in records
        if row["kind"] == "trial"
        for p in Path(row["path"]).parents
    }
    for row in records:
        path = Path(row["path"])
        if (
            row["kind"] == "job"
            and path not in parents
            and study.job_protocols(study.runs / path)
        ):
            row.update(eligible=True, reason=None, kind="job-without-trials")
        if row["eligible"]:
            row["layout_digest"] = inventory_stamp(study.runs / path)
    records.extend(extra_inventory(extra, study))
    return {
        "schema_version": "libstruct.archive.inventory.v1",
        "scope": "native-scg",
        "records": records,
        "paper_index_sha256": digest(canonical(study.paper)),
        "source_inventory_sha256": digest(canonical(study.sources)),
    }


def summary(m, ref):
    return {
        "run_id": m["run_id"],
        "trial_id": m["trial_id"],
        "manifest_sha256": ref["sha256"],
        "source_status": m["source_bundle"]["status"],
        "verifier": m["metadata"]["evaluation_schema_version"],
        "artifacts": dict(Counter(a["status"] for a in m["artifacts"])),
    }


def output_path(path, study, extra):
    p = path.expanduser()
    p = p.parent.resolve() / p.name
    if p.resolve() != p or any(
        p.is_relative_to(r) for r in (study.runs, study.data, extra)
    ):
        raise ValueError("use a fresh output path outside the original data")
    return p


def trial_path(study, value):
    p = Path(value)
    if not p.is_absolute():
        p = study.runs / key_path(value)
    if p != p.resolve() or not p.is_relative_to(study.runs) or not p.is_dir():
        raise ValueError("trial path must be a real directory under the run root")
    return p


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--output", type=Path)
    report = sub.add_parser("verifier-report")
    report.add_argument("--version", required=True)
    report.add_argument("--output", required=True, type=Path)
    sub.add_parser("target")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("trial", help="trial path relative to the run root")
    ingest.add_argument("--manifest-out", type=Path)
    migrate = sub.add_parser("migrate")
    migrate.add_argument("inventory", type=Path)
    migrate.add_argument("--authorize-bulk", metavar="INVENTORY_SHA256")
    migrate.add_argument(
        "--refresh",
        action="store_true",
        help="recheck previously registered rows and linked assets",
    )
    for cmd in (ingest, migrate):
        mode = cmd.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--dry-run", action="store_true", help="default")
        cmd.add_argument("--confirm-target")
    sub.add_parser("reconcile").add_argument("--confirm-target")
    for name in ("query", "fetch", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--run", required=name != "query")
        cmd.add_argument("--trial")
        cmd.add_argument("--revision")
        if name == "query":
            for field in ("protocol", "model", "executor", "experiment"):
                cmd.add_argument("--" + field)
        if name == "fetch":
            cmd.add_argument("--out", type=Path, required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--prefix", default="libstructbench/")
    launch = sub.add_parser("launch")
    launch.add_argument("-c", "--config", type=Path, required=True)
    launch.add_argument(
        "--job-name", help="use a fresh Harbor job name for this execution"
    )
    launch.add_argument("-y", "--yes", action="store_true")
    args, forwarded = parser.parse_known_args(argv)
    if forwarded and args.command != "launch":
        parser.error("unrecognized arguments")
    if any(
        arg.split("=")[0] in {"--jobs-dir", "--job-name", "-c", "--config"}
        or arg.startswith("-o")
        for arg in forwarded
    ):
        parser.error("use the saved config to specify the archive job directory")
    if args.command == "target":
        print(target())
        return 0
    if args.command == "launch":
        cfg = read_json(args.config)
        job = (
            Path(cfg["jobs_dir"]) / key_path(args.job_name or cfg["job_name"])
        ).resolve()
        if enabled():
            if runs_root() not in job.parents:
                parser.error("choose a job directory under the configured runs root")
            if job.exists() and (not job.is_dir() or any(job.iterdir())):
                parser.error(
                    "job directory already contains data; use --job-name with a fresh name"
                )
        hook(job, starting=True)
        try:
            return subprocess.run(
                ["harbor", "run", "-c", str(args.config)]
                + (["--job-name", args.job_name] if args.job_name else [])
                + (["--yes"] if args.yes else [])
                + forwarded,
                env=agent_env(),
                check=False,
            ).returncode
        finally:
            hook(job)
    study, state, extra = settings()
    if args.command == "verifier-report":
        if not version(args.version):
            raise ValueError("expected a semantic verifier version")
        rows = []
        for row in study.inventory():
            if not row.get("eligible"):
                continue
            trial = study.runs / row["path"]
            original = read_json(trial / "verifier/details.json").get(
                "benchmark_version"
            )
            rescored = read_json(
                trial / "verifier/rescore" / ("libgen-" + args.version) / "details.json"
            )
            if (
                original != args.version
                and rescored.get("benchmark_version") != args.version
            ):
                continue
            choice, _ = latest(trial, study.paper.get(row["path"], []), study)
            rows.append(
                {
                    **{
                        k: row[k]
                        for k in (
                            "experiment_id",
                            "protocol_id",
                            "model",
                            "executor",
                            "execution_id",
                            "path",
                        )
                    },
                    "version_use": "execution"
                    if original == args.version
                    else "rescore",
                    "execution_verifier": original,
                    "latest_attributable_verifier": choice["version"],
                }
            )
        with output_path(args.output, study, extra).open("x", newline="") as out:
            writer = csv.DictWriter(
                out, fieldnames=list(rows[0]) if rows else ["path", "version_use"]
            )
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps(dict(Counter(r["version_use"] for r in rows))))
        return 0
    if args.command == "inventory":
        report = inventory(study, extra)
        rows = report["records"]
        data = canonical(report)
        if args.output:
            with output_path(args.output, study, extra).open("xb") as handle:
                handle.write(data)
        print(
            json.dumps(
                {
                    "inventory_sha256": digest(data),
                    "records": len(rows),
                    "counts": dict(
                        Counter(
                            "eligible" if r["eligible"] else r["reason"] for r in rows
                        )
                    ),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "reconcile":
        with closing(journal(state)) as db:
            pending = list(db.execute("SELECT job,pid FROM pending ORDER BY job"))
        failures = []
        for job, pid in pending:
            if live(pid):
                continue
            try:
                archive_job(job, study, state, confirm=args.confirm_target)
            except Exception as exc:
                failures.append({"job": job, "error": type(exc).__name__})
        print(json.dumps({"examined": len(pending), "pending_errors": failures}))
        return int(bool(failures))
    if args.command in {"ingest", "migrate"}:
        if args.command == "migrate":
            frozen = args.inventory.read_bytes()
            batch = digest(frozen)
            report = json.loads(frozen)
            if (
                report.get("schema_version") != "libstruct.archive.inventory.v1"
                or report.get("scope") != "native-scg"
            ):
                raise ValueError("unexpected inventory schema or scope")
            if report.get("paper_index_sha256") != digest(
                canonical(study.paper)
            ) or report.get("source_inventory_sha256") != digest(
                canonical(study.sources)
            ):
                raise ValueError("study inputs changed; refresh inventory")
            if args.apply and args.authorize_bulk != batch:
                raise ValueError("bulk apply requires the reviewed inventory SHA-256")
        archive = (
            backend(study, state, write=True, confirm=args.confirm_target)
            if args.apply
            else Archive(study, state, None, None, None)
        )
        if args.command == "ingest":
            manifest, ref = archive.ingest(
                trial_path(study, args.trial), dry=not args.apply
            )
            if args.manifest_out:
                with output_path(args.manifest_out, study, extra).open("xb") as out:
                    out.write(canonical(manifest))
            print(json.dumps(summary(manifest, ref), indent=2))
            return 0
        rows = [r for r in report["records"] if r["root"] == "runs" and r["eligible"]]
        counts = Counter()
        # A completed migration receipt is specific to the exact destination.
        dest = (
            target()
            if os.getenv("LSB_ARCHIVE_MODE") == "r2" and args.apply
            else "local"
        )
        receipt = digest(canonical([batch, str(study.runs), dest]))
        db = journal(state) if args.apply else None
        try:
            for r in rows:
                if (
                    db
                    and not args.refresh
                    and db.execute(
                        "SELECT 1 FROM migrated WHERE batch=? AND path=?",
                        (receipt, r["path"]),
                    ).fetchone()
                ):
                    counts["already_registered"] += 1
                    continue
                try:
                    p = trial_path(study, r["path"])
                    if inventory_stamp(p) != r.get("layout_digest"):
                        raise ValueError("local artifacts changed; refresh inventory")
                    if r["kind"] == "job-without-trials":
                        if any(
                            entry["kind"] == "trial" for entry in study.inventory(p)
                        ):
                            raise ValueError(
                                "inventory job gained trials; refresh inventory"
                            )
                        if archive.empty_job(p, dry=not args.apply) is None:
                            raise ValueError("job eligibility changed")
                    else:
                        actual = study.classify(p)
                        if any(
                            actual[k] != r.get(k)
                            for k in (
                                "eligible",
                                "protocol_id",
                                "executor",
                                "execution_id",
                                "status",
                            )
                        ):
                            raise ValueError("inventory identity changed")
                        archive.ingest(p, dry=not args.apply)
                    if db:
                        with db:
                            db.execute(
                                "INSERT OR IGNORE INTO migrated VALUES (?,?)",
                                (receipt, r["path"]),
                            )
                    counts["registered" if db else "planned"] += 1
                except Exception as exc:
                    counts["failed"] += 1
                    print(
                        json.dumps({"path": r["path"], "error": type(exc).__name__}),
                        file=sys.stderr,
                    )
        finally:
            if db:
                db.close()
        print(json.dumps(counts))
        return int(bool(counts["failed"]))
    archive = backend(study, state, write=False)
    if args.command == "list":
        for store in (archive.evidence, archive.restricted):
            for key in store.list(args.prefix):
                print(key)
    elif args.command == "query":
        criteria = {
            field: getattr(args, arg)
            for arg, field in [
                ("run", "run_id"),
                ("trial", "trial_id"),
                ("revision", "_id"),
                ("protocol", "protocol_id"),
                ("model", "model"),
                ("executor", "executor"),
                ("experiment", "experiment_id"),
            ]
            if getattr(args, arg)
        }
        snapshots = archive.catalog.find("snapshots", **criteria)
        for record in snapshots if args.revision else current_snapshots(snapshots):
            print(json.dumps(record))
    else:
        out = output_path(args.out, study, extra) if args.command == "fetch" else None
        print(
            json.dumps(
                {
                    "verified_artifacts": archive.recover(
                        args.run, out, args.trial, args.revision
                    )
                }
            )
        )
    return 0


def main(argv=None):
    try:
        return cli(argv)
    except (Exception, KeyboardInterrupt) as exc:
        # Library, network and parse exception messages can include credentials.
        print(
            f"Archive command failed ({type(exc).__name__}). Check paths, target confirmation and setup.",
            file=sys.stderr,
        )
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
