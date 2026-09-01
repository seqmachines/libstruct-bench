# Legacy-only linked ground-truth conversion

This phase freezes the human-curated starting point before any primary-source
comparison. It is a representation conversion, not a scientific audit.

Read every packet-listed `legacy_curated_html` and
`current_benchmark_record` file. No primary evidence, rendition, benchmark
prediction, prior proposal, or review record is available in this packet. Do
not use online search, remembered kit knowledge, or information from another
protocol.

Return one linked canonical candidate for each task:

- T1: convert the current final-library record without changing its scientific
  claims.
- T2: convert the current oligo record and the protocol-only TSV projection
  without changing their scientific claims.
- T3: when no current T3 exists, translate the ordered legacy HTML
  step-by-step workflow into the smallest scientifically sufficient molecular
  graph. This is a new-document conversion candidate, not approved ground
  truth.

T3 must use one workflow per weakly connected molecular process, retain shared
upstream states once, and put canonical modalities only on `final_outputs`.
Create a new state or transition only for a change in sequence architecture or
strand structure, or for a distinct branch product required downstream. Fold
cleanup, purification, size selection, QC, pooling, routine incubation, and
other non-structural handling into the nearest substantive transition.

Preserve uncertainty. Use `unknown`, `ambiguous`, `externally_completed`, or an
explicitly incomplete architecture where the legacy curation does not support
a stronger claim. Do not invent nucleotide sequences, strand structures,
reagents, operations, or missing steps to make the workflow look complete.
Current T2 identifiers may normalize legacy names, but T3 may reference only
oligos present in the converted T2 candidate. Sequencing primers are not
library-generation oligos merely because their sequence appears in a final
library.

Each physical strand is written independently 5' to 3'. Duplexes contain both
strands and explicit paired regions; RNA:DNA hybrids retain both molecules.
The terminal reference strands must match the converted T1 libraries in the
same orientation or token-aware reverse-complement orientation. Use the
embedded canonical schemas as formatting constraints, then make the three
candidates pass linked T1-T2-T3 validation.

For every T1 `library_sequence` and every T3 strand that supplies a complete
`sequence_architecture`, compare the assembled string with its ordered segment
projection. When every segment supplies exact bases, a placeholder, or a fixed
length, the token-aware projection must consume the complete assembled string.
A fixed-length placeholder may correspond to the same placeholder or the same
number of literal IUPAC bases, and nucleotide chemistry notation retains its
underlying base. Never omit a declared barcode, UMI, index, insert, or adapter
token from the assembled sequence merely because T1 and terminal T3 would still
match each other.

For each lineage row, cite only packet-listed legacy/current source IDs used to
create that task candidate. Lineage records provenance, not scientific support.
Return no findings, corrections, approvals, or primary-evidence conclusions.
