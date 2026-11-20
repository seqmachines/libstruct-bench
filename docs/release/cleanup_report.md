# Public release scope

The public baseline starts with one clean Git tree. It includes the benchmark
code, the paper's saved-score analyses, tests, and the R2/Atlas archive tools.
Original protocol documents, private verifier data, local credentials and raw
agent runs stay outside the release. The source inventory records document
identities, provenance and rights information.

The October review below describes the earlier development checkout. Its
backup and history references apply to that checkout. This repository ships
without a repository-level code license.

## Development cleanup review — 27 October 2026

**Status: prepared locally for author review; nothing committed or pushed.**
The existing Git history is not ready to be made public as-is. No manuscript,
figure, prediction, ground-truth record, scoring rule, or historical numerical
result was edited by this cleanup.

**Follow-up organization:** public paper directory names now omit version/date
suffixes. Benchmarks are grouped under `task1/`, `tasks2_3/`, and `shared/`; see
the follow-up section below. The earlier byte-preservation receipts describe
the initial cleanup stage, before these explicitly requested path updates.

## Backup and reversibility

- The temporary branch `backup/pre-public-release-20261027` was created
  before cleanup, then deleted at the
  author's request. `main` and the local file archives remain intact.
- A branch cannot capture uncommitted work. The local, ignored
  `.release-archive/20261027/working-tree-before.tar.gz` therefore also preserves
  all 8,410 tracked and nonignored untracked files present before cleanup.
  `files-before.json` records their hashes; staged/unstaged patches and the
  initial status are retained beside it. The working tree was already dirty;
  these snapshots distinguish earlier work from this cleanup.
- Existing ignored `runs/` and `analysis/` directories were not copied into that
  tarball and were not modified. Local `.env` credentials were neither printed
  nor included in it. This is not a disaster-recovery backup of all private data.
- Archived task files remain under `.release-archive/20261027/archived/`, with
  their original repository-relative layout. `archive-roots.json` lists moved
  roots and `archived-files.json` hashes every file. Restore a root to its
  original location only when that destination is absent; verify its hashes
  first. Never publish the archive.

## Changes prepared

| Area | Action and reason |
| --- | --- |
| Generated tasks | Archived 13,979 files (247.3 MB of file contents), including generated dependency copies, private verifier answers, source documents and historical name-only locks. No bytes were changed or permanently deleted. |
| Source code, schemas and learning runtime | Retained all 321 existing files under `src/`, `schemas/` and `improvement/` byte-for-byte. Uncertain compatibility code was retained rather than declared unused without evidence. |
| Scientific results | Added `paper/`: 113 selected artifacts (26.1 MB), including final score tables, 640-configuration sensitivity outputs, repeat and name-only results, statistical tables and analysis sources. Original local analyses remain unchanged. |
| Public provenance | Added original/published hashes for all exported artifacts. Workstation path prefixes are replaced in public copies only. Large CSVs are gzip-compressed; numerical text is preserved. |
| Portable verification | Added `paper/reproduce.py` to check artifact hashes and reproduce saved-score means, repeat summaries, and final primary paired tests/CIs without inference or rescoring. |
| Documentation | Replaced the minimal root README with installation, dataset destinations, paper-result mapping and commands. The public paper URL is explicitly pending. Old Libgen operator notes moved to `docs/legacy/`; older designs are distinguished from final paper selections. |
| Local paths | Replaced personal absolute paths in docs and three name-only configs. The audit workflow now requires `args.audit_root`; the audit skill uses the explicitly configured `LIBSTRUCT_DATA_ROOT`. Approval rules are unchanged. |
| Source rights | Added a metadata-only source inventory; removed local paths and verbatim source excerpts from its public copy. No third-party document or raw trajectory is exported. |
| Ignore rules | Excluded release archives, generated tasks, private build contexts, historical name-only locks and local staging/caches. Existing ignored runs/analyses stay local. |
| Tests | Replaced the test's dependency on checked-in private task bundles with synthetic task generation and the same snapshot/isolation assertions. Updated the audit path assertion for a configurable data root. |

Archived roots:

```text
benchmarks/libgen/tasks/
benchmarks/libgen/capability-training-tasks/
benchmarks/libgen/capability-training-tasks-b5/
benchmarks/library_structure/tasks/
benchmarks/oligo_extraction/tasks/
benchmarks/oligo_extraction/tasks_text5/
benchmarks/oligo_extraction/tasks_text5_summary/
benchmarks/libgen/name_only/tasks/
benchmarks/libgen/name_only/revisions/
benchmarks/libgen/name_only/experiment.json
```

These are generated or historical local contexts, not evidence that their
experiments are scientifically obsolete. Their generators, configurations,
selected outputs and provenance are retained. A local command that previously
used one of these paths must restore or regenerate its private task context.

## Scientific consistency checks

- Final paper scorer: explicit `libstruct_bench.libgen.v4_7_0`, SHA-256
  `1618943605e33e488654c7b02af85b027d3009c82f1aa1572d0d782b7c1da11e`, matching
  the camera-ready input manifest.
- Reference set: `approved-20261010-b672aab997a2fdc0`. C30 retains its historical
  selection; no selector was rerun. No original figure was regenerated.
- Figure 3a/3b selected attempts remain unchanged. The 60 GPT/Codex repeats
  remain a separate descriptive analysis. Name-only primary analysis is
  original bundle versus single name-only (n=1 versus n=1), including the
  verified Claude SHARE-seq retry, with informative Task 2 populations.
- Final Bonferroni tables take precedence over explicitly historical Holm
  columns. Task 1 API comparisons remain separate from native Tasks 2–3.
- `python paper/reproduce.py --statistics` verified all 113 exported artifacts,
  reproduced 301 main aggregate means, all 60 repeat executions' summaries,
  15 name-only comparisons and four model–harness primary comparisons,
  including the saved paired tests and CIs.
- Full test suite: **703 passed, 122 subtests passed**; five PyMuPDF/SWIG
  deprecation warnings, no failures. The focused scoring/generation/audit tests
  also passed. No agent execution was launched.
- All archived files matched their recorded hashes. All 113 original analysis
  sources still matched their pre-export hashes. No protected core source,
  schema or learning-runtime file changed. Detailed local receipts are in
  `.release-archive/20261027/preservation-check.json`.

## Security and redistribution review

`python tools/release_audit.py --history --output <new-report.json>` scans
tracked/nonignored working-tree files, decompresses exported CSVs, and scans
all reachable Git blobs. It emits candidate locations, never matched secret
values. The post-cleanup scan found **zero credential-pattern matches, personal
path matches, private payload paths, or files above 10 MB** in the public
candidate. This targeted pattern scan is not a guarantee of exhaustive secret
detection.

The history scan found **27 source-document blobs** among 2,012 reachable blobs,
including historical PDFs and spreadsheets. No credential-pattern matches were
found. Removing files from the working tree does **not** remove those objects
from history, branches, tags, forks, prior downloads, or GitHub caches.

The source inventory has 248 source occurrences over 73 protocols: 35 marked
verified redistributable subject to their recorded conditions, 44 permission
required, and 169 unresolved. **112 entries lack a source-specific recorded
URL**; related protocol URLs and inferred DOI candidates must not be substituted
as if verified file links. The candidate redistributes none of these documents,
including the potentially clearable subset. Inventory evidence is the prior
audit, not a new legal clearance.

## Reproducibility gaps and author decisions

1. **Public Git history:** choose a separately reviewed clean public history/new
   public repository or an explicitly approved history rewrite. This cleanup
   did neither. Do not simply change this existing repository's visibility or
   push all branches. Deleting the temporary backup branch did not remove the
   source-document objects reachable through the remaining history.
2. **License:** no repository-level code license is currently present. Authors
   must select/confirm code licensing and third-party attribution; no license
   was invented. Upstream scg_lib_structs attribution does not license source
   PDFs or automatically license all repository code.
3. **Frozen input release:** raw predictions and complete approved references
   are not included in this candidate. Public numeric tables are reproducible
   at the saved-score level; full rescoring needs a cleared, hash-matched input
   release. Raw trajectories and source text require separate review. Do not
   claim a self-contained raw-input reproduction yet.
4. **Dataset publication:** confirm the two Hugging Face uploads and record their
   immutable revisions. The README uses the requested destinations, not an
   assumption that every upload is already public or matches the paper hashes.
5. **Source acquisition:** complete the 112 missing file-specific URLs and check
   document-version metadata. Continue link-only distribution for uncleared
   sources. Existing frozen bundles remain intact locally.
6. **Historical programs/figures:** preserved scripts still refer to the original
   local analysis layout and some private/preceding-stage inputs. They are
   provenance copies. The portable checker reproduces saved-score statistics;
   it does not rebuild all figures, rerun mixed models, or regenerate every
   historical pipeline stage. Those remaining prerequisites are stated in
   `paper/README.md`, not silently replaced with new defaults.
7. **Paper URL:** pending author update. The separate manuscript was inspected
   for final panel/reporting settings but not edited.

Scientific limitations remain explicit: no repeated C30 runs, second
adjudicator, corrected-versus-uncorrected analysis, granularity-invariant graph
matcher, or optimized retrieval baseline was added or implied.

Review the changes relative to the local pre-cleanup snapshot, not only to
HEAD: the starting tree contained substantial uncommitted scientific work.
Nothing has been staged, committed, pushed, or uploaded by this cleanup.

## Follow-up: task-oriented benchmark layout

At the author's request, the public benchmark configuration tree was reduced
from 31 to 25 files and organized as:

```text
benchmarks/
  README.md
  task1/       final PDF/API, native-agent and name-only configs; input provenance
  tasks2_3/    linked benchmark, native name-only configs, network assets, provenance
  shared/      common protocol-processing instructions
```

- Moved final Task 1 configs out of the historical `round2/` nesting. Preserved
  its earlier attachment inventory under `task1/provenance/` for the exclusion
  regression. New Task 1 CLI defaults point to the final paper manifest.
- Consolidated Tasks 2–3 under `tasks2_3/`. The broader planning matrix and
  ten-protocol training inventory are retained under `provenance/` and clearly
  identified as historical. Final panels remain defined by `paper/` manifests.
- Archived seven superseded files locally: the standalone oligo-extraction
  configs/README, obsolete Task 1 model list, and two superseded operator READMEs.
  The retained legacy generator now requires explicit manifest/output arguments.
- Moved the reference-migration report to `docs/evaluation/`, and the older
  standalone extraction guide to `docs/legacy/`. Added short task entry guides.
- Updated current source/test/doc/config path references and ignore rules.
  Twenty source files have path/help/default-argument edits; these are not
  scientific scoring changes. Old code/experiment locks retain their recorded
  hashes and must not be silently bypassed for a resumed historical run.
- Kept all scorer implementations, task-rule text, network-asset content,
  scientific protocol inventories, original run locations, predictions, ground
  truth, saved score tables and historical C30 selection unchanged.

[`benchmark_layout.json`](benchmark_layout.json) records every moved/archived
benchmark file and before/after hashes. The ignored
`.release-archive/20261027/benchmark-layout/` holds rollback copies, with no new
Git branch. Historical paper manifests remain byte-identical; their original
paths are provenance locators, not rewritten current-path assertions.

Validation after reorganization: **703 tests and 122 subtests passed**. All
113 exported paper artifacts and their saved-score checks passed again. Local
Markdown links, benchmark JSON and Python source syntax were checked. No agent
inference, figure regeneration, commit or push occurred.
