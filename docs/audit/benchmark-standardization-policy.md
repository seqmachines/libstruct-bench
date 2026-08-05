# Benchmark Standardization Policy

Policy identifier: `libstruct.benchmark-standardization`.

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
orientation to the whole state. Oligo-derived T3 segments explicitly record
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

## Evaluator assignment

For T1, use a global optimal one-to-one sequence assignment; cleaned T1 does
not store library identifiers. Modality is retained as a diagnostic and must
not override a better structural match. T1 reward is the soft F1 of sequence
similarities, so missing and extra libraries are both penalized.

For T2, use a global optimal one-to-one sequence assignment. Names and
orientations are retained as diagnostics; they do not silently change or
reverse-complement a source sequence. Never use order-dependent greedy
matching.
