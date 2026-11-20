"""Freeze a single trial, publish immutable blobs, then commit its verified catalog record."""

from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime
from functools import lru_cache
from importlib.resources import files as resources
from pathlib import Path

from jsonschema import Draft202012Validator

from .versions import frozen_results, latest
from .catalog import current_snapshots, index_documents
from .discovery import identities, read_json, scalar
from .provenance import checkpoint_context, execution_inputs, source_context, within
from .security import environment_secrets, prepare, screen
from .storage import IntegrityError, canonical, digest, files, key_path, sha256

PREFIX = "libstructbench"
GT_NAMES = {
    "t1": "groundtruth_final_lib_struct.json",
    "t2": "groundtruth_oligos.json",
    "t3": "groundtruth_library_generation_workflow.json",
}


@lru_cache(maxsize=1)
def validator():
    schema = json.loads(
        resources(__package__).joinpath("manifest.schema.json").read_text()
    )
    return Draft202012Validator(schema)


def trial_stamp(directory):
    result = {}
    for f in files(directory):
        stat = f.stat()
        result[f] = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return result


def role(rel):
    p = Path(rel)
    if "verifier" in p.parts or "groundtruth" in p.name:
        return "evaluation"
    if "prediction" in p.name:
        return "output"
    if p.parts[0] == "agent" or "trajectory" in p.name:
        return "trajectory"
    if p.name in {"config.json", "lock.json", "manifest.json"}:
        return "configuration"
    if p.suffix in {".txt", ".log", ".jsonl"}:
        return "log"
    return "artifact"


def seconds(start, finish):
    try:
        return (
            datetime.fromisoformat(finish.replace("Z", "+00:00"))
            - datetime.fromisoformat(start.replace("Z", "+00:00"))
        ).total_seconds()
    except (ValueError, AttributeError, TypeError):
        return None


def metadata(result, cfg, lock, path, host):
    info = result.get("agent_info") or {}
    model = info.get("model_info") or {}
    agent = cfg.get("agent") or lock.get("agent") or {}
    kwargs = agent.get("kwargs") or {}
    stats = result.get("agent_result") or {}
    reward = (
        read_json(path / "verifier/reward.json")
        or (result.get("verifier_result") or {}).get("rewards")
        or {}
    )
    start, finish = result.get("started_at"), result.get("finished_at")
    if host.get("invoked_at") and (seconds(host["invoked_at"], start) or 0) <= 0:
        host = {}
    return {
        "observed_model": scalar(model.get("name")),
        "model_version": scalar(model.get("version")),
        "harness_version": scalar(info.get("version") or kwargs.get("version")),
        "git_commit": scalar(host.get("git_commit") or cfg.get("git_commit")),
        "git_dirty": host.get("git_dirty"),
        "reasoning": scalar(kwargs.get("reasoning_effort") or kwargs.get("thinking")),
        "seed": cfg.get("seed", kwargs.get("seed"))
        if isinstance(cfg.get("seed", kwargs.get("seed")), int)
        else None,
        "started_at": scalar_time(start),
        "finished_at": scalar_time(finish),
        "duration_seconds": seconds(start, finish),
        "exception_type": scalar(
            (result.get("exception_info") or {}).get("exception_type")
        ),
        "usage": {
            k: number(stats.get(k))
            for k in ("n_input_tokens", "n_output_tokens", "n_cache_tokens", "cost_usd")
        },
        "metrics": {
            k: number(v)
            for k, v in reward.items()
            if scalar(k) and number(v) is not None
        },
        "evaluation_schema_version": scalar(
            read_json(path / "verifier/details.json").get("benchmark_version")
        ),
    }


def number(value):
    return value if type(value) in (float, int) and math.isfinite(value) else None


def scalar_time(value):
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return None


class Archive:
    def __init__(self, study, state, evidence, restricted, catalog):
        self.study = study
        self.state = state
        self.evidence, self.restricted, self.catalog = evidence, restricted, catalog

    def store(self, key):
        return (
            self.evidence if key.startswith(PREFIX + "/protocols/") else self.restricted
        )

    def publish(self, key, data, temp, dry):
        encoded = canonical(data)
        expected = digest(encoded)
        if not dry:
            temp.write_bytes(encoded)
            if screen(temp, environment_secrets(), scan_encoded=False):
                raise ValueError("sensitive index material withheld")
            self.store(key).upload(key, temp, expected)
        return {"key": key, "sha256": expected}

    def ingest(self, path, *, dry=False, host=None):
        initial = trial_stamp(path)
        row = self.study.classify(path)
        if not row["eligible"]:
            raise ValueError("trial excluded: " + row["reason"])
        cfg, result, lock = (
            read_json(path / n) for n in ("config.json", "result.json", "lock.json")
        )
        cfg = cfg if "agent" in cfg else result.get("config") or cfg
        job = self.study.job(path)
        job_rel = str(job.relative_to(self.study.runs))
        run, trial = identities(row, job_rel, cfg, lock)
        source, attachments = source_context(self.study, row, cfg, lock, result)
        checkpoints, linked = checkpoint_context(self.study, row, job)
        attachments.extend(linked)
        attachments.extend(execution_inputs(self.study, cfg, lock))
        paper = self.study.paper.get(row["path"], [])
        evaluation, selected_dir = latest(path, paper, self.study)
        attachments.extend(frozen_results(self.study, paper, evaluation))
        for f in files(path):
            if f.is_relative_to(path / "verifier") and (
                not selected_dir
                or not f.is_relative_to(selected_dir)
                or (
                    selected_dir == path / "verifier"
                    and f.is_relative_to(selected_dir / "rescore")
                )
            ):
                continue
            rel = f.relative_to(path).as_posix()
            attachments.append(("trial/" + rel, f, role(rel), None))
        for f in sorted(job.iterdir()):
            if (
                f.is_file()
                and not f.is_symlink()
                and f.name
                in {
                    "config.json",
                    "lock.json",
                    "result.json",
                    "job.log",
                    "kb_evaluation_aggregate.json",
                }
            ):
                attachments.append(("job/" + f.name, f, role(f.name), None))
        for ref in paper:
            record = ref["row"]
            for task, name in GT_NAMES.items():
                expected = record.get("reference_" + task + "_sha256")
                if expected:
                    rel = row["protocol_id"] + "/" + name
                    f = within(self.study.data / "ground_truth", rel)
                    if f and sha256(f) == expected:
                        attachments.append(
                            (
                                "curated-records/" + expected + "/" + name,
                                f,
                                "evaluation",
                                expected,
                            )
                        )
        manifest = {
            k: row[k]
            for k in (
                "experiment_id",
                "protocol_id",
                "model",
                "executor",
                "path",
                "status",
                "execution_id",
                "job_id",
            )
        }
        manifest.update(
            schema_version="libstruct.archive.trial.v1",
            run_id=run,
            trial_id=trial,
            job_path=job_rel,
            technology_id=scalar(
                cfg.get("technology_id") or result.get("technology_id")
            ),
            metadata=metadata(result, cfg, lock, path, host or {}),
            checkpoints=checkpoints,
            paper=[
                {
                    "table": ref["table"],
                    "column": ref["column"],
                    "row_sha256": digest(canonical(ref["row"])),
                    **{
                        k: ref["row"][k]
                        for k in (
                            "panel",
                            "group",
                            "case_id",
                            "row_id",
                            "bundle_case_id",
                            "name_case_id",
                            "reference_set_id",
                            "scorer_sha256",
                        )
                        if k in ref["row"]
                    },
                }
                for ref in paper
            ],
            artifacts=[],
            source_bundle=source,
        )
        skills = lock.get("skills") or []
        manifest["metadata"]["skills"] = [
            {
                k: scalar(item.get(k))
                for k in ("name", "version", "digest", "git_commit_id")
            }
            for item in skills
            if isinstance(item, dict)
        ]
        manifest["metadata"]["skill_version"] = scalar(
            cfg.get("skill_version")
        ) or next(
            (
                item["version"] or item["digest"]
                for item in manifest["metadata"]["skills"]
                if item["version"] or item["digest"]
            ),
            None,
        )
        manifest["metadata"]["prompt_sha256"] = next(
            (
                ref["row"]["task_prompt_sha256"]
                for ref in paper
                if ref["row"].get("task_prompt_sha256")
            ),
            None,
        )
        manifest["metadata"].update(
            evaluation_origin=evaluation["origin"],
            evaluation_schema_version=evaluation["version"],
            superseded_verifiers=evaluation["superseded_versions"],
        )
        if evaluation["origin"] != "execution" or evaluation["metrics"]:
            manifest["metadata"]["metrics"] = evaluation["metrics"]
        secrets = environment_secrets()
        with tempfile.TemporaryDirectory() as staging:
            temp = Path(staging) / "staged"
            for logical, f, kind, expected in sorted(set(attachments)):
                key_path(logical)
                entry = prepare(f, temp, kind, secrets)
                if expected and entry["original_sha256"] != expected:
                    raise IntegrityError("linked artifact changed: " + logical)
                if entry["status"] != "withheld":
                    domain = "protocols" if kind == "source" else "private"
                    key = f"{PREFIX}/{domain}/blobs/sha256/{entry['sha256']}"
                    entry["key"] = key
                    if not dry:
                        self.store(key).upload(key, temp, entry["sha256"])
                manifest["artifacts"].append({"path": logical, "role": kind, **entry})
            if initial != trial_stamp(path):
                raise IntegrityError(
                    "trial changed during archival; retry after the writer exits"
                )
            # Source descriptors are independently recoverable without access to predictions.
            source_record = {
                "schema_version": "libstruct.archive.bundle.v1",
                **source,
                "artifacts": [
                    a for a in manifest["artifacts"] if a["role"] == "source"
                ],
            }
            # Verifier provenance belongs to the restricted trial manifest.
            for field in (
                "groundtruth_revision",
                "groundtruth_repo",
                "verifier_version",
            ):
                source_record.pop(field, None)
            bundle = digest(canonical(source_record))
            bundle_key = (
                f"{PREFIX}/protocols/{row['protocol_id']}/{bundle}/manifest.json"
            )
            manifest.update(source_bundle_id=bundle, source_manifest_key=bundle_key)
            validator().validate(manifest)
            self.publish(bundle_key, source_record, temp, dry)
            root = f"{PREFIX}/experiments/{row['experiment_id']}/{run}"
            version = digest(canonical(manifest))
            ref = self.publish(
                f"{root}/trials/{trial}/{version}/manifest.json", manifest, temp, dry
            )
            if not dry:
                locations = {
                    scope: getattr(store, "bucket", "local")
                    for scope, store in (
                        ("evidence", self.evidence),
                        ("restricted", self.restricted),
                    )
                }
                self.catalog.register(index_documents(manifest, ref, locations))
                snapshots = current_snapshots(
                    self.catalog.find("snapshots", run_id=run)
                )
                run_record = {
                    "schema_version": "libstruct.archive.run.v1",
                    "experiment_id": row["experiment_id"],
                    "run_id": run,
                    "trials": sorted(
                        (
                            {
                                "trial_id": s["trial_id"],
                                "manifest_key": s["key"],
                                "sha256": s["sha256"],
                            }
                            for s in snapshots
                        ),
                        key=lambda s: (s["trial_id"], s["sha256"]),
                    ),
                }
                self.publish(
                    f"{root}/manifests/{digest(canonical(run_record))}.json",
                    run_record,
                    temp,
                    False,
                )
        return manifest, ref

    def empty_job(self, job, host=None, *, dry=False):
        """Keep a native job which terminated before it produced a trial record."""
        tasks = self.study.job_protocols(job)
        if not tasks:
            return None
        cfg = read_json(job / "config.json")
        agents = cfg["agents"]
        rel = job.relative_to(self.study.runs)
        exp = (
            "task1-native"
            if rel.parts[0] == "library_structure"
            else "tasks23-" + rel.parts[1]
        )
        result = read_json(job / "result.json")
        run = digest(canonical([exp, result.get("id") or str(rel), agents]))
        record = {
            "schema_version": "libstruct.archive.job.v1",
            "run_id": run,
            "experiment_id": exp,
            "job_path": str(rel),
            "status": "no-trials",
            "protocol_ids": sorted(set(tasks)),
            "git_commit": (host or {}).get("git_commit"),
            "artifacts": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp) / "staged"
            for name in ("config.json", "lock.json", "result.json", "job.log"):
                path = within(job, name)
                if path:
                    entry = prepare(path, temp, role(name), environment_secrets())
                    if entry["status"] != "withheld":
                        entry["key"] = (
                            PREFIX + "/private/blobs/sha256/" + entry["sha256"]
                        )
                        if not dry:
                            self.restricted.upload(entry["key"], temp, entry["sha256"])
                    record["artifacts"].append(
                        {"path": "job/" + name, "role": role(name), **entry}
                    )
            version = digest(canonical(record))
            key = f"{PREFIX}/experiments/{exp}/{run}/manifests/{version}.json"
            ref = self.publish(key, record, temp, dry)
            if not dry:
                self.catalog.register(
                    [
                        ("experiments", {"_id": exp, "project": "libstructbench"}),
                        ("runs", {"_id": run, "experiment_id": exp}),
                        (
                            "snapshots",
                            {
                                "_id": version,
                                **ref,
                                "run_id": run,
                                "experiment_id": exp,
                                "trial_id": None,
                                "status": "no-trials",
                                "archive_status": "verified",
                                "source_path": str(rel),
                            },
                        ),
                    ]
                )
        return record

    def recover(self, run, destination=None, trial=None, revision=None):
        criteria = {"run_id": run}
        if trial:
            criteria["trial_id"] = trial
        if revision:
            criteria["_id"] = revision
        snapshots = self.catalog.find("snapshots", **criteria)
        if not revision:
            snapshots = current_snapshots(snapshots)
        if not snapshots:
            raise ValueError("no matching verified snapshots")
        count = 0
        for snap in snapshots:
            if snap["_id"] != snap["sha256"] or len(snap["_id"]) != 64:
                raise IntegrityError("invalid catalog snapshot identity")
            with tempfile.TemporaryDirectory() as stage:
                path = Path(stage) / "manifest.json"
                self.restricted.download(snap["key"], path, snap["sha256"])
                manifest = json.loads(path.read_bytes())
            if manifest.get("schema_version") == "libstruct.archive.job.v1":
                if manifest.get("run_id") != run or snap["trial_id"] is not None:
                    raise IntegrityError("job manifest identity mismatch")
            else:
                validator().validate(manifest)
                if (
                    manifest["run_id"] != run
                    or snap["trial_id"] != manifest["trial_id"]
                ):
                    raise IntegrityError("catalog trial identity mismatch")
                self.evidence.verify(
                    manifest["source_manifest_key"], manifest["source_bundle_id"]
                )
            if destination is not None:
                root = (
                    destination
                    / key_path(manifest.get("trial_id", "job"))
                    / snap["_id"]
                )
                if root.resolve() != root:
                    raise ValueError("symlink in restore path")
                self.restricted.download(
                    snap["key"], root / "manifest.json", snap["sha256"]
                )
            for art in manifest["artifacts"]:
                if art["status"] == "withheld":
                    continue
                store = self.store(art["key"])
                if destination is None:
                    store.verify(art["key"], art["sha256"])
                else:
                    dest = root / key_path(art["path"])
                    if dest.resolve() != dest:
                        raise ValueError("symlink in restore path")
                    store.download(art["key"], dest, art["sha256"])
                count += 1
        return count
