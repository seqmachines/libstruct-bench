# Task 1 — final-library reconstruction

This folder contains the manuscript's 20-protocol Task 1 configurations,
previously under `library_structure/round2/`. The document-supplied API and
native-agent conditions use the same PDF inventory. The name-only control uses
separate API executions with the protocol name/version and no documents.

| File | Purpose |
| --- | --- |
| `protocols.json` | Pinned 20-protocol PDF inventory, API model/settings defaults and recorded attachment exclusions. |
| `codex.json`, `claude-code.json`, `antigravity-cli.json` | Native-agent Harbor configurations: one attempt per protocol, requested high reasoning. |
| `name_only.json` | Protocol-name-only API control, exact model/settings and historical input hashes. |
| `provenance/original_protocols.json` | Earlier input inventory used to verify that only seven XLSX attachments from six protocols were excluded; all 20 protocols were retained. |
| `../shared/protocol_processing_rules.md` | Shared task instructions. |

The configured API models are GPT-5.6 Sol, Claude Opus 5 and Gemini 3.7 Flash,
with requested high reasoning and a 64,000 completion-token cap. Native CLI
versions are Codex 0.147.0, Claude Code 2.1.233 and Antigravity 1.3.2. The native
runs do not have the API's total completion-token cap; a shared reasoning label
does not imply equal compute. Earlier Figure 3 Antigravity version provenance
is distinct and remains in the locked results metadata.

## Reproduce saved results

From the repository root, without inference:

```bash
python paper/reproduce.py --statistics
```

Final reference-corrected Task 1 scores and Bonferroni tests are under
[`paper/data/`](../../paper/README.md). The older labels used during execution
were not changed in-place. Exact counts and sequence similarity have separate
definitions; exact reconstruction also requires matching library counts.

## Execution tools

```bash
python -m libstruct_bench.cli.generate_library_v0_tasks --help
python -m libstruct_bench.cli.run_openrouter_library_v0 --help
python -m libstruct_bench.cli.run_task1_name_only --help
python -m libstruct_bench.cli.task1_name_only_sandbox --help
```

The Task 1 generator/API runner default to `benchmarks/task1/protocols.json`.
Use approved references and lawfully acquired, hash-verified source copies.
Source PDFs and generated tasks are not redistributed. Historical input-repo
identifiers are provenance, not a grant of redistribution rights.

Native configs intentionally retain their original `runs/library_structure/`
output and generated-task locations. Existing runs have not been moved. For
any new execution, choose a fresh output/job path and prepare its private task
context first. Docker/Harbor and the corresponding provider credentials are
required for agent runs. Table checks above need none of them.

`name_only.json` still pins local reference/provenance files in `analysis/`.
These are prerequisites for a new run and are not all distributed publicly;
the lock must pass rather than be bypassed. Moving configuration files does
not authorize replacing an existing experiment lock or rerunning old cases.
