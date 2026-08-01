# Audit Evidence Policy

## Evidence scope

Only files listed in the protocol's validated input manifest may support an
audit conclusion. Online search, remembered commercial-kit sequence, and
unversioned local files are out of scope.

If missing external evidence is scientifically important, record a
`source_or_evidence_missing` issue and recommend adding a pinned source. Do
not insert remembered bases into a proposed benchmark record.

## Source roles

### Primary evidence

Primary protocol documents establish what an agent could recover from the
versioned source bundle. Record document identity and version as well as the
page, sheet, table, cell range, section, figure, or other stable locator.

### Legacy curated HTML

The HTML is valuable human-curated provenance and may encode cross-protocol or
kit knowledge. It is not unquestioned truth and is never relabeled as primary
evidence.

### Current benchmark record

The current JSON is the artifact under review. It can demonstrate what the
benchmark currently scores but cannot independently support its own scientific
correctness.

The global oligo TSV is exposed only as a deterministic protocol-row
projection. Its original row numbers, full-file hash, old names, and old
sequences are lineage, not scientific support, and must survive accepted T2
conversions.

## Evidence-first phase gate

The primary-source extraction must be completed and hash-frozen before legacy
HTML, current benchmark records, predictions, traces, or evaluator output are
made visible to the audit agent. A prompt instruction alone is not an adequate
phase boundary.

Every included primary source must appear exactly once in the source-coverage
ledger. An unreadable source blocks comparison until it is repaired or a human
changes its source disposition.

## Required comparisons

For every final library, relevant oligo, and molecular workflow step:

1. Start from the frozen primary-evidence artifact.
2. Compare the current JSON with the legacy HTML to detect extraction,
   omission, strand, and normalization changes.
3. Compare both with primary evidence to determine whether every scored
   constant and variable-region length is recoverable.
4. Check all primary documents for version and internal conflicts.
5. Check that every distinct final sequencing product is represented.
6. Check read orientation and reverse-complement operations.
7. Check evaluator identifiers and assignments separately from biological
   structure.

## Conflicts and transformations

Do not silently choose between a schematic and an explicit final PCR product,
between documents, or between protocol versions. Cite each side and emit a
human-review finding.

Record transformations that connect evidence to a proposed value, including
reverse complementation, wrapper removal, homopolymer expansion, placeholder
normalization, biological-insert omission, and construct assembly. Preserve
the before and after values plus a rationale.

Evidence excerpts should be the shortest text or sequence span needed to
support the finding. Never substitute an uncited summary for a stable source
locator.

## Support classifications

- `explicit`: printed directly in an approved source.
- `derivable`: reproducibly assembled from cited approved components.
- `externally_completed`: supported by an approved manifest-listed vendor or
  cited-method source rather than the assay's core documents.
- `ambiguous`: evidence supports multiple interpretations or cannot select one.
- `unsupported`: no manifest-listed source supports the value.

Agent confidence describes the audit assistant's uncertainty; it does not
change evidentiary status or authorize a correction.
