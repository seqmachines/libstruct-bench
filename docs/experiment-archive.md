# Native experiment archive

The archive preserves vendor-native Task 1 and Tasks 2–3 attempts on protocols identified by the scg_lib_structs source inventory. This includes Codex, Claude Code, Gemini CLI, Antigravity, Kimi CLI, their benchmark adapters, failed attempts, repeats, name-only controls and native learning runs. Source files, run directories and paper tables are read-only inputs.

## Inspect locally

Run these commands from the project root. The first command installs the console entry point and the optional cloud clients. Shell commands below assume an active virtual environment.

```sh
uv sync --locked --inexact --extra archive
source .venv/bin/activate
review_dir=$(mktemp -d)
trial=libgen/codex/libgen-gpt-5-6-sol/10x_chromium_3_feature_barcoding__bN2Lxic
libstruct-archive inventory --output "$review_dir/inventory.json"
libstruct-archive verifier-report --version 4.6.0 --output "$review_dir/verifier-4.6.csv"
libstruct-archive ingest "$trial" --dry-run --manifest-out "$review_dir/pilot-manifest.json"
```

The inventory uses original Harbor configs and results. It skips the linked run browser and inventories direct API, Pi and mini-SWE trials as local-only. The sibling `important_runs` contains two five-protocol GPT-5.5/Codex parser controls, plus `parser_analysis` tables, code and figures. These are useful provenance for the parser experiment. Their local-only status follows their experimental condition even though Codex executed them. The private source resolver checks `protocols-test/protocols_scg_all/`, the earlier `protocols/` layout, and the task’s embedded source directory by recorded SHA-256. Set `LSB_IMPORTANT_RUNS` to override the parser-control directory. See [.env.example](../.env.example) for all path overrides.

The verifier report identifies each trial directory, its protocol, model, execution ID, execution-time version, whether the requested version was used at execution or during rescoring, and the newest attributable version. The archive compares numeric versions and prefers the frozen final-paper result at the same version. Task 1 uses separate reference-corrected scores. Superseded verifier directories stay local. Original job receipts and trial results can contain the score Harbor recorded at execution. Those files remain execution evidence. The searchable metrics record the selected evaluation version and origin. The historical scorer command continues to use 4.6.0.

## Exercise the full workflow locally

```sh
export LSB_ARCHIVE_MODE=local
export LSB_ARCHIVE_STATE="$review_dir/state"
libstruct-archive ingest "$trial" --apply > "$review_dir/pilot.json"
run_id=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$review_dir/pilot.json")
libstruct-archive query --run "$run_id"
libstruct-archive verify --run "$run_id"
libstruct-archive fetch --run "$run_id" --out "$review_dir/restore"
```

The local backend uses ordinary files and SQLite. Restore writes each snapshot under its trial ID and manifest hash. The `trial/` and `job/` subdirectories preserve original relative paths. Source documents, execution inputs, learning assets and verifier-only records have their own paths. Restore reuses an existing destination file only after its SHA-256 matches. Paths under the original data roots are refused.

## Configure private cloud storage

1. In the intended Cloudflare account, enable R2 and choose distinct private buckets for primary evidence and restricted experiment artifacts. Leave public development URLs and custom domains disabled. To create new buckets after choosing their names, use the following commands. [Cloudflare bucket setup](https://developers.cloudflare.com/r2/buckets/create-buckets/)

   ```sh
   npx wrangler login
   npx wrangler r2 bucket create "$R2_EVIDENCE_BUCKET"
   npx wrangler r2 bucket create "$R2_ARCHIVE_BUCKET"
   ```

2. Create an R2 Object Read & Write S3 token scoped to those two buckets. Keep it on the archival host. Any future evidence consumer should receive only its authorized evidence, with separate read credentials. Each source entry carries its recorded rights and provenance. Keep third-party documents private. [Cloudflare R2 authentication](https://developers.cloudflare.com/r2/api/tokens/)

3. Use the existing Proofread Atlas cluster. Add the archival host to the project IP access list. Give the archive database user read, insert, update, collection-creation and index-creation access to the five `lsb_*` collections listed below. Proofread's application user and its existing collections require no migration. [Atlas database users](https://www.mongodb.com/docs/atlas/security-add-mongodb-users/)

4. Put the following variables in this checkout's Git-ignored `.env.archive.local`. Use shell assignments with single-quoted literal values, keep the file readable only by its owner, and enter credentials locally. The repository launcher loads this file in its own process. Preview the destination with the following command.

   ```sh
   ./tools/libstruct-archive target
   ```

   Required values are `R2_ENDPOINT_URL`, `R2_EVIDENCE_BUCKET`, `R2_ARCHIVE_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `MONGODB_URI`. `LSB_MONGO_DATABASE` defaults to `proofread`. The target command prints only the endpoint, bucket names, database host and database name. To enable future cloud archives, save `LSB_ARCHIVE_MODE='r2'` and set `LSB_CONFIRMED_TARGET` to the **reviewed literal output** in the private profile. A one-time `--confirm-target` also works for an individual ingest. An explicit `LSB_ARCHIVE_MODE=local` or `off` in the invoking environment takes precedence. Local mode stores data on disk, and off disables automatic completion hooks.

## First live trial and bulk migration

After explicit confirmation for the pilot, run the same one-trial ingest and retrieval against R2 and Atlas. If you ran the local exercise in the same shell, clear its overrides first.

```sh
unset LSB_ARCHIVE_MODE LSB_ARCHIVE_STATE
./tools/libstruct-archive ingest "$trial" --apply > "$review_dir/cloud-pilot.json"
run_id=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$review_dir/cloud-pilot.json")
./tools/libstruct-archive query --run "$run_id"
./tools/libstruct-archive fetch --run "$run_id" --out "$review_dir/cloud-restore"
./tools/libstruct-archive verify --run "$run_id"
```

For bulk review, the following dry-run reads, screens and hashes the selected files without contacting R2 or Atlas. It can take substantially longer than inventorying the directories. Review any failures and unresolved source identities. For an individual case, use `ingest --dry-run --manifest-out` for the per-file breakdown.

```sh
libstruct-archive migrate "$review_dir/inventory.json" --dry-run
shasum -a 256 "$review_dir/inventory.json"
```

Only after separate bulk authorization, set `reviewed_digest` to the approved inventory SHA-256 and run the command below. Repeat the same command after interruption. Successful registrations are checkpointed in the private local journal for that destination and inventory. Generate a fresh inventory when trial files change. Add `--refresh` when previously missing external documents or checkpoints become available.

```sh
./tools/libstruct-archive migrate "$review_dir/inventory.json" --apply --authorize-bulk "$reviewed_digest"
```

## New runs and recovery

### Run a single protocol

Harbor opens a prepared task directory containing `task.toml`, instructions,
an environment and verifier files. The parent directory is the dataset path.
The following example selects just the 10x Feature Barcoding task from the
prepared Task 1 collection and reuses the Codex configuration. Run it from
the repository root.

```sh
task_parent=runs/library_structure/round2/tasks
protocol=10x_chromium_3_feature_barcoding
mkdir -p tmp
config=$(mktemp -d tmp/single-protocol.XXXXXXXX)/job.json
python - "$task_parent" "$protocol" "$config" <<'PY'
import json
import sys
from pathlib import Path

parent, protocol, output = sys.argv[1:]
assert (Path(parent) / protocol / "task.toml").is_file(), "Prepare the Harbor task first"
job = json.loads(Path("paper/benchmarks/task1/codex.json").read_text())
job["datasets"] = [{"path": str(Path(parent).resolve()), "task_names": [protocol]}]
Path(output).write_text(json.dumps(job, indent=2))
PY
./tools/libstruct-archive launch -c "$config" --job-name "task1-$protocol-$(date -u +%Y%m%dT%H%M%SZ)"
```

The historical Task 1 template fetches pinned Hugging Face documents. New Task 1
tasks generated with `--source-root` embed the local source documents. Tasks 2–3 support `--source-delivery embedded` to copy hash-checked
local sources into a task at preparation time, or `pinned-hf` to download
them during the image build. A directory of original PDFs is an input to
task preparation. R2 receives the evidence and run artifacts after execution.
Keep private generated task contexts on the host.

### Run a prepared benchmark job

For an individual native Harbor config, prepare the versioned task context and provider login. The planners import Harbor in Python and the launcher invokes the Harbor executable. Both should use the same benchmark installation. This host's existing Harbor 0.20.0 includes a customized Antigravity adapter. Its project virtual environment inherits the base Python's site packages and its `bin/harbor` points to that host executable. For a new environment on a similarly configured host, create the venv with the Harbor installation's Python and `--system-site-packages`. Use `uv sync --inexact` when adding archive dependencies to retain installed execution tools.

From the repo root, use this host command in a fresh shell. The example selects a new output directory for the existing Task 1 config. The name goes to both Harbor and the archive journal. A cloud or local archive launch refuses to reuse a populated job directory.

```sh
./tools/libstruct-archive launch -c paper/benchmarks/task1/codex.json --job-name "task1-codex-$(date -u +%Y%m%dT%H%M%SZ)"
./tools/libstruct-archive reconcile
```

For matrix, external-knowledge and learning plans, activate the project virtual environment and export the host profile in the planning and execution shells. This also enables the KB executor and resume CLI hooks. For example, from the repo root:

```sh
source .venv/bin/activate
set -a
source ./.env.archive.local
set +a
```

Newly generated plans then use `libstruct-archive launch`. Running plain `harbor run` bypasses the hook, including in old generated scripts. Use the host launcher with those scripts' prepared configs for new runs. The launcher strips archive environment variables from the agent process. The host journal defaults to `~/.local/state/libstruct-archive`. Keep it on persistent private storage. The archive updates the `lsb_*` collections in Atlas. Proofread owns publishing those records in its review interface.

The journal is written before execution. The completion hook handles successful and failed runs without changing Harbor's exit status. Reconciliation retries queued jobs after archive errors or a terminated launcher. Completed jobs with no trials can be archived as job records when the config identifies the native harness and scg protocols. A missing output directory remains pending. Rerun a one-trial ingest after an R2 or MongoDB outage. Multipart upload IDs and uploaded parts survive process restarts. Existing objects are read back and verified by SHA-256 before registration. Configure an R2 lifecycle rule for abandoned incomplete multipart uploads if desired. [R2 upload behavior](https://developers.cloudflare.com/r2/objects/upload-objects/)

## Data model and queries

| Entity | Identity and content |
| --- | --- |
| Experiment | Study or learning lineage. Panel membership and archive eligibility come from separate records. |
| Run | Experiment, Harbor job identity and recorded agent configuration. A root-relative job identity is used where Harbor did not record an ID. |
| Trial | Harbor execution UUID when available, otherwise a deterministic relative-path identity. Copies with the same UUID share a trial identity. Different attempts retain different IDs. |
| Snapshot | SHA-256 of the canonical versioned trial manifest. Changed files or different source locations retain their own immutable receipts. The default query and restore view selects the newest verifier version per trial and run. Use `--revision` for an exact recorded snapshot. |
| Source bundle | Immutable descriptor of the protocol, task pins, expected documents, hashes, source revisions and link status. Name-only, candidate and unresolved identities are explicit. Missing model versions, seeds and timestamps are null. |

R2 groups frozen manifests under `libstructbench/experiments/<experiment>/<run>/` and `libstructbench/protocols/<protocol>/<bundle>/`. Blobs have content-addressed keys within separate evidence and restricted storage. A manifest maps each blob to its original logical path. Quarantined or withheld entries carry the reason. Sanitized logs and trajectories carry both original and archived hashes. Opaque embedded payloads and sensitive log strings can be removed from the archived copy. Pattern-based screening cannot recognize every possible secret; keep archive credentials out of execution artifacts at the source.

The MongoDB collections are `lsb_experiments`, `lsb_runs`, `lsb_trials`, `lsb_source_bundles` and `lsb_snapshots`. The latter contains the verified object reference, run/trial/protocol IDs, model and harness, execution status, selected scores, usage, costs, timing and archival status. Source-bundle records include source metadata and private object references. Unique `_id` values make registration repeatable. Secondary indexes cover run/protocol, model/harness, and status. Original files and large trajectories are stored in object storage.

```sh
libstruct-archive query --protocol drop_seq --executor codex
libstruct-archive query --experiment tasks23-baseline --model gpt-5.6-sol
libstruct-archive list --prefix libstructbench/experiments/
libstruct-archive fetch --run "$run_id" --trial "$trial_id" --revision "$manifest_sha256" --out "$review_dir/exact"
```

## Validation and cleanup

```sh
uv sync --locked --inexact --extra archive --extra archive-test --extra test --extra audit-docs
uv pip install --python .venv/bin/python -r paper/requirements.txt
uv run --no-sync pytest -q
.venv/bin/python paper/reproduce.py --statistics
```

With the private source collection present, this test repeats the saved Codex pilot against mock S3 and MongoDB, restores its five source PDFs, checks the local trial bytes, and verifies idempotence.

```sh
LSB_TEST_REAL_PILOT=1 uv run --no-sync pytest -s -q tests/test_archive.py -k real_native_pilot
```

The statistics check also needs the paper's NumPy dependency from `paper/requirements.txt`. Mock S3 and MongoDB tests exercise the production adapters, including multipart resumption. The filesystem/SQLite tests check failed trials, immutable recovery and dry-run behavior.

The retired cumulative-manifest validator had no callers. The older ignored rules-sync helper targeted archived task roots. Their original bytes and SHA-256 restore map are in `.release-archive/archive-cleanup-20261119/`. For historical code verification, restore copies into a separate historical checkout. Earlier cleanup work and frozen scientific inputs retain their original locations.

