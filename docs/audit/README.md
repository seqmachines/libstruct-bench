# Ground-Truth Audit Pipeline

This subsystem produces a human-approved, evidence-grounded release for T1
final libraries, T2 oligos, and T3 molecular workflows. Claude Code proposes
evidence and issues; it never approves or applies ground-truth changes.

## Repository and dataset boundaries

Keep the implementation distributed by responsibility:

- `libstruct-bench`: schemas, policies, prompts, Python, CLIs, and tests;
- the all-protocol Hugging Face dataset: primary documents, legacy
  `scg_lib_structs` HTML/assets, and immutable source snapshots;
- the private audit-data repository: reviewed catalogs, manifests, renditions,
  packets, transcripts, proposals, decisions, applications, checkpoints, and
  release specifications;
- the audited ground-truth Hugging Face dataset: only human-approved T1/T2/T3
  artifacts and the frozen release manifest.

Use `groundtruth_final_lib_struct.json`, `groundtruth_oligos.json`, and
`groundtruth_library_generation_workflow.json` as the per-protocol T1, T2,
and T3 artifact names in the audited dataset.

Never copy private protocol files, proposed labels, or human decisions into
this code repository. Every Hugging Face reference must use an immutable
40–64-character commit hash, never `main`.

`groundtruth_oligos.tsv` is a baseline input. Preserve its row lineage, audit
it like every other current record, and do not treat it as authoritative.
Comparison packets contain a deterministic protocol-only projection of this
global TSV, with `source_row_number` pointing back to the immutable full-file
hash; rows from other protocols are not exposed to that audit.

## Phase gates

1. Discover every actual and expected source in a source catalog. Discovery
   creates `pending` records; it does not approve anything.
2. A human marks each record `included`, `excluded`, or `unavailable`, with a
   reason. Manifest creation blocks while any source remains pending.
3. Build deterministic renditions of every included PDF, Word document, and
   spreadsheet. PDF page images preserve figures without OCR.
4. Materialize an evidence packet containing primary sources and renditions
   only. Legacy HTML and current benchmark labels are physically absent.
5. Run Claude Code read-only to produce `protocol_evidence.v1` for T1/T2/T3.
   Every primary source must be accounted for; an unreadable source blocks the
   protocol.
6. Freeze the evidence JSON. Build a comparison packet that adds legacy HTML,
   current records, and optional benchmark-run artifacts.
7. Run Claude Code read-only to produce `protocol_audit.v2`. The output is an
   issue proposal, not a decision.
8. Render a human packet. A human accepts, rejects, modifies, leaves
   unresolved, or excludes every issue.
9. Apply only accepted or modified ground-truth patches with deterministic
   Python, pinned proposal/decision/baseline hashes, and regression fixtures.
10. Record checkpoints at 0, 5, 10, every later multiple of 5, and the final
    protocol count. Independently audit every high-impact protocol plus a
    seeded 10% sample with Codex.
11. Freeze only when all expected protocols are reviewed, required independent
    audits are complete, accepted changes have application logs, no included
    task has an unresolved blocker/high issue, and regression results are
    clean.

## Installation

From the repository root:

```bash
python -m pip install -e '.[audit-docs,test]'
```

The source and ground-truth dataset checkouts must correspond exactly to the
commit hashes placed in the catalog. Store private outputs outside this
repository.

## 1. Build and review the source catalog

```bash
libstruct-build-audit-source-catalog \
  --protocols-dir "$SOURCE_CHECKOUT/protocols" \
  --html-dir "$SOURCE_CHECKOUT/scg_html" \
  --html-asset-root "$SOURCE_CHECKOUT" \
  --source-manifest-tsv "$SOURCE_CHECKOUT/protocols/SOURCE_MANIFEST.tsv" \
  --html-map "$AUDIT_DATA/config/legacy-html-map.json" \
  --oligo-tsv "$GROUNDTRUTH_CHECKOUT/groundtruth/groundtruth_oligos.tsv" \
  --source-protocols-prefix protocols \
  --groundtruth-protocols-prefix groundtruth \
  --html-prefix scg_html \
  --oligo-tsv-dataset-path groundtruth/groundtruth_oligos.tsv \
  --source-repository "$SOURCE_HF_REPO" \
  --source-revision "$SOURCE_HF_COMMIT" \
  --groundtruth-repository "$GROUNDTRUTH_HF_REPO" \
  --groundtruth-revision "$GROUNDTRUTH_HF_COMMIT" \
  --out "$AUDIT_DATA/catalogs/source-catalog.initial.json"
```

The prefix options describe paths inside the immutable Hugging Face
checkouts, independently of the local discovery paths. The defaults match a
combined checkout with `protocols/`, `groundtruth/`, and `scg_html/`. Use an
empty protocol prefix if a dataset stores protocol directories at its root.

Exit status 1 is expected while review is pending. Edit a copy in the private
audit-data repository: add a human `review` and set every `approval_status`.
Regenerate with `--previous-catalog` after source updates; unchanged hashes
retain decisions, while changed files return to `pending`.

The reviewed HTML exception for v4 may map to `10xChromium3.html`:

```json
{
  "schema_version": "libstruct.legacy_html_map.v1",
  "protocols": {
    "10x_chromium_3_gene_expression_v4": ["10xChromium3.html"]
  }
}
```

## 2. Build manifests and renditions

```bash
libstruct-build-audit-manifests \
  --catalog "$AUDIT_DATA/catalogs/source-catalog.reviewed.json" \
  --checkpoint-id pilot-0 \
  --reviewed-protocol-count 0 \
  --out "$AUDIT_DATA/pilot/manifests"

libstruct-build-audit-renditions \
  --manifest "$AUDIT_DATA/pilot/manifests/manifests/$PROTOCOL_ID.json" \
  --source-dataset-dir "$SOURCE_CHECKOUT" \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/renditions"
```

Rendition failures are blockers. Do not replace unreadable tables or diagrams
with remembered kit knowledge or web searches.

## 3. Run evidence first, then comparison

```bash
libstruct-prepare-audit-phase-packet \
  --phase evidence \
  --manifest "$AUDIT_DATA/pilot/manifests/manifests/$PROTOCOL_ID.json" \
  --source-dataset-dir "$SOURCE_CHECKOUT" \
  --groundtruth-dataset-dir "$GROUNDTRUTH_CHECKOUT" \
  --rendition-bundle-dir "$AUDIT_DATA/pilot/$PROTOCOL_ID/renditions" \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/evidence-packet"

libstruct-run-claude-audit \
  --phase evidence \
  --packet "$AUDIT_DATA/pilot/$PROTOCOL_ID/evidence-packet" \
  --model "$FULL_VERSIONED_CLAUDE_MODEL_ID" \
  --run-id evidence-001 \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/evidence-run-001"

libstruct-prepare-audit-phase-packet \
  --phase comparison \
  --manifest "$AUDIT_DATA/pilot/manifests/manifests/$PROTOCOL_ID.json" \
  --source-dataset-dir "$SOURCE_CHECKOUT" \
  --groundtruth-dataset-dir "$GROUNDTRUTH_CHECKOUT" \
  --rendition-bundle-dir "$AUDIT_DATA/pilot/$PROTOCOL_ID/renditions" \
  --evidence-artifact "$AUDIT_DATA/pilot/$PROTOCOL_ID/evidence-run-001/evidence.json" \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/comparison-packet"

libstruct-run-claude-audit \
  --phase comparison \
  --packet "$AUDIT_DATA/pilot/$PROTOCOL_ID/comparison-packet" \
  --model "$FULL_VERSIONED_CLAUDE_MODEL_ID" \
  --run-id comparison-001 \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/comparison-run-001"
```

The harness allows only `Read`, `Glob`, and `Grep`, uses plan permission mode,
captures the full structured transcript, and records the exact model, Claude
version, harness, prompt, skill, policies, schema, tools, checkpoint, budget,
and hashes.

## 4. Human review and deterministic application

```bash
libstruct-render-audit-review \
  --proposal "$AUDIT_DATA/pilot/$PROTOCOL_ID/comparison-run-001/audit.json" \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/review-001"

libstruct-validate-audit-review \
  --proposal "$AUDIT_DATA/pilot/$PROTOCOL_ID/comparison-run-001/audit.json" \
  --decision "$AUDIT_DATA/pilot/$PROTOCOL_ID/review-001/decision.json"

libstruct-apply-audit-decision \
  --proposal "$AUDIT_DATA/pilot/$PROTOCOL_ID/comparison-run-001/audit.json" \
  --decision "$AUDIT_DATA/pilot/$PROTOCOL_ID/review-001/decision.json" \
  --baseline "CURRENT_T1=$GROUNDTRUTH_CHECKOUT/groundtruth/$PROTOCOL_ID/groundtruth_final_lib_struct.json" \
  --baseline "CURRENT_T2=$GROUNDTRUTH_CHECKOUT/groundtruth/$PROTOCOL_ID/groundtruth_oligos.json" \
  --artifact-schema "CURRENT_T1=schemas/groundtruth/final_library_groundtruth.v1.schema.json" \
  --artifact-schema "CURRENT_T2=schemas/groundtruth/oligo_groundtruth.v1.schema.json" \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/application-001"
```

Use the exact source IDs from the proposal for `--baseline`. A modification
must contain the human replacement value and replacement patch. Non-ground-
truth issues remain preserved but are never applied as label changes.

If the accepted proposal creates the first T3 artifact, it targets `new-t3`
as a `new_groundtruth_record`. Do not supply a baseline for it; add
`--artifact-schema new-t3=schemas/groundtruth/library_generation_workflow.v1.schema.json`.
The application log records an absent baseline and creates the file only from
the accepted or human-modified root patch.

Run every generated accepted-correction fixture before a checkpoint:

```bash
libstruct-run-audit-regressions \
  --fixture "$AUDIT_DATA/pilot/$PROTOCOL_ID/application-001/regressions/issue-001.json" \
  --baseline "CURRENT_T1=$GROUNDTRUTH_CHECKOUT/groundtruth/$PROTOCOL_ID/groundtruth_final_lib_struct.json" \
  --out "$AUDIT_DATA/pilot/$PROTOCOL_ID/regression-results.json"
```

For a regression fixture whose `baseline_state` is `absent`, omit
`--baseline`; the runner deterministically starts from an empty document.
Checkpoint reporting validates the regression-results schema and requires its
issue set to exactly match the corrections in the supplied application logs.

## 5. Build the audited oligo outputs

After the reviewed T2 candidates are final, build both the canonical JSON
catalog and the updated TSV:

```bash
libstruct-build-audited-oligos \
  --t2 /path/to/protocol-a/groundtruth_oligos.json \
  --t2 /path/to/protocol-b/groundtruth_oligos.json \
  --decision protocol-a=protocol-a:decision:001 \
  --decision protocol-b=protocol-b:decision:001 \
  --created-at 2026-08-01T12:00:00Z \
  --out "$AUDIT_DATA/releases/v1.0.0/audited-oligos"
```

The output contains `oligo_catalog.json`, `groundtruth_oligos.tsv`, and a
hash-pinned build record. Source-visible names and original TSV row lineage
are retained. Reused `canonical_oligo_id` values must agree on canonical name,
sequence, role, orientation, and family; a conflict blocks the build for human
resolution. Assay-specific oligos without a shared canonical ID retain their
protocol-specific name and receive a protocol-scoped catalog ID.

## 6. Checkpoints and release

```bash
libstruct-report-audit-checkpoint \
  --checkpoint-id checkpoint-5 \
  --reviewed-protocol-count 5 \
  --proposal /path/to/proposal.json \
  --decision /path/to/decision.json \
  --application-log /path/to/application-log.json \
  --regression-results /path/to/regression-results.json \
  --previous-checkpoint "$AUDIT_DATA/checkpoints/checkpoint-0.json" \
  --out "$AUDIT_DATA/checkpoints/checkpoint-5.json"

libstruct-build-audit-release \
  --spec "$AUDIT_DATA/releases/v1.0.0/release-spec.json" \
  --artifact-root "$AUDIT_DATA" \
  --out "$AUDIT_DATA/releases/v1.0.0/release-manifest.json"
```

The release specification is human-maintained; the release manifest is
generated. The generator verifies every referenced schema, policy, manifest,
evidence artifact, audit, decision, application, checkpoint, and T1/T2/T3
artifact before writing the manifest. Each release artifact declares its
`artifact_source_id`; its hash must equal the latest deterministic application
candidate, or the unchanged pinned baseline if no application changed it.
The specification also points `oligo_outputs` at the catalog, TSV, and build
metadata from step 5. The release builder reproduces both files from the
released T2 artifacts and human decision IDs before freezing them.

## Pilot and benchmark cutover

Start with five protocols spanning multi-library output, spreadsheets,
orientation risk, legacy-HTML mapping, and protocol-version ambiguity. The
current pilot set is `s3_atac`, `10x_chromium_3_feature_barcoding`,
`sci_rna_seq`, `petri_seq`, and `dr_seq`. Review the checkpoint-5 metrics and
revise only versioned, protocol-independent policy before scaling.

Do not repoint live Harbor configs until a frozen release exists in the
audited ground-truth Hugging Face dataset. The staged graders are:

- `libstruct-grade-library` for
  `libstruct.final_library_groundtruth.v1`;
- `libstruct-grade-oligos` for `libstruct.oligo_groundtruth.v1`.

They reject legacy ground-truth shapes and mutable Hugging Face revisions.
T1 uses identifier-aware global assignment; T2 uses global Hungarian sequence
assignment. Benchmark standardization remains in
`benchmark-standardization-policy.md`, separate from protocol evidence and
agent memory.
