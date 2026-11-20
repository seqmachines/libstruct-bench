# Locked manuscript results

This package exports existing numerical analyses without rescoring, inference,
or checkpoint reselection. The final Tasks 2–3 verifier is 4.7.0 and the approved
reference set is `approved-20261010-b672aab997a2fdc0`. C30 remains the checkpoint
selected historically under the earlier validation criterion.

## Safe reproduction entry point

From the repository root:

```bash
python paper/reproduce.py
python paper/reproduce.py --statistics
```

The second command needs NumPy (`pip install -r paper/requirements.txt`). It
checks exported SHA-256 hashes, rederives aggregate means and repeat ranges,
and reproduces the final name-only and model–harness primary paired tests and
bootstrap intervals. It does not write data, run an agent, alter scientific
weights, or treat weight draws as new model runs. An optional `--output` path
writes a **new** verification report and refuses to overwrite a file.

The checked comparisons use the saved ordering, seeds, resample counts, and
Bonferroni families. Main-table confidence intervals, secondary mixed models,
figure rendering, and raw-prediction scoring are not rerun by this command.
Their original programs and saved outputs are retained separately.

## Authoritative tables

| Directory | Definition and precedence |
| --- | --- |
| `data/main/` | Camera-ready Figure 3/4 protocol scores, reference audit, completion counts, cost and discrepancy profiles. In `results.csv.gz`, select `config_id=original` for the main results. |
| `data/sensitivity/` | All 640 scoring configurations; each weight vector varied separately, plus the 3×3 state/transition threshold grid. Ranges are scoring sensitivity, not CIs or model replications. |
| `data/name_only/` | Final **n=1 versus n=1** paired analysis and portrait LaTeX tables. Bundle is the original Figure 3a selected execution. Includes the verified Claude SHARE-seq retry. These tables supersede earlier three-bundle-versus-one summaries. |
| `data/repeats/` | Three GPT/Codex executions per protocol, 60 total. Task 2: 19 informative protocols; Task 3: 20. Original is part of the three-execution mean; no independent-means test. |
| `data/model_harness/` | Fully crossed GPT/Claude/Gemini × Pi/mini-SWE design, native contrasts, mixed-effects and blocked analyses. Historical Holm columns remain identifiable provenance. |
| `data/bonferroni/` | Final model–harness primary and Task 1 API/agent multiplicity adjustments. **Use these Bonferroni columns for the manuscript**, not historical Holm columns. |
| `data/secondary/` | Final correction of 12 secondary model–harness omnibus tests. |

Use `data/main/panel_membership.csv` for actual protocol membership. The
five-protocol external-knowledge pilot and the learning validation panel must
not be treated as the same panel. Primary Task 2 means exclude `N_req=0`;
`data/main/zero_required.csv` preserves their separate diagnostics. Task 3
populations are unchanged. Task 1 PDF/name controls are API executions;
Tasks 2–3 name-only controls are native-agent executions.

The descriptive discrepancy categories are not independent biological error
rates: state alignment affects downstream edge agreement. Neither the scoring
sensitivity study nor the GPT repeat analysis establishes robustness of the
model–harness adjusted significance result to alternative scoring rules.

## Provenance and portability

`export_manifest.json` records every exported artifact, its original source
path and hash, and the hash of its published copy. Numeric CSV text is preserved.
Large CSVs are deterministically gzip-compressed. Only workstation path prefixes
were replaced; these are **locators**, not evidence that a file is distributed:

- `repo/`: original repository root, including ignored `runs/` and `analysis/`.
- `external/audit-data/`: separate private audit-data checkout.
- `external/manuscript/`: separate manuscript checkout.
- `external/home/`: other local tooling.

`provenance/` preserves selected-trial, prediction and reference identities.
Public folder names omit version and date suffixes; original source paths,
verifier versions, and hashes remain recorded for reproducibility.
Benchmark configs are now grouped under `benchmarks/task1/`,
`benchmarks/tasks2_3/` and `benchmarks/shared/`. The
[relocation inventory](../docs/release/benchmark_layout.json) maps old benchmark
paths to current files. Frozen input manifests keep their original identities;
an old path is not permission to replace a pinned hash or resume an old lock.
Hashes inside these historical manifests identify original bytes. They are
not recalculated to describe the path-redacted copies; published-copy hashes
are in `export_manifest.json`. The paper can therefore trace an input without
distributing its contents or the workstation account name.

`methods/` contains preserved historical Python/R sources. Some require the
original `analysis/paper_results/` layout, local input manifests, prior-stage
results, manuscript checkout, and R packages. Path redaction makes those files
**provenance copies, not standalone entry points**. Use `paper/reproduce.py`
for the portable checks. Do not run a historical `freeze.py`, plotter, or
finalizer against the locked output directories.

## Additional inputs needed for full reproduction

Raw predictions, raw trajectories, approved ground truth, audit adjudications,
source PDFs, and every historical experiment directory are not bundled here.
This prevents unreviewed redistribution of third-party text in source documents
and logs. Numerical tables can be checked without them; rescoring predictions
cannot. Publish a separately cleared, hash-matched prediction/reference release
and immutable dataset revisions before claiming self-contained raw-input
reproduction. Final uploaded dataset identities have not been assumed.

Original figures and raw results remain untouched locally. The retained
plotting programs document how they were generated, but a fresh checkout does
not yet provide a one-command rebuild of every main/supplementary figure.
R/package and private-input prerequisites are recorded in historical manifests
where available; omissions are release gaps, not inferred defaults.

There are no new experiments here: no matched repeated C30 runs, second
adjudicator, corrected-versus-uncorrected comparison, granularity-invariant
graph matcher, or optimized retrieval baseline.
