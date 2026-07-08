# Protocol Processing Shared Rules

These rules apply to every protocol-processing task section. Task-specific instructions still define the required output schema and which section or sections to complete.

## Universal Source-Grounding Rules

- Use only the files provided by the task, normally the files downloaded into `input/` by `python fetch_input.py`.
- Do not search the web, use benchmark ground truth, use prior answer keys, or answer from protocol-name memory.
- Treat schema examples as shape examples only. Do not copy example bases, placeholder lengths, object names, or segment order unless the task input supports them.
- Ground every fixed base, variable-region length, barcode role, strand orientation, and workflow step in explicit source evidence.
- Parse the complete provided source set, including PDF-derived text, spreadsheet-derived text, appendices, supplementary tables, and pasted text blocks. Do not stop after the first obvious oligo section.
- Keep protocol versions separate. Only use oligos, barcode lengths, and workflow steps for the chemistry/version shown in the task input. Do not merge older or newer chemistry versions unless the source explicitly says they are identical.
- Prefer strong structured parsers for raw files. For PDFs in coding-agent tasks, use Docling with OCR disabled when available because it can combine native PDF text, layout prediction, and table structure. PyMuPDF (`import fitz`) and `pypdf` layout extraction are acceptable stable alternatives and cross-checks.
- Do not run OCR or use OCR-derived text as task evidence for raw PDFs; OCR can introduce unstable sequence conversions. Build an evidence view from native text-layer extraction, layout-sorted text blocks, table cells, appendix text, and stable vector/text-layer labels in diagrams. Render pages only for visual layout checks or to confirm that a sparse page is image-only, not to OCR-convert sequence text.
- For spreadsheets, use `openpyxl`; if needed, inspect zipped XML workbook parts directly.
- Preserve source-visible nucleotide/IUPAC motifs, chemical modifications, and structural descriptors as evidence when they are part of an oligo or library architecture, including examples such as `/5Phos/`, `/5Bio/`, `/5BiosG/`, `/3InvdT/`, `/3SpC3/`, `/5rApp/`, `/ddU/`, `(dU)`, `+`, `*`, `rG`, `rA`, `rU`, `rC`, `polyT`, and bead/linker labels. Put non-base chemistry or descriptors in names, roles, components, aliases, original/source text fields, or notes when the schema supports them; do not insert non-base prose into a normalized sequence string.
- Treat degenerate bases as sequence content when the source prints them as part of a primer or adapter. For example, `N`, `V`, `B`, `R`, `Y`, and related IUPAC letters are not generic placeholders unless the task schema asks you to convert a variable region into placeholders.
- Clean parser/text-extraction typography artifacts inside sequence strings: normalize `5′`, `5’`, `5ʹ`, `3′`, `3’`, and `3ʹ` to `5'` and `3'`; normalize dash variants such as `–`, `—`, and `−` to `-`; remove spaces inserted around sequence hyphens or terminal markers.
- Remove orientation wrappers and non-nucleotide descriptors from normalized sequence fields. For example, `5'-Bead-Linker-TTT...-3'`, `5'-bead-linker-TTT...-3'`, and `5' - Bead - Linker - TTT... - 3'` should become `TTT...` in `sequence`, while `Bead-Linker` can be represented in the oligo name, role, component, or notes if the schema supports it.
- Treat `*` between bases, such as `AGAGT*A*C`, as a chemical linkage marker rather than a nucleotide. In many oligo tables this denotes phosphorothioate bonds between adjacent bases. Preserve `*` in source-preserving outputs such as parser-control text extraction or original/source text fields, but remove `*` from normalized oligo `sequence` fields used by normal oligo-extraction tasks, yielding `AGAGTAC`. Preserve the linkage modification only in notes/components/source text fields if the schema supports it.
- Join wrapped sequence lines when an oriented sequence is split across line breaks, for example `5'-AAGC...AACGCA` followed by `GAGT...-3'`. Preserve the full joined sequence rather than emitting only the first line.
- Reject false-positive sequence candidates that are English words, legal boilerplate, PDF headers or footers, page labels, or prose that accidentally matches IUPAC nucleotide letters.
- Do not invent, repair, complete, or reverse-complement source sequences unless the specific task section asks you to construct a final library on a chosen strand.

## Oligo Extraction Section

- Extract all source-supported oligos, primers, adapters, adapter strands, and library-related nucleotide strings requested by the task.
- Search both explicit oligo/primer/adapter sections and final-library/library-structure sections. Useful cues include `Oligonucleotide Sequences`, `Adapter and primer sequences`, `Primer sequences`, `Reagents`, `Library Construction`, `Final library structure`, `Library structure`, `Sequencing library`, read-configuration sections, sample-index PCR sections, and PCR-product diagrams.
- Include linker, blocking, template-switching, PCR primer, index primer, sequencing primer, bead/gel-bead oligo, pre-amplification primer, P5/P7 adapter or primer, TruSeq/Nextera element, Tn5/transposase-binding element, hairpin adapter, splint oligo, probe, promoter, and primer-site/binding-site records when the task input supports them.
- Include source-visible final-library or product sequence strings when they are printed or recoverable from a source-visible diagram. Do not derive a final library product by protocol simulation in an extraction-only section; derived construction belongs to the library-construction section.
- If the task schema permits null sequences and an oligo is named but exact bases are not shown, include the named item with a null sequence and mark or note that the sequence is not shown. If the schema requires sequence strings only, omit missing-base items rather than inferring them.
- Preserve strand information when available. Use `direction` values such as `5_to_3`, `3_to_5`, or `unknown` when the task schema supports them.
- For double-stranded adapters, preserve both source-visible strands. Emit separate strands or a parent object with strand components if the task schema supports that shape. Do not drop short reverse strands merely because they are shorter than ordinary sequence-candidate thresholds when they appear in an oriented double-stranded adapter context.
- If the same sequence appears in different supported concepts, such as a sequencing primer and a strand of an adapter, keep both concepts when the task schema supports named records. Conversely, collapse true strand-pair adapters into one double-stranded object when the schema supports components.
- Do not collapse distinct singleton oligos such as linker strands, blocking strands, template-switching oligos, read sequencing primers, or separate P5/P7 primers into a generic family.
- For parser-control text tasks, keep input sources independent. Do not use `human` text to fill `mineru`, `pymupdf`, `pypdf`, or `docling`, and do not use one parser output to repair another.
- For normalized oligo summaries, collapse repeated oligos that share one architecture into one canonical entry, but keep truly different architectures separate.
- For normalized oligo summaries, do not output PCR products, linear amplification products, transposed DNA products, sample-index PCR products, final library top/bottom strands, or other workflow/product constructs as oligos. Use printed product diagrams only as evidence for component oligos, primers, adapters, barcode lengths, index roles, and orientation.
- Collapse large repeated barcode/index tables into one family only when rows share the same backbone and differ only by a clearly variable barcode, UMI, sample index, or similar region. Preserve example row evidence through aliases, components, or notes when the task schema supports them.
- If one name maps to multiple real sequences, emit stable variants instead of overwriting one with another.
- If one exact sequence appears under very different names, prefer the source-supported functional names and avoid deleting a concept solely because the sequence is duplicated.
- If oligo A contains the full source-supported sequence of oligo B and B is at least 5 nt, record B as a component of A when the task schema supports components and the source context supports containment.

## Library-Construction Section

- Build final sequencing-library architecture by simulating the protocol workflow forward from source evidence. The final library is often not printed verbatim.
- Inventory the source-visible building blocks first: capture/RT primers, bead oligos, template-switch oligos, ligation or round adapters, RT or Tn5 adapters, PCR primers, sequencing primers, and P5/P7 flow-cell adapters.
- Chain steps in protocol order, tracking what each biochemical operation adds, removes, copies, or selects: capture, reverse transcription, template switch or second strand, amplification, fragmentation or tagmentation, ligation, library PCR, and sequencing-ready product formation.
- Write scored final libraries as a single consistent 5'->3' top strand. Reverse-complement source oligos only when needed to place a bottom-strand oligo into the final top-strand construct.
- Resolve fragment selection. If fragmentation, tagmentation, restriction, or sonication creates multiple fragments, only the fragment retained by the final library-PCR primer pair reaches sequencing.
- When a step creates multiple product types, track which products are amplifiable and which are dead ends. Do not let dead-end products contribute to the final scored library.
- Reconstruct enzymatic junctions only when the source gives sequence basis for them. If a constant region is unsupported, represent it only as unknown/debug annotation if the schema allows it; do not supply remembered bases.
- Omit biological payloads such as cDNA, gDNA, insert, VDJ insert, and sgRNA spacer from scored final-library strings. Annotate them only in debug/display fields when the schema supports that.
- Reconcile the assembled structure against read configuration before finishing. Confirm what Read 1, Read 2, Index 1, and Index 2 read, which primers prime them, which strand they read, and whether cycle lengths fit the inferred regions.
- If the protocol produces multiple final libraries or modalities, output separate library entries rather than concatenating them.
- For multimodal or sub-library protocols, identify where shared processing diverges into separate library types, then construct each final library separately.

## Placeholder Rules

- In normalized oligo outputs, use bracket placeholders such as `[CELL_BARCODE:16]`, `[UMI:12]`, `[SAMPLE_INDEX:8]`, `[I5_INDEX:10]`, `[I7_INDEX:10]`, `[RT_BARCODE:10]`, `[TN5_INDEX:8]`, `[FEATURE_BARCODE:15]`, `[PHASE_BLOCK:4]`, `[RANDOM:9]`, and `[VARIABLE:4]`.
- Use `CELL_BARCODE` for cell/GEM/bead/well/plate/subarray/combinatorial barcode regions; `UMI` for molecular identifiers; `SAMPLE_INDEX`, `I5_INDEX`, or `I7_INDEX` for library indexes; `RT_BARCODE` for reverse-transcription barcodes; `TN5_INDEX` for tagmentation indexes; and `FEATURE_BARCODE` for feature, capture, antibody, or guide-capture barcodes.
- Use `RANDOM`, `PHASE_BLOCK`, or `VARIABLE` only for non-biological randomers, phase blocks, spacers, degenerate bases, overhangs, and other structural variable bases when a more specific role is not supported.
- If a source gives a range such as `[0-4 bp PB]`, use the maximum explicit length for a single scored representation.
- Bare labels without length, such as `[i7]`, `[barcode1]`, or `[CLS1]`, are not enough by themselves. Infer their length from the task input before emitting a scored placeholder.
- In final-library `library_sequence` strings, use the placeholder alphabet required by that task schema: `#` for cell barcode, `~` for UMI, `@` for sample/i5/i7 index, `&` for ligation barcode, `=` for RT barcode, `%` for Tn5/tagmentation index, `$` for feature/capture/antibody barcode, and `?` for non-biological spacer/linker/phase/random/degenerate/overhang regions.
- Do not use literal base letters or IUPAC ambiguity symbols such as `B`, `U`, `I`, `R`, `T`, or `V` as generic placeholder runs. Preserve IUPAC letters only when they are source-visible sequence motifs, such as anchored oligo-dT suffixes `VN` or `TVN`.
- Expand compact source shorthand such as `T(30)` or `A(30)` only when the task schema or scoring representation expects expanded bases. Otherwise preserve the source-visible shorthand in display fields.

## Self-Check And Review Triggers

- A prediction with only one or two oligo records is usually incomplete unless the full source truly contains only one or two exact sequence-bearing oligo, adapter, primer, or library-related entries.
- Re-scan every line containing terms such as `oligo`, `primer`, `adapter`, `adaptor`, `bead`, `gel bead`, `index`, `barcode`, `UMI`, `read 1`, `read 2`, `P5`, `P7`, `Nextera`, `TruSeq`, `TSO`, `cDNA`, `pre-amp`, `PCR`, `linker`, `blocking`, `hairpin`, `Tn5`, `transposase`, `splint`, `probe`, and `promoter`.
- Flag or note uncertainty when a named oligo lacks bases, the same name has multiple different sequences, the same sequence has very different names, direction is unclear, a double-stranded pair is only partially complementary, barcode/UMI/index length may change by version, a sequence appears only in a diagram, or an item may be a primer site/binding site rather than an ordered oligo.
- Before finalizing a construction task, verify the step-by-step path is continuous from input molecule to final library and that the final library explains all sequencing reads.

## Output Hygiene

- Write exactly the artifact path requested by the task, usually `/logs/artifacts/prediction.json`.
- The artifact must contain JSON only. Do not put Markdown, comments, or explanations in it.
- Before finishing, check that the artifact exists, is non-empty, and parses with `python -m json.tool`.
