# Benchmark Standardization Policy

Policy version: `libstruct.benchmark-standardization.v1`.

This policy controls benchmark representation and matching only. It must not
contain protocol-specific sequences, source conclusions, or learned agent
memory.

## Evidence versus scoring representations

Preserve source-visible strings and orientations in evidence. A canonical
scoring representation is a derived field and must cite the transformation
that produced it. Biological inserts may be omitted only in the scoring
projection; their location remains explicit in the annotated structure.

## Variable regions

Use `[ROLE:LENGTH]` for a source-supported fixed-length non-biological variable
region. The role and length must be explicit or reproducibly derivable. Do not
invent a length for an unlabeled or proprietary region. Preserve ranges as an
ambiguity unless a separately reviewed benchmark rule selects a value.

## Orientation

Store oligo sequences in their source-visible orientation and record
`5_to_3`, `3_to_5`, or `unknown`. Reverse complementation is a transformation,
not an implicit normalization. Final-library strands remain distinct.

## Names and aliases

Use one reviewed canonical identifier for commonly reused adapters and oligos.
Assay-specific oligos retain the source paper or protocol name. Every
source-visible alternate name remains an alias. Sequence equality alone does
not establish identity; role, orientation, modifications, and human review are
also required.

## Evaluator assignment

For T1, lock unique stable library identifiers first. Normalize identifiers
and modalities with Unicode-compatible case folding and collapse each run of
non-alphanumeric separators to one underscore. Lock a unique modality only
after identifier matches are removed. Assign all remaining entries with a
global optimal one-to-one sequence match. T1 reward is the soft F1 of sequence
similarities, so missing and extra libraries are both penalized.

For T2, use a global optimal one-to-one sequence assignment. Names and
orientations are retained as diagnostics; they do not silently change or
reverse-complement a source sequence. Never use order-dependent greedy
matching.
