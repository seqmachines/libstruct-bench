# Defect report: the T3 state contract cannot express intramolecular pairing

Status: **resolved**, 2026-08-11. Filed from the `lianti` audit the same day.
Engineering hand-off — not a normative policy document.

Both defects were fixed. `_pairing_side_segments` now permits
`side_1.strand_id == side_2.strand_id` when the architecture is
`partially_duplex`, and `partially_duplex` no longer carries a two-strand
minimum, so a one-strand closed dumbbell validates. The skill contract adds the
matching rules, including that a carried closed dumbbell and its enzymatically
opened product are separate states.

`lianti` resumed on the same day and was finalized and promoted with the
faithful representation: both transposon-end stems declared as intramolecular
`reverse_complementary` paired regions, and `state_closed_dumbbell` carried as a
one-strand `partially_duplex` state between ligation and USER excision. No
segment anywhere in that record still carries the `mixed` workaround. T3 grew
from 10 states / 8 transitions to 12 / 10.

The rest of this document is retained as the record of what was requested and
why.

## Summary

Two molecules in LIANTI are genuinely self-paired. The schema cannot represent
either, so the candidate graph is less faithful than the primary evidence.

## Defect 1 — a paired region cannot lie within one strand

`validate_molecular_state_architecture` in
`src/libstruct_bench/audit/groundtruth.py` requires each paired-region side to
resolve to a **different** strand:

> Pairing sides must resolve to different strands and contain nonempty,
> contiguous segment IDs in 5′→3′ order.

A hairpin stem pairs a strand with itself, so it cannot be declared at all.

**The molecule.** The LIANTI transposon end is a hairpin: a 19-bp mosaic-end
stem closed by a single-stranded T7-promoter loop. This is not a curation
artifact — the primary source states it directly (Chen et al., *Science*
356:189–194 (2017), Fig. 1B):

> LIANTI transposon consists of a 19-bp double-stranded transposase binding
> site and a single-stranded T7 promoter loop.

The stem is the transposase binding site, i.e. the functional heart of the
method.

**Current workaround.** Four segments in `state_tagmented_gdna` —
`tag_top_me_nontransferred`, `tag_top_me_transferred`,
`tag_bottom_me_nontransferred`, `tag_bottom_me_transferred` — carry
`structural_role: "mixed"` plus an explanatory state property. That records
*that* a stem exists without asserting the pairing, which is honest but lossy:
nothing says which segments pair, or that the pairing is reverse-complementary.

**Requested change.** Allow a paired region whose two sides name segments on the
same `strand_id`, with the existing contiguity and 5′→3′ ordering rules applied
per side. The reverse-complementarity check should apply unchanged — a stem is
still a duplex. Consider requiring that the two sides be disjoint and separated
by at least one intervening segment (the loop), which distinguishes a genuine
hairpin from a malformed self-reference.

## Defect 2 — no architecture value for a covalently closed duplex

`strand_architecture` is an enum of `single_stranded`, `double_stranded`,
`partially_duplex`, `rna_dna_hybrid`, `y_shaped_duplex`, `mixed_population`,
`unknown`. None describes a molecule that is **one physical strand** yet
**largely duplex**.

**The molecule.** NEBNext hairpin adaptors ligated at both ends of a dA-tailed
fragment, with both nicks sealed, give a covalently closed dumbbell. It stays
closed until USER excises the dU and opens it into the Y-shaped duplex. Legacy
`LIANTI.html` step (10) draws exactly this.

Declaring it `single_stranded` is false (it is mostly paired). Declaring it
`double_stranded` is false (there is one strand, and the validator would demand
two). `partially_duplex` still requires ≥2 strands.

**Current workaround.** Legacy steps (10) and (11) are merged into one
ligation+USER transition whose product is `state_y_adapter_ligated`. The closed
intermediate is never represented, so the graph does not show that the molecule
is transiently circularised — which is the reason the USER step exists.

**Requested change.** Add a `covalently_closed` (or `self_paired`) value
permitting exactly one strand with declared paired regions, gated on Defect 1
since its pairing is necessarily intramolecular. Once available, the merged
transition can split back into ligation → closed dumbbell → USER excision →
Y-shaped duplex, matching the legacy curation and the kit chemistry.

## Scope and priority

Not urgent, but not cosmetic either: it is the difference between recording the
transposase binding site as a duplex and recording it as an unexplained "mixed"
annotation. LIANTI is the first protocol in the campaign with hairpin chemistry
and will not be the last — hairpin adaptors are standard NEBNext, and several
unaudited protocols use them.

Both changes are additive. No existing artifact declares intramolecular pairing
or the new architecture value, so nothing already promoted can be invalidated.
The 27 promoted protocols should revalidate unchanged; that is worth asserting
in a test.

Related: the T1/T3 terminal-orientation relaxation delivered on 2026-08-11
(`_reverse_complement_architecture`, `_terminal_library_error`) came from the
same class of problem — the schema forcing a less faithful representation than
the evidence supports.
