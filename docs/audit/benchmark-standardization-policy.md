# Benchmark Standardization Policy

Policy identifier: `libstruct.benchmark-standardization`.

This policy controls benchmark representation and matching only. It must not
contain protocol-specific sequences, source conclusions, or learned agent
memory.

## Evidence versus scoring representations

Preserve source-visible strings and orientations in evidence. Approved T1
stores one canonical `library_sequence`; retain biological insert locations in
that sequence with placeholders such as `[CDNA]`. Detailed roles remain in the
T1 segment decomposition. Do not store a duplicate
`annotated_library_sequence` or a benchmark scoring projection in approved T1.
A canonical scoring representation is derived downstream and must cite the
transformation that produced it. Biological inserts may be omitted only from
that derived scoring projection.

## Variable regions

Use `[ROLE:LENGTH]` for a source-supported fixed-length non-biological variable
region. The role and length must be explicit or reproducibly derivable. Do not
invent a length for an unlabeled or proprietary region. Preserve ranges as an
ambiguity unless a separately reviewed benchmark rule selects a value.

`ROLE` is biological, never directional. Use canonical placeholders such as
`[I5_INDEX:8]`, `[TN5_INDEX:8]`, and `[I7_INDEX:8]`. Never append `_RC`,
`_REVCOMP`, `_REVERSE`, `_FWD`, or another orientation marker to a placeholder
role. In particular, `[TN5_INDEX_RC:8]` and `[I7_INDEX_RC:8]` are invalid.

## Orientation

Store oligo sequences in their source-visible orientation and record
`5_to_3`, `3_to_5`, or `unknown`. Reverse complementation is a transformation,
not an implicit normalization. Final-library strands remain distinct. T3
molecular states contain explicit physical strands, each recorded 5′→3′;
duplex pairing is represented between strands rather than by assigning one
orientation to the whole state. A `partially_duplex` state may instead contain
one physical strand with a paired region between two disjoint arms of that same
strand, representing a source-supported intramolecular hairpin. Use state
properties to distinguish `hairpin` and `covalently_closed_dumbbell` topology.
Oligo-derived T3 segments explicitly record
whether they preserve or reverse-complement the T2 oligo orientation.

For an oligo-derived T1 or T3 segment, `sequence` stores the exact bases on the
modeled strand. The referenced T2 record's `sequence` stores the source-visible
oligo bases. The segment's `oligo_derivations[].orientation_to_source` stores
`same_orientation`, `reverse_complement`, or `unknown`. For example, a T1
segment may use placeholder `[TN5_INDEX:8]` and sequence `CGCGGTTC`, link to a
T2 oligo whose sequence is `GAACCGCG`, and record
`orientation_to_source: reverse_complement`. Do not copy the T2 source sequence
into the segment or encode this relationship as `[TN5_INDEX_RC:8]`.

## Names and aliases

Use one reviewed canonical identifier for commonly reused adapters and oligos.
Assay-specific oligos retain the source paper or protocol name. Every
source-visible alternate name remains an alias. Sequence equality alone does
not establish identity; role, orientation, modifications, and human review are
also required.

## Modalities

Approved T1 libraries and T3 workflows use exactly one of these human-readable
modality labels: `gene expression`, `genomic DNA`, `feature barcode`, `sgRNA`,
or `chromatin accessibility`. Do not store abbreviations, capitalization
variants, snake case, protocol names, or the generic value `library` as a
modality. Benchmark predictions normalize common aliases—such as `RNA`,
`scRNA-seq`, `gDNA`, `feature_barcode`, `ATAC`, `scATAC`, and
`chromatin_accessibility`—to this vocabulary for workflow matching. This
label normalization is not a scientific distinction.

## Evaluator assignment

For T1, use a global optimal one-to-one sequence assignment; cleaned T1 does
not store library identifiers. Modality is retained as a diagnostic and must
not override a better structural match. T1 reward is the soft F1 of sequence
similarities, so missing and extra libraries are both penalized.

For T2, let `O_used` contain every T2 ID referenced by T3 transition
`oligo_ids` or state-segment `oligo_derivations`. For a source-only benchmark,
let `O_score` restrict `O_used` to sequence claims marked `explicit` or
`derivable` from the agent-visible source bundle. Externally completed,
ambiguous, and unsupported claims remain in approved ground truth but are
neutral in source-only scoring. All records outside `O_used` are optional. Use
normalized nucleotide sequence alone with global optimal one-to-one
assignment; names, aliases, roles, orientation, modifications, and support
metadata do not affect sequence assignment within `O_score`. Exact predictions
of optional or neutral claims are neutral, while an unmatched unknown or
duplicate prediction reduces precision. Never use order-dependent greedy
matching.

For T3, require exactly one workflow per modality and match workflows by
canonicalized modality, including the reviewed aliases above. Preserve
alternative routes for one modality as branches
inside that workflow. The primary score is molecular-transition soft F1 using
operation, semantically matched substrate and product states,
carried/discarded classification, and transition-local T2 sequence multisets.
Major reagent names are diagnostic. Also report typed-edge F1 for substrate,
carried-product, and discarded-product edges after state and transition
alignment. Apply the same source-recoverability mask to supported state
architecture, transition, transition-local oligo, and typed-edge claims. Do not
use spectral or graph-distance metrics.
