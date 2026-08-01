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
   - T3: each ordered molecular step, its inputs, reagents/oligos,
     transformations, products, and final-library linkage.
5. Give every extracted field a stable ID and exact source locator. Record all
   transformations. Preserve source conflicts and alternatives rather than
   selecting one silently.

This phase produces evidence, not ground truth and not human decisions.
