# Primary-evidence extraction phase

Do not infer anything from legacy curation, current benchmark records, prior
agent runs, training examples, review memory, or remembered kit knowledge.
Those inputs are intentionally absent.

1. Read `packet.json` and `manifest.json`.
2. Read every listed primary source completely. Cover all pages, sheets,
   tables, figures, diagrams, appendices, and embedded oligo material. Read
   every packet-listed deterministic rendition as an aid for native text,
   tables, and figures, but verify claims against the original source rather
   than treating a rendition as a separate scientific source.
   Manifest entries marked unavailable are provenance only; do not infer their
   contents or use external knowledge to fill them.
3. Account for every primary `source_id` exactly once in `source_coverage`.
   Mark a genuinely irrelevant source `not_relevant` with a reason. Marking a
   source `unreadable` blocks comparison and must not be worked around with
   memory.
4. Extract evidence-backed fields for:
   - T1: every distinct final sequencing library, structure, strand,
     orientation, modality, constant segment, and variable-region length;
   - T2: oligo names, source-visible aliases, sequences, roles, orientations,
     modifications, components, and families;
   - T3: molecular states and transitions, including initial/final states,
     substrates, operations, reagents/oligos, all relevant products,
     carried-forward and discarded products, complete strand architecture, and
     final-library linkage.
5. Review T2 and T3 in one chronological pass. Register an oligo with a stable
   T2 ID when it first appears, then use that same ID in each T3 transition
   that uses it. Prefer the smallest scientifically sufficient graph. Create
   states and transitions primarily for changes in molecular sequence
   architecture or strand structure. If neither changes, fold cleanup,
   purification, size selection, pooling, washing, QC, quantification,
   dilution, routine reagent handling, and inactivation into the nearest
   substantive transition's operation detail, major reagents, notes, and
   evidence. Add a separate non-sequence node only when a distinct branch or
   carried molecular product is essential downstream, and explain why. Keep
   only scientifically important products and dead ends; do not enumerate
   incidental fractions or minor procedural details. Do not create a
   transition for a figure or paragraph that merely displays the preceding
   product, and represent PCR cycling as one transition.
   Molecular states represent meaningful carried-forward products, not
   transient reaction complexes. For template switching, represent the
   pre-switch product as an mRNA:cDNA hybrid with exactly two logical strands:
   one RNA and one DNA. Put the template-switch oligo in the template-switching
   transition's `oligo_ids`, and represent the product as cDNA containing the
   incorporated TSO-derived sequence. Do not retain a third TSO strand in a
   state unless a packet-listed source explicitly establishes that the
   three-strand complex is carried forward.
6. For every T3 molecular state, do not infer a generic single- or
   double-stranded product. Use the controlled `strand_architecture` value
   (`single_stranded`, `double_stranded`, `partially_duplex`,
   `rna_dna_hybrid`, `y_shaped_duplex`, `mixed_population`, or `unknown`) and:
   - represent every physical strand explicitly under `strands`, always written
     in its own 5′→3′ direction, with segments listed in that same order;
   - set `reference_strand_id` to the strand that corresponds to the canonical
     T1 strand through the workflow;
   - split strand segments at paired/unpaired boundaries and label overhangs
     and internal unpaired regions with `structural_role`;
   - connect paired segment groups through `paired_regions`, listing each
     side's contiguous segment IDs in that strand's own 5′→3′ order; use
     `reverse_complementary` only when supported, and preserve a documented
     mismatch or unknown pairing instead of forcing complementarity;
   - record nicks, gaps, and breaks under `discontinuities`;
   - distinguish RNA and DNA strands in an RNA–DNA hybrid; and
   - keep placeholder roles biological and orientation-free: use
     `[I5_INDEX:8]`, `[TN5_INDEX:8]`, or `[I7_INDEX:8]`, never `_RC`,
     `_REVERSE`, or `_FWD` role variants; and
   - for each oligo-derived segment, put the bases in that modeled strand's
     `sequence`, retain the source-visible bases in the linked T2 oligo, and
     set `orientation_to_source` to `same_orientation`,
     `reverse_complement`, or `unknown`.
   Use `unknown` where the sources do not establish an architecture fact. Do
   not invent a complementary strand merely because a later step commonly
   produces one.
7. Before returning evidence, preflight every state against the exact
   `validate_molecular_state_architecture` contract in `groundtruth.py`:
   - strand IDs, state-wide segment IDs, paired-region IDs, and discontinuity
     IDs are unique; `reference_strand_id` resolves; every strand is `5_to_3`;
   - `single_stranded` has exactly one strand and no paired region;
     `double_stranded` has exactly two strands, at least one paired region, and
     no unpaired or overhanging segment; `partially_duplex` has at least two
     strands, at least one paired region, and at least one unpaired segment;
     `rna_dna_hybrid` has exactly two logical strands, one RNA and one DNA, and
     at least one paired region; `y_shaped_duplex` has exactly two strands, at least one
     paired region, and an unpaired arm on both strands. Declared pairing in
     `mixed_population` and `unknown` states must still satisfy the reference
     and ordering checks below;
   - each paired-region side resolves to a known strand, contains at least one
     segment from that strand, and lists contiguous segments in their 5′→3′
     order; the two sides use distinct strands; except for
     `mixed_population`, no segment appears in more than one paired region;
   - every segment labeled `paired_region` appears in a declared
     `paired_regions` entry. A segment absent from `paired_regions` must not be
     labeled `paired_region`; preserve genuinely unpaired random-primer,
     SMART, overhang, linker, and adapter regions with the appropriate
     unpaired structural role. Conversely, a declared paired-region side may
     contain only `paired_region`, `mixed`, or `unknown` segments. Never add a
     false pairing merely to satisfy validation;
   - an explicit `reverse_complementary` pair must be reverse-complementary.
     Use `documented_noncanonical` or `unknown` when supported instead of
     changing a source sequence; and
   - every discontinuity resolves to one strand and lies between two adjacent
     segments on that strand in 5′→3′ order.
   Repair representation and bookkeeping inconsistencies before emitting the
   artifact. Do not change scientifically supported sequence, strand identity,
   or pairing merely to pass validation.
8. Give every extracted field a stable ID and exact source locator. Record all
   transformations. Preserve source conflicts and alternatives rather than
   selecting one silently.

This phase produces evidence, not ground truth and not human decisions.
