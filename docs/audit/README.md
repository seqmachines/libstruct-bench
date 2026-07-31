# Ground-Truth Audit

This subsystem builds a trusted, evidence-grounded, versioned ground-truth
release for sequencing-library structure benchmarks. It separates scientific
review from benchmark engineering and keeps human reviewers as the final
authority.

## Repository boundary

`libstruct-bench` tracks:

- audit policy and documentation;
- JSON Schemas for input manifests, proposals, decisions, and releases;
- Claude and Codex workflows;
- deterministic pipeline code and tests.

The separate private audit-data repository tracks:

- immutable legacy HTML snapshots;
- pinned references to primary protocol documents;
- baseline ground-truth JSON;
- agent proposals and run provenance;
- human review decisions;
- frozen releases.

Large protocol documents may remain in a private dataset. Their audit manifest
must pin the repository, revision, path, and SHA-256 rather than relying on a
mutable local path.

## Audit packet

Every protocol is presented as a manifest with three source roles:

| Role | Contents | Evidentiary use |
| --- | --- | --- |
| `primary_evidence` | PDF, XLS/XLSX, DOC/DOCX, and other protocol files | Primary scientific evidence |
| `legacy_curated_html` | Original `scg_lib_structs` HTML page or family pages | Provenance for the human-curated legacy interpretation |
| `current_benchmark_record` | Current `groundtruth_final_lib_struct.json` and `groundtruth_oligos.json` | Candidate benchmark record being audited |

The current corpus has 73 protocol directories and 69 legacy HTML pages.
Protocol-to-HTML relationships must therefore be explicit in the manifest;
filename matching is not authoritative.

This separation supports error attribution:

- primary evidence versus HTML: legacy curation, external-knowledge, source
  conflict, or protocol-version issue;
- HTML versus current JSON: extraction or normalization issue;
- correct JSON versus an incorrect score: evaluator or identifier-matching
  issue.

## Workflow

1. Build and validate an immutable input manifest.
2. Run Claude Code read-only against one protocol packet and require a
   `libstruct.protocol_audit.v1` result.
3. Validate and store the proposal without modifying the baseline.
4. Have a human reviewer record issue-level decisions.
5. Apply accepted patches with deterministic Python after checking proposal
   and baseline hashes.
6. Generate regression tests and a candidate release.
7. Use Codex for an independent audit of high-impact cases and a reproducible
   random subset.
8. Freeze a release only after the release gates in
   `adjudication-policy.md` pass.

## Build the input inventory

Install this repository in editable mode, then run:

```bash
libstruct-build-audit-inventory \
  --protocols-dir /path/to/protocols \
  --html-dir /path/to/scg_html \
  --out /path/to/private-audit-data/inventory
```

The command writes `inventory.json` and one validated manifest per ready
protocol under `manifests/`. It does not copy protocol, HTML, or ground-truth
files. A nonzero exit reports unresolved input mappings; inspect
`inventory.json` rather than guessing a mapping.

Reviewed protocol-to-HTML exceptions are supplied with `--html-map`:

```json
{
  "schema_version": "libstruct.legacy_html_map.v1",
  "protocols": {
    "10x_chromium_3_gene_expression_v4": ["10xChromium3.html"]
  }
}
```

## Prepare one audit packet

```bash
libstruct-prepare-audit-packet \
  --manifest /path/to/inventory/manifests/s3_atac.json \
  --protocols-dir /path/to/protocols \
  --html-dir /path/to/scg_html \
  --out /path/to/private-audit-data/packets/s3_atac
```

The command validates the manifest, rejects stale source hashes, and copies
only the listed files into role-separated directories. Packet files are made
read-only. Use `--mode symlink` only when an external filesystem sandbox
protects the original sources.

The initial pilot protocols are `s3_atac`,
`10x_chromium_3_feature_barcoding`, `sci_rna_seq`, `petri_seq`, and `dr_seq`.

## Tool configuration

Launch audit sessions from this repository and expose the private data checkout
with Claude Code's `--add-dir`. Use plan mode, an explicit tool set, deny edit
and web tools, and an OS-level read-only filesystem boundary. For scripted
runs, use print mode with `--json-schema`, bounded turns, and captured stdout.

`--allowedTools` pre-approves calls; it is not a tool restriction. Enforcement
must come from `--tools`, deny rules, and the filesystem sandbox.
