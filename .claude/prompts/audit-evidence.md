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
     carried-forward and discarded products, and final-library linkage.
5. Review T2 and T3 in one chronological pass. Register an oligo with a stable
   T2 ID when it first appears, then use that same ID in each T3 transition
   that uses it. Create a state only for a meaningful molecular or physical
   change. Do not create a transition for a figure or paragraph that merely
   displays the preceding product, and represent PCR cycling as one transition.
6. Give every extracted field a stable ID and exact source locator. Record all
   transformations. Preserve source conflicts and alternatives rather than
   selecting one silently.

This phase produces evidence, not ground truth and not human decisions.
