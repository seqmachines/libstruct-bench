# Libgen ground-truth migration and scoring impact

> Historical note: this report documents the superseded 2.0.0
> workflow-per-modality migration. Libgen 3.0.0 instead uses one workflow per
> connected molecular process with modality-labelled `final_outputs`; its
> baselines must be rerun.

Inspection date: 2026-08-10

Input snapshot: `/Users/seqmachines/playground/protocols-test/ground_truth`

The SHA-256 digest of the sorted SHA-256 listing for the 60 inspected T1/T2/T3
files is `a2c1f577ad263cc603910658da064f02e0032f87dc23413116851ac95b7be366`.
This report was written before modifying the canonical schemas, validators,
scorer, or approved JSON files.

## Inventory

All 20 protocols contain a complete linked T1/T2/T3 bundle.

| Quantity | Count |
| --- | ---: |
| T1 libraries | 25 |
| T2 oligos | 265 |
| T3 workflows | 20 |
| T3 states | 165 |
| T3 transitions | 123 |
| T2 records referenced by T3 | 167 |
| T2 records not referenced by T3 | 98 |
| Dangling T3-to-T2 references | 0 |

The required T2 count is derived from the union of transition `oligo_ids` and
state-segment `oligo_derivations[].oligo_id`. It is not stored in T2.

| Protocol | T1 modalities | T2 required / optional | Current workflows |
| --- | --- | ---: | ---: |
| `10x_chromium_3_feature_barcoding` | RNA; feature_barcode | 11 / 5 | 1 |
| `10x_chromium_3_gene_expression_v4` | gene_expression | 9 / 3 | 1 |
| `10x_chromium_single_cell_atac_v2` | scATAC | 5 / 6 | 1 |
| `cel_seq` | scRNA-seq | 6 / 6 | 1 |
| `ddseq_single_cell_3_rna_seq_kit` | cDNA library; DO library | 9 / 7 | 1 |
| `dr_seq` | RNA; gDNA | 6 / 0 | 1 |
| `drop_seq` | scRNA-seq | 8 / 7 | 1 |
| `indrop_v1` | scRNA-seq | 8 / 10 | 1 |
| `microwell_seq` | library | 5 / 10 | 1 |
| `petri_seq` | scRNA-seq | 14 / 6 | 1 |
| `pip_seq_v4` | RNA | 7 / 6 | 1 |
| `s3_atac` | ATAC | 5 / 4 | 1 |
| `scdamid` | library | 9 / 3 | 1 |
| `sci_atac_seq` | chromatin_accessibility | 7 / 4 | 1 |
| `sci_rna_seq` | single_cell_rna_seq | 6 / 4 | 1 |
| `scrrbs` | library | 5 / 3 | 1 |
| `seq_well_s3` | single_cell_rna_seq | 6 / 3 | 1 |
| `share_seq` | ATAC; RNA | 17 / 3 | 1 |
| `smart_seq` | RNA (two alternative libraries) | 10 / 4 | 1 |
| `split_seq` | single-cell RNA-seq | 14 / 4 | 1 |

## T2 reference audit

### Missing and dangling records

No T3 transition or segment derivation references a missing T2 `oligo_id`.
Consequently, there are no currently missing required T2 records and no
dangling T3 oligo references.

### Platform sequencing primers currently made required

Seven sequencing-primer-like T2 records are currently referenced by T3. Their
uses separate into two groups.

These four are referenced only because a state segment has the same sequence as
a platform sequencing primer. They are not the physical library-generation
oligo and should cease to determine required-T2 recall:

- `10x_chromium_3_gene_expression_v4`:
  `oligo_illumina_truseq_read_1_primer` and
  `oligo_illumina_truseq_read_2_primer`;
- `indrop_v1`: `oligo_truseq_read_1_sequencing_primer_partial`;
- `scdamid`: `oligo_truseq_read_1_sequencing_primer_partial`.

Their state segments should instead derive from the physical capture,
adapter, or PCR oligo that incorporated the sequence. A sequence match alone
must not establish oligo provenance.

These three are legitimate required records because the named material is
also physically used during library generation:

- `ddseq_single_cell_3_rna_seq_kit`:
  `oligo_illumina_nextera_read_2_primer` is the s7-ME transposome oligo;
- `scdamid`: `oligo_illumina_truseq_read_1_primer` is the Y-adapter bottom
  strand used in ligation;
- `share_seq`: `oligo_illumina_nextera_read_1_primer` is a transferred Tn5
  strand used during ATAC and cDNA tagmentation.

Removing the four incorrect provenance links lowers the derived required set
from 167 to 163 without deleting any T2 record.

### Unreferenced library-generation candidates

Most of the 98 optional records are correctly optional platform sequencing
primers, standalone flow-cell sequences, QC probes, or unselected protocol
variants. The following groups warrant audit attention because they describe
physical library-generation or bead-synthesis material but are not referenced
by the current T3 graph:

- `cel_seq`: the separately recorded T7 promoter element;
- `indrop_v1`: the acrydite primer, BA19 block, and round-1/round-2 bead
  synthesis barcode plates;
- `microwell_seq`: the three split-pool bead-synthesis oligo sets;
- `drop_seq` and `microwell_seq`: alternative SeqB bead-capture oligos.

These are not automatically added to the required set. Some describe upstream
bead manufacture or a protocol variant outside the scored workflow. They need
scientific adjudication before T3 gains a new transition or derivation.

### Incorporated segments lacking provenance

A conservative sequence-overlap scan found 119 newly appearing segment
instances (101 distinct role/source candidate groups) with no
`oligo_derivations`, despite matching a T2 sequence or component in the same
protocol. This is a review queue, not 119 confirmed defects: repeated adapter
motifs and sequencing-primer sites often match multiple T2 records.

The largest queues are feature barcoding (18), ddSEQ (22), PIP-seq v4 (11),
SHARE-seq (11), and SPLiT-seq (11). Deterministic validation should report
these candidates with their state, strand, segment, and possible T2 sources,
but must not infer provenance from sequence equality. Human-approved T3
derivations remain the only source of required-T2 membership.

## Modality migration

The current T3 schema requires one root-level `modality`, and none of the 20
workflow objects has its own modality. The migration removes the root field and
requires `workflow.modality`.

Sixteen protocols receive the corresponding T1 modality on their sole
workflow. `smart_seq` remains one RNA workflow with two internal routes and two
final states.

Four mixed workflows must be split while preserving their existing molecular
states and operations:

- `10x_chromium_3_feature_barcoding`: RNA and feature_barcode;
- `ddseq_single_cell_3_rna_seq_kit`: cDNA library and DO library;
- `dr_seq`: RNA and gDNA;
- `share_seq`: ATAC and RNA.

Shared experimental operations may appear in both modality graphs, but each
workflow keeps only the substrates, products, oligos, and final states relevant
to its modality. Alternative routes that produce the same modality remain
branches inside one workflow.

After migration there will be 24 workflows, exactly one per distinct T1
modality in each protocol.

## Scoring impact

### T2

- In benchmark `2.0.0`, the primary metric is `t2_required_family_f1`.
- `O_used` contains the T2 families referenced by T3; the source-only recall
  denominator `O_score` further restricts these to explicit or derivable
  sequence claims.
- Flat sequences and ordered components are canonicalized to one molecule-level
  representation. Fixed-length placeholders define family templates. Valid
  concrete members collapse before scoring, while scaffold, role, orientation,
  and modification differences keep predictions in separate families.
- Names, aliases, and kind remain diagnostics. Role, orientation, and
  modifications bound family collapse but do not otherwise change the
  sequence-soft match score. Externally completed, ambiguous, and unsupported
  claims remain canonical but are neutral.
- The current `1e-9` name-based assignment tie-break is removed.
- Exact predictions of optional T2 families are neutral. Additional valid
  members of a recovered family are collapsed; unrelated extra families still
  reduce precision.
- `t2_exact_required_family_recall` reports the fraction of required families
  matched exactly. Unlike the retired all-or-nothing diagnostic, unrelated
  extras do not change this recall value.
- Concrete ground-truth records without a family template remain individual
  member-level requirements.

### T3

- Workflows are paired by exact modality instead of flattening every workflow.
- Semantic state matching remains an internal alignment step. `t3_state_f1` is
  surfaced as a complementary architecture diagnostic, not a reward component.
- `t3_molecular_transition_f1` becomes the primary T3 metric. Transition
  similarity uses operation, matched substrates/products, carried/discarded
  classification, and transition-local T2 family-sequence multisets.
- T3 oligo use no longer consumes a predicted-ID-to-gold-ID map from T2
  assignment.
- `t3_typed_edge_f1` measures substrate, carried-product, and
  discarded-product edges after state and transition matching.
- State architecture, transitions, transition-local oligos, and typed edges
  use the same source-recoverability mask where support metadata applies.
- Major reagent names remain diagnostic. No spectral or graph-distance metric
  is introduced.

The overall benchmark reward remains 30% T2 and 70% T3, now using
`t2_required_family_f1` and `t3_molecular_transition_f1` respectively.
Standard Harbor reporting contains only `reward`, those two primary metrics,
`t2_exact_required_family_recall`, `t3_state_f1`, and `t3_typed_edge_f1`. The remaining
precision/recall, metadata, boundary, reagent, and count diagnostics are kept
in the verifier details artifact rather than the headline reward metrics.

## Migration controls

1. Change the canonical and prediction T3 schemas first.
2. Migrate the 20 T3 documents without changing nucleotide sequences,
   orientations, state content, transition order, or T1/T2 scientific values.
3. Validate each linked T1/T2/T3 bundle and require one workflow per T1
   modality.
4. Derive required T2 IDs at runtime from the approved T3 graph.
5. Keep candidate provenance defects in the audit report unless a deterministic
   correction is already established by an approved T3 transition.
6. Run perturbation tests before regenerating or scoring Harbor tasks.

## Post-migration verification

The deterministic migration completed without changing any T1 or T2 file. It
rewrote the 20 T3 files to place modality on workflows, split the four mixed
graphs, and replaced the four sequencing-primer provenance links identified
above with the physical library-generation oligos already present in T2.

| Quantity | Post-migration count |
| --- | ---: |
| Valid linked protocol bundles | 20 / 20 |
| T3 workflows | 24 |
| T3 states | 168 |
| T3 transitions | 130 |
| Derived required T2 records | 163 |
| Optional T2 records | 102 |
| Missing or dangling required T2 records | 0 |

The state and transition totals increase because shared operations in the four
formerly mixed graphs are represented inside each applicable modality
workflow; the migration did not invent new nucleotide sequence claims. The
SHA-256 digest of the sorted post-migration 60-file hash listing is
`2273ed3dd56f40e2ef3ed40a263989dd25c5e80cb9b738f06fa2e0e1bba37682`.

The migration produced 163 T3-referenced records in `O_used`: 116 explicit, 6
derivable, 39 externally completed, and 2 ambiguous by record-level support
status. The current source-only scorer keeps all 163 in canonical ground truth
but excludes externally completed, ambiguous, and unsupported sequence claims
from `O_score`. Their T3 inclusion and source availability remain visible for
audit and error analysis rather than being mislabeled as agent failures.

Three sequencing-primer-named records remain required, but their approved T2
roles show that the physical molecules are also used in library generation:
ddSEQ s7-ME, the scDamID Y-adapter bottom strand, and a SHARE-seq transferred
Tn5 strand. They are not platform-only sequencing primers.
