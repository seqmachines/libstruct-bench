"""Read-only discovery of Harbor and LibStructBench records, including incomplete jobs."""

from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from .storage import canonical, digest

NATIVE = {
    "codex",
    "claude-code",
    "gemini-cli",
    "antigravity-cli",
    "kimi-cli",
    "kimi-code",
    "AntigravityGcp",
    "AntigravityPinned",
    "AntigravityNameOnly",
    "ClaudeNameOnly",
    "CodexNameOnly",
}
BASELINES = {
    "codex",
    "claude-code",
    "antigravity-cli",
    "kimi-code",
    "pi",
    "mini-swe-agent",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/+-]{1,200}\Z")


def read_json(path):
    if path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def scalar(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def read_csv(path):
    if path.is_file():
        with path.open(newline="") as handle:
            yield from csv.DictReader(handle)


class Study:
    def __init__(self, repo, runs, data):
        self.repo, self.runs, self.data = repo.resolve(), runs.resolve(), data.resolve()
        self.sources = defaultdict(dict)
        for row in read_csv(repo / "docs/release/source_inventory.csv"):
            self.sources[row["protocol_id"]][row["source_path"]] = row
        if not self.sources:
            raise ValueError("missing scg_lib_structs source inventory")
        self.paper = defaultdict(list)
        self.name_meta = read_json(
            repo / "paper/provenance/name_only/results_n1/input_manifest.json"
        )
        self.attested_files = {}
        for manifest, field in (
            ("camera_ready/input_manifest", "inputs"),
            ("run_variability/results/input_manifest", "protected_sha256"),
            ("name_only/results_n1/input_manifest", "inputs_sha256"),
        ):
            self.attested_files.update(
                read_json(repo / ("paper/provenance/" + manifest + ".json")).get(
                    field, {}
                )
            )
        self.prediction_pins = defaultdict(dict)
        for name, checksum in self.attested_files.items():
            if name.startswith("repo/runs/") and Path(name).name in {
                "t2_prediction.json",
                "t3_prediction.json",
            }:
                rel = name.removeprefix("repo/runs/")
                if "/artifacts/" in rel:
                    trial, tail = rel.split("/artifacts/", 1)
                    self.prediction_pins[trial]["artifacts/" + tail] = checksum
        for name, columns in (
            ("main/selected_protocol_scores", ["trial"]),
            ("repeats/execution_scores", ["trial"]),
            ("main/task1_recomputed", ["prediction_path"]),
            ("name_only/native_per_protocol", ["bundle_trial", "name_trial"]),
            ("model_harness/settings_by_trial", ["trial"]),
        ):
            for row in read_csv(repo / ("paper/data/" + name + ".csv")):
                for column in columns:
                    relative = row[column].removeprefix("repo/").removeprefix("runs/")
                    if column == "prediction_path":
                        relative = relative.split("/artifacts/")[0]
                    self.paper[relative].append(
                        {"table": name, "column": column, "row": row}
                    )

    def classify(self, path):
        rel = path.relative_to(self.runs)
        cfg = read_json(path / "config.json")
        result = read_json(path / "result.json")
        lock = read_json(path / "lock.json")
        cfg = cfg if "agent" in cfg else result.get("config") or cfg
        task = cfg.get("task") or lock.get("task") or result.get("task_id") or {}
        protocol = Path(
            str(task.get("path") or task.get("name") or path.name.split("__")[0])
        ).name
        agent = cfg.get("agent") or lock.get("agent") or {}
        harness = (
            agent.get("name")
            or agent.get("import_path")
            or (result.get("agent_info") or {}).get("name")
        )
        short = (harness or "").split(":")[-1]
        parts = rel.parts
        branch = parts[1] if len(parts) > 1 else "unknown"
        family = parts[0] if parts else "unknown"
        if family == "libgen" and branch in BASELINES:
            experiment = (
                "tasks23-name-only"
                if "name-only" in path.parent.name
                else "tasks23-baseline"
            )
        elif family == "libgen":
            experiment = "tasks23-" + branch
        elif family == "library_structure":
            experiment = "task1-" + ("round2" if "round2" in parts else "legacy")
        else:
            experiment = "unclassified"
        if protocol not in self.sources:
            reason = "unknown-protocol"
        elif short not in NATIVE:
            reason = "non-native" if short else "unknown-harness"
        elif experiment == "unclassified":
            reason = "unknown-study"
        else:
            reason = None
        error = result.get("exception_info") or {}
        return {
            "path": rel.as_posix(),
            "experiment_id": experiment,
            "protocol_id": protocol,
            "executor": scalar(harness),
            "model": scalar(agent.get("model_name")),
            "eligible": reason is None,
            "reason": reason,
            "kind": "trial",
            "status": "failed"
            if error
            else "completed"
            if result.get("finished_at")
            else "partial",
            "execution_id": scalar(result.get("id")),
            "job_id": scalar(cfg.get("job_id")),
            "config_unreadable": (path / "config.json").exists()
            and not read_json(path / "config.json"),
        }

    def inventory(self, root=None):
        root = root or self.runs
        if not root.resolve().is_relative_to(self.runs) or not root.is_dir():
            raise ValueError("expected an existing directory under runs")
        for directory, dirs, names in os.walk(root, followlinks=False):
            p = Path(directory)
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith("by_setting") and not (p / d).is_symlink()
            )
            rel = p.relative_to(self.runs)
            if (
                "config.json" in names
                or "__" in p.name
                and set(names) & {"lock.json", "trial.log", "result.json"}
            ):
                cfg = read_json(p / "config.json")
                if "agents" in cfg:
                    yield {
                        "path": rel.as_posix(),
                        "kind": "job",
                        "eligible": False,
                        "reason": "job-context",
                    }
                elif "agent" in cfg or "__" in p.name:
                    yield self.classify(p)
                    # Some consolidated histories nest earlier trial directories.
            elif "request.json" in names and rel.parts[:1] == ("library_structure",):
                yield {
                    "path": rel.as_posix(),
                    "kind": "direct-api",
                    "eligible": False,
                    "reason": "non-native",
                }

    def job_protocols(self, job):
        cfg = read_json(job / "config.json")
        agents = cfg.get("agents") or []
        names = [
            (a.get("name") or a.get("import_path") or "").split(":")[-1] for a in agents
        ]
        rel = job.relative_to(self.runs)
        if (
            not names
            or any(n not in NATIVE for n in names)
            or rel.parts[0] not in {"libgen", "library_structure"}
        ):
            return None
        tasks = [Path(t.get("path", "")).name for t in cfg.get("tasks", [])]
        for ds in cfg.get("datasets", []):
            if ds.get("task_names"):
                tasks.extend(ds["task_names"])
                continue
            if not ds.get("path"):
                return None
            dataset = Path(ds["path"])
            if not dataset.is_absolute():
                dataset = self.repo / dataset
            if not dataset.resolve().is_relative_to(self.repo) or not dataset.is_dir():
                return None
            tasks.extend(
                p.name for p in dataset.iterdir() if (p / "task.toml").is_file()
            )
        return (
            sorted(set(tasks))
            if tasks and all(t in self.sources for t in tasks)
            else None
        )

    def job(self, path):
        for parent in path.parents:
            if parent == self.runs:
                break
            if "agents" in read_json(parent / "config.json"):
                return parent
        return path.parent


def identities(row, job, config, lock):
    agent = config.get("agent") or lock.get("agent") or {}
    # Everything affecting an execution belongs in its frozen manifest. Use the
    # stable job identity and agent settings to distinguish multi-agent jobs.
    run = digest(canonical([row["experiment_id"], row["job_id"] or job, agent]))
    trial = (
        digest(canonical(["harbor", row["execution_id"]]))
        if row["execution_id"]
        else digest(canonical(["local-harbor", row["path"]]))
    )
    return run, trial
