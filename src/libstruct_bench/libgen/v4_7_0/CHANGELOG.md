# Molecular workflow verifier 4.7.0

The installed scorer, Harbor verifier, and rescoring command use the paper implementation in
`libstruct_bench.libgen.v4_7_0`. Its SHA-256 is recorded in the paper's frozen analysis manifest.

## Family comparison and construction

- In family-aware nucleotide edit distance, a predicted placeholder cannot
  substitute for a fixed ground-truth A/C/G/T/U. The reverse direction remains
  allowed: concrete predicted panel members can satisfy a ground-truth family.
- N and V remain ambiguity tokens, distinct from specified fixed bases.
  Existing family-placeholder compatibility with N/V is retained. This is a
  bounded fix, not a new interpretation of all IUPAC sets: a family placeholder
  is still compatible with V in a family-aware comparison even though V excludes
  T. Concrete-versus-degenerate substitutions and non-family edit distance retain
  the historical behavior. The full IUPAC information-order problem is deferred.
- An explicitly supplied assembled sequence is retained. Component names,
  roles, and redundant placeholder annotations cannot replace its bases.
  Component-only molecules likewise prefer an explicit sequence, including
  mixed fixed/degenerate spans, over an inferred or annotated placeholder.
- Sequence-free components can still supply explicit placeholders, or derive
  role/length placeholders using the pre-existing rule. Any such predicted
  placeholder is subject to the fixed-base restriction above.
- Correct concrete members still collapse against an actual compatible target
  family. Deduplication retains original member records for scoring. Existing
  flat-panel inference, metadata grouping, masks, and neutral functional
  correspondence are unchanged.
- Transition-local oligo matching uses the same corrected family code.

## Numerical exactness

- Optional T2 scientific neutralization and exact required-family recall use
  **all enabled scientific components equal to their exact maximum**, for at
  least one family member. Empty support does not establish exactness.
- Neutral T3 state and transition matches use the corresponding unmasked
  scientific component vector, including when testing unmatched predictions.
  Their weighted sum is no longer the exactness predicate. The actual weighted
  scientific score and assignment scores are still computed as before.
- Neutral strand removal tests molecule type, orientation, segment cardinality,
  structural roles, and exact per-segment sequence compatibility directly.
- No epsilon tolerance or rounding-to-one is introduced. The nearest float
  below one is imperfect. Weight changes cannot turn an all-correct component
  vector into a non-exact vector; a zero scientific weight cannot excuse an
  imperfect enabled component. A changed alignment can still legitimately
  change which entities qualify for neutralization.
- Primitive normalized edit/count similarities still use their exact maximum
  to identify zero edits/full overlap. These are distinct from a floating-point
  weighted-sum equality test.

## Deliberately unchanged

All three scientific weight vectors, assignment thresholds and adjustments,
support masks, source-unrecoverable functional reconciliation, typed-edge
policy, combined reward (`0.3 T2 + 0.7 transition F1`), and zero-required-family
behavior are unchanged. Task 1 is unchanged. There is no graph contraction or
granularity correction. Existing predictions and canonical references are
read-only. Historical C30 selection is retained, not reoptimized.

Regression tests: `tests/test_libgen_v4_7_0.py`. Frozen re-evaluation and repeated
W4 sensitivity: `analysis/paper_results/verifier_corrected_v4_7_0/`.
