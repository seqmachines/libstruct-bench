"""Create a local, linked run browser without moving or rewriting executions.

Grouping uses recorded configuration, not inferred experimental equivalence.
Raw traces stay in their original directories and are never copied into HTML.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r"secret|password|credential|api.?key|auth.?json|(?:access|refresh|oauth|auth|bearer|session)[_-]?token|^token$|token_path", re.I)
NATIVE = {"codex", "claude-code", "antigravity-cli", "kimi-code", "pi", "mini-swe-agent"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def slug(value):
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value)).strip("-.")[:90] or "unknown"


def scrub(value):
    """Credential values do not appear in the local settings manifest either."""
    if isinstance(value, dict):
        return {k: "[credential omitted]" if SECRET.search(k) else scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def walk_files(root):
    for directory, dirs, names in os.walk(root, followlinks=False):
        # Generated views are never input records, including on a later rebuild.
        dirs[:] = sorted(d for d in dirs if not d.startswith("by_setting")
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink():
                yield path


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in walk_files(root)}


def read(path, errors, hashes):
    try:
        data = path.read_bytes()
        hashes[path] = hashlib.sha256(data).hexdigest()
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("expected an object")
        return result
    except (OSError, ValueError) as exc:
        errors.append({"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                       "error_type": type(exc).__name__})
        return {}


def protocol(config, result, path):
    task = config.get("task", {})
    if task.get("path"):
        return Path(task["path"]).name
    return result.get("protocol_id") or path.name.split("__")[0]


def category(path, runs, job):
    parts = path.relative_to(runs).parts
    if parts[0] == "library_structure":
        edition = "paper" if "round2" in parts else "legacy"
        condition = "name_only" if "name_only" in parts else "pdf"
        return "task1", f"{edition}_{condition}"
    if parts[0] == "libgen" and len(parts) > 1:
        branch = parts[1]
        if branch in NATIVE:
            return "tasks2_3", "name_only" if "name-only" in str(path) else "bundle"
        if branch == "external-knowledge":
            return "tasks2_3", "knowledge_" + job.name.split("--", 1)[-1]
        # Keep learning lineages, checkpoints, and stages distinct. Unidentified
        # workflow settings remain source-job-specific instead of being pooled.
        return "tasks2_3", f"{branch}__{job.name}"
    return "other", job.name


def setting(config, result):
    keys = ("agent", "environment", "verifier", "install_only", "timeout_multiplier",
            "agent_timeout_multiplier", "verifier_timeout_multiplier", "agent_setup_timeout_multiplier",
            "environment_build_timeout_multiplier", "extra_instruction_paths")
    material = scrub({key: config.get(key, {"not_recorded": True}) for key in keys})
    info = result.get("agent_info") or {}
    material["recorded_agent_info"] = scrub(info)
    return material


def selected_memberships(repo):
    selected = defaultdict(set)

    def resolve(value):
        if value.startswith("repo/"):
            value = value[5:]
        path = Path(value)
        return path if path.is_absolute() else repo / path

    def add(file, columns, label):
        path = repo / "paper/data" / file
        if not path.exists():
            return
        with path.open() as handle:
            for row in csv.DictReader(handle):
                for col, suffix, is_file in columns:
                    if row.get(col):
                        target = resolve(row[col])
                        if is_file:
                            target = target.parent
                            if target.name in {"artifacts", "output", "outputs", "agent"}:
                                parent = next((p for p in target.parents if (p / "config.json").is_file()), None)
                                if parent is not None:
                                    target = parent
                        selected[target].add(label(row) + suffix)

    add("main/panel_membership.csv", [("trial", "", False)], lambda r: f"{r['panel']} / {r['group']}")
    add("repeats/execution_scores.csv", [("trial", "", False)], lambda r: f"GPT repeats / {r['group']}")
    add("name_only/native_per_protocol.csv", [("bundle_trial", " / bundle", False), ("name_trial", " / name", False)], lambda r: "name-only comparison")
    add("main/task1_recomputed.csv", [("prediction_path", "", True)], lambda r: "Task 1 / " + r["interface"])
    control = repo / "paper/data/name_only/task1_per_protocol.csv"
    if control.exists():
        with control.open() as handle:
            for row in csv.DictReader(handle):
                case = repo / "runs/library_structure/round2/name_only" / slug(row["model"]) / row["protocol_id"]
                if case.is_dir():
                    selected[case].add("Task 1 / name-only API control")
    return selected


def artifacts(path, all_files, recursive=True):
    found = []
    for p in all_files:
        if not p.is_relative_to(path) or (not recursive and p.parent != path):
            continue
        name = p.name.lower()
        if "prediction" in name and p.suffix == ".json":
            kind = "prediction"
        elif ("trajectory" in name or "transcript" in name or name in
              {"codex.txt", "claude-code.txt", "kimi-cli.txt", "antigravity-cli.txt", "raw_response.txt"}):
            kind = "trace"
        elif name in {"result.json", "reward.json", "details.json", "error_analysis.json", "trial.log"}:
            kind = "result"
        else:
            continue
        found.append({"path": str(p), "relative_path": p.relative_to(path).as_posix(), "kind": kind})
    return sorted(found, key=lambda item: (item["kind"], item["relative_path"]))


def discover(runs, repo, all_files, errors, hashes):
    configs = {}; jobs = {}; trials = {}
    for path in all_files:
        if path.name != "config.json":
            continue
        config = read(path, errors, hashes)
        if isinstance(config.get("agents"), list):
            jobs[path.parent] = config
        elif "agent" in config and "task" in config:
            trials[path.parent] = config
        configs[path.parent] = config
    selected = selected_memberships(repo)
    records = []; represented_jobs = set()
    for path, config in sorted(trials.items()):
        job = next((p for p in path.parents if p in jobs), path.parent)
        represented_jobs.add(job)
        result = read(path / "result.json", errors, hashes) if (path / "result.json").exists() else {}
        agent = config.get("agent") or {}; kwargs = agent.get("kwargs") or {}
        task, condition = category(path, runs, job)
        material = setting(config, result)
        # Full material controls equality; only safe scalar metadata is displayed.
        exception = (result.get("exception_info") or {}).get("exception_type")
        records.append(dict(source=path, job=job, kind="harbor_trial", task=task, condition=condition,
            model=agent.get("model_name") or "not-recorded",
            harness=agent.get("name") or str(agent.get("import_path") or "not-recorded").split(":")[-1],
            version=str((result.get("agent_info") or {}).get("version") or kwargs.get("version") or "not-recorded"),
            reasoning=str(kwargs.get("reasoning_effort") or kwargs.get("thinking") or "not-recorded"),
            protocol=protocol(config, result, path),
            status=exception or ("completed" if result.get("finished_at") else "unfinished/no result"),
            started_at=str(result.get("started_at") or ""),
            execution_id=str(result.get("id") or ""), task_checksum=str(result.get("task_checksum") or ""),
            material=material, selected=sorted(selected.get(path, [])),
            archived=any("archive" in p.lower() or p in {"resume_snapshots", "consolidations", "rescore-quarantine"} for p in path.parts)))
    # Task 1 API cases lack Harbor config.json. Requests may embed huge PDFs:
    # never read them. Keep different source batches separate when their exact
    # request settings have not been parsed/verified.
    roots = [runs / "library_structure/direct_api", runs / "library_structure/round2/direct_api",
             runs / "library_structure/round2/name_only"]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*/*")):
            if not path.is_dir() or path.is_symlink() or not any((path / n).exists() for n in ("request.json", "prediction.json", "result.json", "error.json")):
                continue
            result = read(path / "result.json", errors, hashes) if (path / "result.json").exists() else {}
            task, condition = category(path, runs, root)
            material = {"source_batch": root.relative_to(runs).as_posix(), "model_directory": path.parent.name,
                        "request_settings_not_parsed": True, "provider": result.get("provider"),
                        "sandboxed": result.get("sandboxed")}
            records.append(dict(source=path, job=root, kind="task1_api", task=task, condition=condition,
                model=result.get("model") or path.parent.name, harness="API", version="not-recorded",
                reasoning="see experiment/request metadata", protocol=path.name,
                status=result.get("status") or ("prediction saved" if (path / "prediction.json").exists() else "no prediction"),
                started_at=str(result.get("started_at_unix") or ""), execution_id="", task_checksum="",
                material=material, selected=sorted(selected.get(path, [])), archived=False))
    for path, config in sorted(jobs.items()):
        if path in represented_jobs:
            continue
        task, condition = category(path, runs, path)
        records.append(dict(source=path, job=path, kind="job_without_indexed_trials", task=task, condition=condition,
            model="see job config", harness="job", version="not-recorded", reasoning="not-recorded",
            protocol="job", status="no indexed trial configuration", started_at="", execution_id="", task_checksum="",
            material={"source_job": path.relative_to(runs).as_posix()}, selected=[], archived="archive" in str(path)))
    return records, len(jobs), selected


def href(path, output):
    return html.escape(quote(os.path.relpath(path, output), safe="/"), quote=True)


def write_html(output, records, workflow_links):
    esc = html.escape
    lines = []
    for row in records:
        links = [f'<a href="{href(output / row["link"], output)}">Open run folder</a>']
        for kind in ("prediction", "trace", "result"):
            subset = [a for a in row["artifacts"] if a["kind"] == kind]
            for artifact in subset[:3]:
                path = output / row["link"] / artifact["relative_path"]
                links.append(f'<a href="{href(path, output)}">{esc(artifact["relative_path"])}</a>')
            if len(subset) > 3:
                links.append(f'<span>+{len(subset)-3} more {kind} files in folder</span>')
        tags = " · ".join(row["selected"])
        flags = ("Historical copy · " if row["archived"] else "") + ("Repeated execution ID · " if row["execution_id_occurrences"] > 1 else "")
        lines.append(f'<tr data-selected="{bool(tags)}" data-trace="{bool(row["trace_count"])}">'
            f'<td>{esc(row["task"])}<small>{esc(row["condition"])}</small></td>'
            f'<td><b>{esc(row["harness"])}</b><small>{esc(row["model"])}</small><small>CLI {esc(row["version"])} · {esc(row["reasoning"])}</small>'
            f'<small>Setting {row["setting_id"][:10]}</small></td>'
            f'<td><b>{esc(row["protocol"])}</b><small>{esc(row["source"])}</small></td>'
            f'<td>{esc(row["status"])}<small>{esc(flags)}</small><small>{esc(tags)}</small></td>'
            f'<td class="links">{"".join(links)}</td></tr>')
    workflows = "".join(f'<li><a href="{href(output / link, output)}">{esc(label)}</a></li>' for label, link in workflow_links)
    document = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LibStructBench · saved runs</title><style>
body{font:14px/1.5 system-ui,sans-serif;color:#202b36;background:#f5f6f8;margin:0;padding:28px}
main{max-width:1600px;margin:auto}h1{margin:0;font-size:27px}p{max-width:1050px;color:#52606d}
.toolbar{display:flex;gap:22px;align-items:center;flex-wrap:wrap;margin:22px 0}input[type=search]{padding:11px;width:min(620px,90vw);border:1px solid #aeb8c2;border-radius:6px}
table{border-collapse:collapse;width:100%;background:white}th{text-align:left;background:#e8edf1;position:sticky;top:0;padding:11px}
td{padding:11px;vertical-align:top;border-bottom:1px solid #e4e8ec}small{display:block;color:#607080;max-width:390px;overflow-wrap:anywhere;font-size:11px}
a{color:#245b84;text-decoration:none}a:hover{text-decoration:underline}.links a,.links span{display:block;font-size:12px;overflow-wrap:anywhere}.links{min-width:185px}
details{margin:16px 0}ul{columns:3}#count{font-weight:600}tr[hidden]{display:none}
</style><main><h1>Saved runs, grouped by recorded setting</h1>
<p>These are links to the original execution folders. No predictions, scores, or traces were moved or copied.
Configuration groups are browsing aids, not evidence of controlled seeds or identical runtime context.
Counts refer to directory records, including archived copies and retries—not independent attempts.</p>
<details><summary>Complete workflow folders, including learning and review records</summary><ul>WORKFLOWS</ul></details>
<div class="toolbar"><input id="query" type="search" placeholder="Filter protocol, model, checkpoint, condition or source folder…">
<label><input id="selected" type="checkbox"> Paper-selected / GPT repeat records</label>
<label><input id="trace" type="checkbox"> Has trace files</label><span id="count"></span></div>
<table><thead><tr><th>Experiment</th><th>Model / harness</th><th>Protocol / original location</th><th>Status / selection</th><th>Files</th></tr></thead><tbody>ROWS</tbody></table>
<p>Open trace/prediction links locally. Folder links expose all original artifacts. Links are live: editing through them edits the original; make separate copies for proofreading changes. This browser and raw traces stay local under ignored runs/.</p>
</main><script>
const rows=[...document.querySelectorAll('tbody tr')], q=document.querySelector('#query'), s=document.querySelector('#selected'), t=document.querySelector('#trace');
function filter(){let n=0;const words=q.value.toLowerCase().split(/\s+/).filter(Boolean);for(const r of rows){const text=r.textContent.toLowerCase();r.hidden=!(words.every(w=>text.includes(w))&&(!s.checked||r.dataset.selected==='True')&&(!t.checked||r.dataset.trace==='True'));if(!r.hidden)n++;}document.querySelector('#count').textContent=`${n} / ${rows.length} records`;}
[q,s,t].forEach(x=>x.addEventListener('input',filter));filter();</script></html>'''
    (output / "index.html").write_text(document.replace("WORKFLOWS", workflows).replace("ROWS", "".join(lines)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    parser.add_argument("--output", type=Path, default=ROOT / "runs/by_setting")
    args = parser.parse_args(); runs = args.runs_root.resolve(); output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Use a new output directory; existing indexes and run folders are never overwritten")
    if not output.is_relative_to(runs) or not output.name.startswith("by_setting"):
        raise ValueError("Output must be a new by_setting* directory under runs-root, excluded from source scans")
    before = snapshot(runs); files = [runs / name for name in before]; errors = []; hashes = {}
    records, job_count, selected = discover(runs, ROOT, files, errors, hashes)
    ids = Counter(r["execution_id"] for r in records if r["execution_id"])
    source_dirs = {r["source"] for r in records}
    files_by_source = defaultdict(list)
    for file in files:
        for parent in file.parents:
            if parent in source_dirs:
                files_by_source[parent].append(file)
            if parent == runs:
                break
    output.mkdir(parents=True)
    seen_settings = {}; indexed_sources = set()
    for row in records:
        source = row["source"]; indexed_sources.add(source)
        identity = {"task": row["task"], "condition": row["condition"], "configuration": row.pop("material")}
        key = digest(identity); row["setting_id"] = key
        system = slug(row["harness"] + "--" + row["model"])
        parent = output / row["task"] / slug(row["condition"]) / system / key[:12]
        parent.mkdir(parents=True, exist_ok=True)
        if key not in seen_settings:
            # Do not expose arbitrary config text: only grouping hash and display
            # fields, with pointers to original config for local inspection.
            (parent / "setting.json").write_text(json.dumps({"setting_id": key,
                "task": row["task"], "condition": row["condition"], "model": row["model"],
                "harness": row["harness"], "version": row["version"], "reasoning": row["reasoning"],
                "definition": "Canonical recorded agent/environment/verifier/timeouts/context; credential fields omitted. Task-specific source checksums are per-record. Missing settings are not assumed equal to explicit defaults."}, indent=2) + "\n")
            seen_settings[key] = identity
        elif seen_settings[key] != identity:
            raise RuntimeError("Configuration hash collision")
        leaf = parent / slug(row["protocol"]) / (slug(source.name) + "--" + digest(str(source))[:8])
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.symlink_to(os.path.relpath(source, leaf.parent), target_is_directory=True)
        assert leaf.resolve() == source
        row["link"] = leaf.relative_to(output).as_posix()
        row["artifacts"] = artifacts(source, files_by_source[source], row["kind"] != "job_without_indexed_trials")
        row["trace_count"] = sum(a["kind"] == "trace" for a in row["artifacts"])
        row["prediction_count"] = sum(a["kind"] == "prediction" for a in row["artifacts"])
        row["execution_id_occurrences"] = ids.get(row["execution_id"], 0)
        row["source"] = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else str(source)
        row["job"] = str(row["job"])
    records.sort(key=lambda r: (r["task"], r["condition"], r["harness"], r["model"], r["protocol"], r["started_at"], r["source"]))
    workflow_links = []
    for root in (runs / "libgen", runs / "library_structure"):
        if not root.exists():
            continue
        for source in sorted(root.iterdir()):
            if not source.is_dir() or source.is_symlink() or source.name.startswith("."):
                continue
            link = output / "workflow_folders" / root.name / source.name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(source, link.parent), target_is_directory=True)
            workflow_links.append((source.relative_to(runs).as_posix(), link.relative_to(output).as_posix()))
    missing = sorted(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p) for p in selected if p not in indexed_sources)
    report = {"directory_records": len(records), "recorded_setting_groups": len(seen_settings),
        "harbor_jobs_found": job_count, "records_with_traces": sum(r["trace_count"] > 0 for r in records),
        "duplicate_execution_ids": sum(n > 1 for n in ids.values()), "read_errors": errors,
        "selected_paths_without_indexed_record": missing, "original_file_count": len(before),
        "original_inventory_sha256": digest(before),
        "note": "Local links only. No claim that directory counts equal independent attempts. API batches remain separate without parsing document-bearing requests."}
    (output / "inventory.json").write_text(json.dumps({"summary": report, "records": records}, indent=2) + "\n")
    columns = [k for k in records[0] if k not in {"artifacts", "selected"}] if records else ["source", "link"]
    with (output / "runs.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns + ["selected", "artifact_paths"])
        writer.writeheader()
        writer.writerows({**{k: r[k] for k in columns}, "selected": " | ".join(r["selected"]),
                          "artifact_paths": " | ".join(a["relative_path"] for a in r["artifacts"])} for r in records)
    write_html(output, records, workflow_links)
    (output / "README.md").write_text("# Local run browser\n\nOpen `index.html`, browse task/condition/model folders, or read `runs.csv` / `inventory.json`.\n\nAll execution folders are relative symbolic links. Originals, scores and traces remain in place. Do not edit originals through these links; create separate proofreading copies.\n\nGroups reflect saved settings, not independent seeds or demonstrated equivalence. Retries, archived copies, different timeouts, harness versions and learning contexts remain identifiable. Selection badges come from existing paper tables; no trial is selected or rescored here.\n\nSome API settings are not parsed because request files embed documents. Those cases are grouped only within their original API batch/model. The index does not include transcript text. It is local and ignored by Git.\n\nRebuild into a new `runs/by_setting*` folder with `python tools/build_run_browser.py --output <new-folder>`. The tool refuses to overwrite any existing directory.\n")
    after = snapshot(runs)
    if before != after:
        raise RuntimeError("Original file inventory changed during indexing; inspect concurrent activity")
    for path, expected in hashes.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Read source changed during indexing: {path}")
    print(json.dumps({k: v for k, v in report.items() if k not in {"read_errors", "selected_paths_without_indexed_record"}}, indent=2))
    print("Read errors:", len(errors), "; selected paths without indexed record:", len(missing))
    print("Original file sizes/mtimes and all read JSON hashes unchanged.")
    print("Browser:", output / "index.html")


if __name__ == "__main__":
    main()
