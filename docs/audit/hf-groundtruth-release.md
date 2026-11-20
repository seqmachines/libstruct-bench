# Ground-truth-only Hugging Face staging

Prepare the selected 20-protocol panel and all 73 canonical protocols in the
same `<staging-name>/groundtruth/<protocol_id>/groundtruth_*.json` layout:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.prepare_groundtruth_uploads \
  --private-root /path/to/private/protocols-test \
  --output /path/to/new/private/staging
```

The output directory must be new. No inference, rescoring, scientific edits,
network calls or uploads are performed. Canonical JSON bytes are preserved.
Only the three ground-truth filenames per selected protocol and explicitly
generated metadata/utilities are copied. Private audit files and source
documents are never copied into the public package. Shared records must have
the same hashes in the 20-protocol and full packages.

Dataset IDs:

- `sequencing/scg-protocols-v1-groundtruth`
- `sequencing/scg-protocols-all-groundtruth`

Each `groundtruth/` is a complete dataset repository root, with a dataset card,
attribution and proposed CC BY 4.0 release notice, source metadata, schemas,
approval-hash provenance and an offline integrity checker. Publication is a
separate action: review license scope and remaining gaps first.

```bash
python /path/to/staging/scg-v1-upload/groundtruth/verify_release.py
python /path/to/staging/scg-v1-upload/groundtruth/verify_release.py \
  --source-root /path/to/private/protocols-test/protocols_scg_all
```

Source URLs and revision labels are copied only when recorded, with version
tokens also transcribed from saved native-text headers. Candidate DOI links
detected in source text remain explicitly unverified, separate from recorded
source URLs. Protocol-level related links are not file identities. Missing
metadata is recorded as missing. Three unavailable full-set catalog entries
remain provenance records with no hash; they are not newly supplied evidence.
The auxiliary text hash normalizes Unicode NFKC and whitespace only and does
not establish figure/table equivalence or canonical extraction across engines.

Approval correspondence is checked against final promotion decisions, the
final connected-process scientific preview, and subsequent human-amendment
receipts. Known canonical modality aliases are compared explicitly. Any other
scientific difference fails closed. A difference restricted to modality but
outside the alias map is staged with `unresolved_modality_refinement` and needs
review; the packager does not approve it. This packaging check does not replace
the complete release gate in `audit/release.py` or a scientific audit.

The full set currently retains 20 legacy T3 workflow records. Their unchanged
JSON is validated using the same in-memory connected-process compatibility
conversion used in the frozen analysis. Current schemas apply to the converted
view. Consumers must use that conversion before current linked validation.

To install prepared packages locally after validation:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.install_groundtruth_uploads \
  --staging /path/to/new/private/staging \
  --private-root /path/to/private/protocols-test
```

The installer rechecks source and canonical hashes, copies and verifies each
new package, and moves the old `groundtruth/` staging directory into
`upload-preparation-backups/<UTC timestamp>/`. Existing `protocols/` folders
remain untouched. It writes no canonical labels and makes no remote changes.
Upload **only the prepared `groundtruth/` subfolder**, never its parent (which
can contain old PDFs and private preparation receipts).

Validation coverage: `tests/audit/test_hf_groundtruth.py` checks byte retention,
private-file exclusion, stale hashes, modality versus scientific differences,
unexpected-file detection, path traversal, and overwrite refusal.
