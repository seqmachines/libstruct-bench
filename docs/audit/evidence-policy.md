# Audit Evidence Policy

## Evidence scope

Only files listed in the protocol's validated input manifest may support an
audit conclusion. Online search, remembered commercial-kit sequence, and
unversioned local files are out of scope.

If missing external evidence is scientifically important, record an
`external_knowledge` issue and recommend adding a pinned source. Do not insert
remembered bases into a proposed benchmark record.

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

## Required comparisons

For every final library and relevant oligo:

1. Compare the current JSON with the legacy HTML to detect extraction,
   omission, strand, and normalization changes.
2. Compare both with primary evidence to determine whether every scored
   constant and variable-region length is recoverable.
3. Check all primary documents for version and internal conflicts.
4. Check that every distinct final sequencing product is represented.
5. Check read orientation and reverse-complement operations.
6. Check evaluator identifiers and assignments separately from biological
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

- `primary_explicit`: printed directly in primary evidence.
- `primary_derived`: reproducibly assembled from cited primary components.
- `legacy_only`: present in legacy HTML but absent from primary evidence.
- `conflicting`: sources support incompatible values or structures.
- `absent`: no manifest-listed source supports the value.
- `ambiguous`: evidence is insufficient to select one interpretation.

Agent confidence describes the audit assistant's uncertainty; it does not
change evidentiary status or authorize a correction.
