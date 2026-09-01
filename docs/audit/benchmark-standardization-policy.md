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

When an assembled T1 sequence or T3 strand architecture is complete and all of
its ordered segments are deterministic (exact sequence, placeholder, or fixed
length), the token-aware segment projection must consume the complete assembled
string. A fixed-length placeholder may be rendered as literal IUPAC bases of
the same length, and nucleotide chemistry notation retains its underlying
base. Validate this within each record before comparing T1 with terminal T3;
two assembled strings that omit the same declared segment are not consistent.
A declared UMI, barcode, index, insert, or adapter segment must occur in the
assembled string.

For compatibility with already promoted records, deterministic validation may
tolerate an unsegmented trailing architecture suffix after every declared
segment has mapped contiguously from the 5' end. That tolerance is not a
canonical authoring convention: new records must describe the complete
assembled molecule, and an unsegmented suffix remains reviewable as a
segment-ledger completeness defect.

## Variable regions

Use `[ROLE:LENGTH]` for a source-supported fixed-length non-biological variable
region. The role and length must be explicit or reproducibly derivable. Do not
invent a length for an unlabeled or proprietary region. Preserve ranges as an
ambiguity unless a separately reviewed benchmark rule selects a value.

`ROLE` is biological, never directional. Use canonical placeholders such as
`[I5_INDEX:8]`, `[TN5_INDEX:8]`, and `[I7_INDEX:8]`. Never append `_RC`,
`_REVCOMP`, `_REVERSE`, `_FWD`, or another orientation marker to a placeholder
role. In particular, `[TN5_INDEX_RC:8]` and `[I7_INDEX_RC:8]` are invalid.

For scoring only, normalize these protocol-neutral biological-payload spelling
aliases while preserving the source-visible value in evidence and approved
records:

- `GENOMIC_DNA`, `GENOMIC_DNA_INSERT`, and `GENOMIC_INSERT` to `GDNA`;
- `CDNA_INSERT` to `CDNA`;
- `MRNA_BODY`, `MRNA_INSERT`, `RNA_INSERT`, `RNA_TRANSCRIPT`, and `TRANSCRIPT`
  to `MRNA`;
- `POLY_A` and `POLY_A_TAIL` to `POLYA`;
- underscore-separated `POLY_T`, `POLY_U`, and `POLY_C` to `POLYT`, `POLYU`,
  and `POLYC`.

For scoring, normalize the legacy benchmark placeholder `RT_BARCODE` to the
agent-visible `CELL_BARCODE` role while preserving its declared length. This is
a vocabulary projection, not permission to merge barcodes with different
lengths or controlled functions.

Do not collapse chemistry, processing, or topology qualifiers such as
`BISULFITE_CONVERTED`, `FRAGMENT`, `TN5_TAGGED`, or `METHYLATED`. A specific
prediction may satisfy a same-length ground-truth `VARIABLE` placeholder,
because the truth makes no stronger role claim; the reverse is not true.

For T3 state scoring only, the predicted two-base IUPAC anchored-primer
shorthand `VN`, or its opposite-strand form `NB`, may satisfy a same-length
ground-truth `[ANCHOR:2]` span. This equivalence is directional and applies to
the complete two-token phrase: it does not make either base an `ANCHOR`
wildcard, does not apply to other literal two-base strings, and does not allow
`VN` or `NB` to satisfy another placeholder role. Preserve the source-visible
spelling in evidence and approved records.

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

Approved T1 libraries and T3 `final_outputs` use exactly one of these
human-readable modality labels: `gene expression`, `genomic DNA`,
`feature barcode`, `sgRNA`, or `chromatin accessibility`. Do not store abbreviations,
capitalization variants, snake case, protocol names, or the generic value
`library` as a modality. Benchmark predictions normalize common aliases—such as `RNA`,
`scRNA-seq`, `gDNA`, `feature_barcode`, `ATAC`, `scATAC`, and
`chromatin_accessibility`—to this vocabulary for terminal-output and workflow
matching. This
label normalization is not a scientific distinction.

## Prediction validation

Use one prediction-validation contract for both the agent-visible validation
command and grading eligibility. Canonical ground-truth validation may impose
stricter authoring invariants, but those invariants must not be applied to a
prediction unless they are also present in the agent-visible contract. A
schema-valid prediction with an imperfect scientific representation remains
scoreable; the relevant scientific dimensions receive reduced credit instead
of invalidating every task metric.

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
global optimal one-to-one assignment with normalized nucleotide sequence as
the primary assignment key and structured scientific claims only as a
deterministic tie-break. Never use order-dependent greedy matching.

T2 reward is a weighted scientific-family similarity: canonical nucleotide
sequence and ordered molecule structure `0.65`, positional modifications and
chemistry `0.15`, molecular kind/assembly `0.10`, source-visible orientation
`0.05`, and controlled functional role `0.05`. Renormalize over dimensions the
ground truth leaves unsupported or unknown. Names, aliases, and free-text role
wording remain diagnostic and do not affect reward. Functional-role credit
comes only from protocol-neutral controlled categories such as reverse-
transcription primer, template-switching oligo, ligation barcode, ligation
linker, blocking oligo, amplification primer, indexing primer, sequencing
primer, primer entry point, and tagmentation adapter. Keep the broad canonical
labels `primer` and `adapter` scorable: a more specific predicted primer role
satisfies ground-truth `primer`, while generic predicted `primer` does not
satisfy a specific ground-truth role. A predicted assembled oligo satisfies
ground-truth `adapter` when its primary role or an explicitly declared
component has a controlled adapter function. Do not otherwise treat primers
and adapters as interchangeable. An unclassified ground-truth role is neutral;
an unclassified prediction does not satisfy a classified ground-truth role.
Parse the oligo's primary function separately from a handle, adapter, or end it
mentions as an annealing target. In particular, an indexed PCR primer does not
also become an RT primer or tagmentation adapter merely because its role text
says it acts at the RT-handle or Tn5-adapter end.

Derive nucleotide and chemistry projections separately. Inline chemistry such
as `/5Phos/`, `/5Biosg/`, or `/ideoxyU/` and an equivalent positional claim in
the molecule or component `modifications` field represent the same chemistry;
do not require the prediction to duplicate the chemistry in both places.
An empty ground-truth `modifications` list makes no source-supported chemistry
claim; it is not evidence that the oligo has no physical attachment or
modification. Treat that unannotated field as a wildcard with full modification
credit so an additional prediction claim is not penalized and the fixed T2
dimension weights do not change. Full credit here means "not contradicted by
the benchmark," not independent verification of the predicted chemistry.
Canonicalize redundant generic wording under a more specific claim in the same
record, such as "RNA ribonucleotides" alongside an explicit `rGrGrG` triplet.
Modified bases retain their underlying nucleotide in the sequence projection.
Keep position-changing or chemically distinct claims separate. Exact required-
family recall requires equality on every enabled scientific dimension, not
sequence alone. Exact predictions of optional or neutral scientific families
are neutral, while an unmatched unknown or duplicate prediction reduces
precision.

For T3, require one weakly connected workflow per molecular process. Store
modality only on `final_outputs`, preserve shared ancestors once, and retain
modality-specific or alternative routes as branches in that connected DAG.
Assign predicted and ground-truth workflows globally using terminal modalities,
states, and transitions, then score each connected DAG once without
modality-specific projections. The primary score is molecular-transition soft
F1 using operation, semantically matched substrate and product states,
carried/discarded classification, and transition-local T2 sequence multisets.
Major reagent names are diagnostic. Also report typed-edge F1 for substrate,
carried-product, and discarded-product edges after state and transition
alignment. Apply the same source-recoverability mask to supported state
architecture, transition, transition-local oligo, and typed-edge claims. Do not
use spectral or graph-distance metrics.

State and transition alignment is a global maximum-weight partial one-to-one
assignment. A candidate pair whose assignment similarity is below `0.25`
remains unmatched even when the predicted and ground-truth inventories have the
same size. Missing and extra entities continue to count in the soft-F1
denominators. For state assignment only, use initial, intermediate, terminal,
or combined initial-terminal workflow position as a bounded `0.10` tie-break.
For transition assignment, retain the bounded molecular-event identity
tie-break described below. These tie-breaks affect assignment only; reward uses
the ordinary scientific similarity of each retained pair.

State reward emphasizes scientific representation rather than prose. Weight
reference-strand sequence `0.50`, controlled strand architecture `0.15`,
ordered strand and segment structure `0.20`, and pairing/discontinuities
`0.15`. Within a segment, weight normalized sequence `0.75` and controlled
structural role `0.25`; free-text segment roles do not affect reward. Compare
both a strand's complete `sequence_architecture` and its deterministic ordered
segment projection, taking the best supported representation. `physical_state`
and `properties` remain diagnostics only. For terminal states sharing a
normalized modality, accept either the direct reference architecture or its
token-aware reverse complement, consistent with the physical-strand policy.
When multiple paired-region records jointly cover every segment declared as
one continuous duplex between the same physical strands and relationship,
compare that complete paired coverage once. Do not penalize a prediction solely
for using one region where ground truth partitions the same coverage into
several; missing paired segments, unpaired intervals, overhangs, relationship
changes, and discontinuities remain distinct claims.

Treat `extension` and `strand_synthesis` as equivalent controlled labels for
operation scoring. For transition assignment, use operation identity and any
scorable physical-oligo identity only as a bounded tie-break after the ordinary
operation/substrate/product/disposition/oligo score. This prevents an adjacent
cleanup transition from taking a molecular event's match without overriding a
materially better topology match.
