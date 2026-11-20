"""Freeze the 20-protocol native-agent T2/T3 name-only control, without inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tomllib
from pathlib import Path

from libstruct_bench.cli import generate_libgen_tasks as gen
from libstruct_bench.libgen.scoring import derive_required_t2_ids, score_t2
from libstruct_bench.libgen.validation import validate_groundtruth_bundle
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / "benchmarks/tasks2_3/name_only"
SYSTEMS = {
    "codex": dict(adapter="CodexNameOnly", model="gpt-5.6-sol", version="0.147.0",
                  effort="max", family="codex", baseline=["libgen-gpt-5-6-sol", "libgen-gpt-5-6-sol-repeats"],
                  job="libgen-gpt-5-6-sol-name-only"),
    "claude-code": dict(adapter="ClaudeNameOnly", model="claude-opus-5", version="2.1.233",
                        effort="max", family="claude-code", baseline=["libgen-claude-code-opus-5", "libgen-claude-code-opus-5-repeats"],
                        job="libgen-claude-code-opus-5-name-only"),
    "antigravity-cli": dict(adapter="AntigravityNameOnly", model="google/gemini-3.7-flash-high", version="1.3.2",
                            effort="high", family="antigravity-cli", baseline=["libgen-gemini-3-7-flash-gcp-n3"],
                            job="libgen-gemini-3-7-flash-gcp-name-only"),
}
PROVIDER_HOSTS = ["api.openai.com", "chatgpt.com", "auth.openai.com", "api.anthropic.com",
                  "aiplatform.googleapis.com", "oauth2.googleapis.com"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def adapt_rules(original: str) -> str:
    # Exact blocks fail closed if the baseline rules change, keeping all other
    # instructions (including schemas/normalization) byte-for-byte intact.
    replacements = {
        "Use only the files preloaded under `/workspace/input/`. Read every listed paper,\nprotocol, supplement, table, spreadsheet, figure, and diagram. Do not browse the\nweb, use remembered kit sequences, inspect legacy `scg_lib_structs` pages, or\nlook for benchmark ground truth or prior answers.":
        "This is the protocol-name-only (parametric-knowledge control) condition.\nReconstruct the named protocol/version using internal knowledge only. No\nprotocol documents or references are supplied. Do not browse, search, retrieve,\ncontact other agents or APIs, inspect caches, or seek benchmark ground truth\nor prior answers. Use local file/shell tools only to read these generic rules\nand schemas and to construct, write, and validate the structured outputs.",
        "For PDFs, prefer native text, layout-sorted text blocks, and table cells. Use\nDocling with OCR disabled, PyMuPDF, and pypdf as complementary parsers. Render\npages for visual inspection of figures and diagrams, but do not use OCR-derived\nsequence text. Use OpenPyXL for spreadsheets and `antiword` for legacy Word\ntables when applicable.":
        "No PDFs, supplements, tables, spreadsheets, figures, or cached references\nare available. Do not fabricate citations or claim to have inspected sources.",
        "- Preserve source-visible oligo sequences and their stated 5′→3′, 3′→5′, or\n  unknown orientation. Do not silently reverse-complement or complete them.":
        "- Reconstruct oligo sequences and their 5′→3′, 3′→5′, or unknown orientation\n  from internal knowledge. Do not silently reverse-complement them.",
        "- Do not invent vendor or kit sequences absent from the provided sources.":
        "- Use internally known vendor or kit sequences; do not invent unknown bases.\n  Express uncertainty using the existing schema where possible.",
        "when the sources support them.": "when supported by your internal knowledge.",
        "unless a source explicitly says that complex": "unless you know that complex",
        "- Before returning the graph, fact-check every state and transition directly\n  against the primary PDFs, supplementary tables/spreadsheets, and relevant\n  figures. Do not accept a plausible mechanistic step merely because adjacent\n  predicted states are internally consistent.":
        "- Before returning the graph, check every state and transition against your\n  internal knowledge of the named protocol. Do not accept a plausible\n  mechanistic step merely because adjacent states are internally consistent.",
    }
    for before, after in replacements.items():
        if original.count(before) != 1:
            raise ValueError(f"Baseline rules changed; review name-only adaptation: {before[:70]}")
        original = original.replace(before, after)
    return original


DOCKERFILE = """FROM python:3.12-slim
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends bash curl ca-certificates ripgrep unzip \\
    && find /etc/apt -type f \\( -name '*.list' -o -name '*.sources' \\) \\
       -exec sed -i -e 's|http://deb.debian.org|https://deb.debian.org|g' \\
       -e 's|http://security.debian.org|https://security.debian.org|g' {} + \\
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir jsonschema
COPY rules.md name_only_inventory.json /workspace/
COPY libstruct_bench /workspace/libstruct_bench
COPY schemas /workspace/schemas
RUN mkdir -p /logs/artifacts
"""


def find_prediction(trial: Path, filename: str) -> Path | None:
    found = list((trial / "artifacts").rglob(filename))
    if len(found) > 1 and len({sha(p) for p in found}) != 1:
        raise ValueError(f"Conflicting artifact copies: {trial}/{filename}")
    return sorted(found)[0] if found else None


def freeze_baselines(protocols: list[dict]) -> dict:
    ids = {p["protocol_id"] for p in protocols}
    baseline = {}
    for system, spec in SYSTEMS.items():
        rows = []
        for job_index, job in enumerate(spec["baseline"]):
            directory = ROOT / "runs/libgen" / spec["family"] / job
            for path in sorted(directory.glob("*/result.json")):
                result = json.loads(path.read_text())
                pid = result["task_name"].removeprefix("sequencing/libgen-")
                if pid not in ids:
                    continue
                agent = result["config"]["agent"]
                if (agent["model_name"].removeprefix("google/") != spec["model"].removeprefix("google/")
                    or agent["kwargs"].get("version") != spec["version"]
                    or agent["kwargs"].get("reasoning_effort") != spec["effort"]):
                    raise ValueError(f"Baseline setting mismatch: {path}")
                predictions = {}
                for task in ("t2", "t3"):
                    p = find_prediction(path.parent, f"{task}_prediction.json")
                    predictions[task] = {"path": str(p), "sha256": sha(p)} if p else None
                rows.append(dict(protocol_id=pid, trial=str(path.parent), job_index=job_index,
                                 started_at=result["started_at"], predictions=predictions,
                                 result_sha256=sha(path)))
        for pid in ids:
            count = sum(r["protocol_id"] == pid for r in rows)
            if count != 3:
                raise ValueError(f"Expected three frozen baseline attempts for {system}/{pid}; found {count}")
        baseline[system] = sorted(rows, key=lambda r: (r["protocol_id"], r["job_index"], r["started_at"]))
    return baseline


def prepare(out: Path, groundtruth_root: Path, gcp_project: str) -> dict:
    if (out / "experiment.json").exists():
        from libstruct_bench.harbor_agents.name_only import validate_lock
        return validate_lock(out / "experiment.json")
    if (out / 'tasks').exists():
        raise ValueError('Unfinished preparation found; inspect it before replacing task files')
    protocols = json.loads((ROOT / "paper/benchmarks/task1/name_only.json").read_text())["protocols"]
    assert len(protocols) == 20
    assert {p["protocol_id"] for p in protocols} == {
        p["protocol_id"] for p in json.loads((ROOT / "paper/benchmarks/tasks2_3/protocols.json").read_text())["protocols"]}
    rules = adapt_rules((ROOT / "paper/benchmarks/tasks2_3/rules.md").read_text())
    baseline = freeze_baselines(protocols)
    truths = {}
    for p in protocols:
        pid = p["protocol_id"]
        files = {k: groundtruth_root / pid / name for k, name in
                 zip(("T1", "T2", "T3"), gen.GROUNDTRUTH_FILENAMES)}
        docs = {k: json.loads(f.read_text()) for k, f in files.items()}
        validate_groundtruth_bundle(docs, protocol_id=pid, schema_root=ROOT / "schemas")
        metrics, _ = score_t2([], docs["T2"]["oligos"], required_oligo_ids=derive_required_t2_ids(docs["T3"]))
        truths[pid] = {"files": files, "hashes": {f.name: sha(f) for f in files.values()},
                       "n_required": int(metrics["required_family_count"])}
    out.mkdir(parents=True, exist_ok=True)
    policy = json.loads((ROOT / "benchmarks/molecular_workflow_reconstruction/network-policy.json").read_text())
    policy["provider_hosts"] = PROVIDER_HOSTS
    policy.pop("qwen_openai_base_url", None)
    policy["agent_phase_transition"] = "explicit_connect_preflight_before_model"
    for protocol in protocols:
        pid = protocol["protocol_id"]
        task = out / "tasks" / pid
        env, tests = task / "environment", task / "tests"
        env.mkdir(parents=True)
        tests.mkdir()
        version = protocol["version"] or "not separately specified"
        instruction = f"""# T2/T3 protocol understanding: {protocol['canonical_name']}

Read `/workspace/rules.md` completely before starting.
Condition: protocol-name-only (parametric-knowledge control).
Protocol name: {protocol['canonical_name']}
Protocol version: {version}
No protocol documents or external knowledge are provided. Reconstruct from
internal knowledge only.

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `{pid}`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for benchmark ground truth, legacy curation,
audit records, or prior answers.
"""
        (task / "instruction.md").write_text(instruction)
        # Keep baseline time, CPU and memory budgets; verifier is offline and separate.
        config = (ROOT / "benchmarks/tasks2_3/tasks" / pid / "task.toml").read_text()
        config = config.replace(f'name = "sequencing/libgen-{pid}"', f'name = "sequencing/libgen-name-only-{pid}"')
        parsed = tomllib.loads(config)
        for key, value in parsed["metadata"].items():
            if key in {"input_repo", "input_revision", "source_delivery", "source_manifest_sha256",
                       "groundtruth_repo", "groundtruth_revision", "groundtruth_source_bundle_sha256",
                       "groundtruth_bundle_sha256", "groundtruth_transform", "network_policy_sha256"}:
                config = config.replace(f"{key} = {json.dumps(value)}\n", "")
        config = config.replace('[metadata]\n', '[metadata]\ncondition = "protocol-name-only"\n')
        config = config.replace('[verifier.environment]\nnetwork_mode = "public"', '[verifier.environment]\nnetwork_mode = "no-network"')
        config = config.split('[verifier.environment.env]')[0]
        (task / "task.toml").write_text(config)
        (env / "rules.md").write_text(rules)
        (env / "Dockerfile").write_text(DOCKERFILE)
        gen._copy_agent_runtime(ROOT / "src/libstruct_bench", env / "libstruct_bench")
        shutil.copytree(ROOT / "schemas/benchmark", env / "schemas/benchmark")
        # Record only files copied into the agent workspace (no protocol evidence).
        inventory = {str(p.relative_to(env)): sha(p) for p in sorted(env.rglob('*'))
                     if p.is_file() and p.name != 'Dockerfile'}
        write_json(env / "name_only_inventory.json", inventory)
        for name in ("ProviderProxy.Dockerfile", "provider_egress_proxy.py", "docker-compose.yaml"):
            shutil.copy2(ROOT / "benchmarks/molecular_workflow_reconstruction/docker_network" / name, env / name)
        # Do not carry the baseline smoke service's host mounts into inference jobs.
        import yaml
        compose = yaml.safe_load((env / "docker-compose.yaml").read_text())
        compose["services"].pop("network-smoke", None)
        (env / "docker-compose.yaml").write_text(yaml.safe_dump(compose, sort_keys=False))
        write_json(env / "egress_policy.json", policy)
        (tests / "Dockerfile").write_text(gen._tests_dockerfile())
        (tests / "grade.py").write_text(gen._grade_py())
        (tests / "test.sh").write_text(gen._test_sh(pid, "", "", truths[pid]["hashes"], local_only=True))
        (tests / "test.sh").chmod(0o755)
        (tests / "groundtruth").mkdir()
        for source in truths[pid]["files"].values():
            shutil.copy2(source, tests / "groundtruth" / source.name)
        shutil.copytree(ROOT / "src/libstruct_bench", tests / "libstruct_bench", ignore=gen._ignore_python_cache)
        shutil.copytree(ROOT / "schemas", tests / "schemas")
    for system, spec in SYSTEMS.items():
        kwargs = dict(version=spec["version"], reasoning_effort=spec["effort"],
                      experiment_lock=str(out / "experiment.json"))
        if system == "antigravity-cli":
            kwargs.update(gcp_project=gcp_project, gcp_location="global")
        config = dict(job_name=spec["job"], jobs_dir=str(ROOT / "runs/libgen" / spec["family"]),
                      n_attempts=1, n_concurrent_trials=4, retry={"max_retries": 0},
                      agents=[dict(import_path=f"libstruct_bench.harbor_agents.name_only:{spec['adapter']}",
                                   model_name=spec["model"], kwargs=kwargs)],
                      datasets=[dict(path=str(out / "tasks"))], environment={"type": "docker"})
        write_json(out / f"{system}.json", config)
    lock = dict(schema_version=1, condition="protocol-name-only (parametric-knowledge control)",
                benchmark_version=LIBGEN_BENCHMARK_VERSION, protocols=protocols, systems=SYSTEMS,
                groundtruth={pid: {"sha256": t["hashes"], "n_required": t["n_required"]} for pid, t in truths.items()},
                baseline=baseline, baseline_policy="Mean of three frozen attempts per protocol, never best-of-three.",
                t2_zero_required_policy="Not applicable; exclude from T2 means, differences and wins; retain T3 and failures.",
                output_failure_policy="Primary comparisons require valid completions in both conditions (all three frozen PDF replicates). Report failures separately. Secondary scheduled-attempt metrics assign completed failures zero; never assign missing/unfinished attempts zero.",
                gcp_project=gcp_project, approved_groundtruth_source=str(groundtruth_root),
                controller_files={str(ROOT / p): sha(ROOT / p) for p in (
                    'src/libstruct_bench/harbor_agents/name_only.py',
                    'src/libstruct_bench/harbor_agents/antigravity_gcp.py',
                    'src/libstruct_bench/harbor_agents/antigravity_pinned.py',
                    'src/libstruct_bench/cli/prepare_libgen_name_only.py',
                    'src/libstruct_bench/cli/analyze_libgen_name_only.py')},
                generated_files={str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
    write_json(out / "experiment.json", lock)
    return lock


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=DEFAULT_OUT)
    p.add_argument('--groundtruth-root', type=Path, default=Path('~/playground/protocols-test/ground_truth').expanduser())
    p.add_argument('--gcp-project', default='gen-lang-client-0325887617')
    args = p.parse_args(argv)
    lock = prepare(args.out.resolve(), args.groundtruth_root.resolve(), args.gcp_project)
    print(f"Locked {len(lock['protocols'])} protocols × 3 native systems, one fresh attempt each.")
    print(f"Configs: {args.out}")
    print("No model calls made. Existing predictions and ground truth are unchanged.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
