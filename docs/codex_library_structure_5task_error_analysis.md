# Codex Library Structure Five-Task Error Analysis

This document analyzes a five-task Codex Harbor run of the library-structure
benchmark. The run completed all five trials without runtime or output-schema
errors, but several predicted structures differed from the curated ground
truth.

## Summary

- Trials: 5
- Runtime errors: 0
- Valid prediction documents: 5/5
- Mean reward: `0.720174`

The observed errors were sequence-reconstruction differences, not Harbor,
Docker, authentication, or JSON failures. The analysis also found that several
score losses were caused or amplified by a mismatch between the evidence that
agents are allowed to use and the evidence represented in the curated ground
truth.

## Results

| Task | Reward | Edit distance | Predicted libraries | Ground-truth libraries |
|---|---:|---:|---:|---:|
| `s3_atac` | 1.000000 | 0 | 1 | 1 |
| `10x_chromium_3_feature_barcoding` | 0.992207 | 3 | 2 | 2 |
| `sci_rna_seq` | 0.900000 | 19 | 1 | 1 |
| `petri_seq` | 0.642487 | 69 | 1 | 1 |
| `dr_seq` | 0.066176 | 384 | 1 | 3 |

## Input Types

The tasks were not all PDF-only.

| Task | Agent-visible input |
|---|---|
| `10x_chromium_3_feature_barcoding` | `10xChromium3fb.pdf` |
| `dr_seq` | `DR-seq.pdf` |
| `petri_seq` | `PETRI-seq_paper.pdf`, `PETRI-seq_protocol.pdf`, and `PETRI-seq.xlsx` |
| `s3_atac` | `s3-ATAC.pdf` and `s3-ATAC.xlsx` |
| `sci_rna_seq` | `paper.pdf` and `supplementary.pdf` |

Each task downloads its inputs from a pinned Hugging Face dataset revision via
its `environment/fetch_input.py` helper.

## Scoring Behavior

The verifier first tries to match a prediction by `library_id`, then by an
exactly equal `modality`, and finally against any remaining prediction. For
each assigned pair, it computes normalized Levenshtein similarity:

```text
similarity = 1 - edit_distance / max(predicted_length, ground_truth_length)
```

A missing ground-truth library receives zero similarity. The primary reward is
the mean similarity across all ground-truth libraries, including missing
entries, rather than the mean over successfully assigned entries.

The relevant implementation is in
`tests/libstruct_bench/library_structure.py` within each generated task.

## Per-Task Analysis

### 10x Chromium 3' Feature Barcoding

Reward: `0.9922067`

Both final libraries were detected and matched. The total difference was only
three edits.

#### RNA library: extra `VN`

```text
Ground truth: ... TTTTTTTTTTTTTTTTTTTTTTTTTTTTTT AGAT...
Prediction:   ... TTTTTTTTTTTTTTTTTTTTTTTTTTTTTT VN AGAT...
```

Several diagrams in the PDF label the capture segment as `Poly(dT)VN`, so the
agent retained the source-visible `VN`. The appendix's explicit final sample
index PCR product prints a 30-base T run without `VN`, and the ground truth
follows that explicit product.

This is a genuine source-priority ambiguity: the task rules also say that
source-visible anchored oligo-dT suffixes such as `VN` should remain literal.

#### Feature library: one-base variable-block difference

```text
Ground truth: ? x 9, $ x 15, ? x 9
Prediction:   ? x 9, $ x 15, ? x 10
```

The raw PDF prints the feature region as `N(9)-N(15)-N(10)` in both the feature
oligo and final PCR product. The prediction therefore follows the supplied
PDF, while the ground truth uses nine bases for the final variable block. This
appears to be a ground-truth off-by-one error.

### DR-seq

Reward: `0.0661765`

The agent emitted one 35-base source-grounded partial structure:

```text
????????CTCCACACTACCTCAACCATCACTCAC
```

The ground truth contains three complete libraries:

1. `gdna_nextera`, 136 bases;
2. `gdna_truseq`, 124 bases;
3. `rna`, 151 bases.

The reward was consequently:

```text
(0.198529 + 0 + 0) / 3 = 0.066176
```

The supplied DR-seq PDF prints P1, P2, P3, and partial Ad-2 architecture, but
describes the final preparations through external dependencies:

- gDNA uses the NEBNext Ultra DNA Library Prep Kit for Illumina;
- mRNA library preparation is performed "as previously described," referring
  to a prior CEL-seq procedure.

The PDF does not print the complete Nextera, TruSeq, or CEL-seq adapter and
flow-cell sequences found in the ground truth. The agent therefore followed
the source-only policy: it did not retrieve the cited method, consult vendor
material, or fill commercial-kit sequences from memory.

The ground truth, however, records `DR-seq.html` as its source and contains
complete Nextera, TruSeq, and CEL-seq structures. That evidence is not included
in the agent-visible task input. DR-seq's low score is therefore primarily an
evidence-scope mismatch, although the prediction was also too conservative to
represent a semantically complete final library.

There is also an evaluator matching issue. The prediction used modality
`"rna"`, while the ground truth used `"RNA"`. Because modality matching is
case-sensitive, the only prediction was greedily assigned to the first gDNA
entry instead of the RNA entry.

### PETRI-seq

Reward: `0.6424870`

The 69 edits can be summarized as follows:

| Difference | Approximate edits |
|---|---:|
| Missing left P5 fixed sequence | 29 |
| Missing right P7 fixed sequence | 24 |
| Different Read 1/TruSeq fixed segment | 10 |
| Extra RT random hexamer | 6 |
| Total | 69 |

The structures begin differently:

```text
Ground truth:
P5 + @ x 8 + ACACTCTTTCCCTACACGACGCTCTTCCGATCT ...

Prediction:
@ x 8 + AGAATACACGACGCTCTTCCGATCT ...
```

The XLSX input explicitly prints
`AGAATACACGACGCTCTTCCGATCT`, which is the sequence used by the prediction.
The supplied PDF/XLSX inputs do not print the complete P5 and P7 sequences;
they mainly name the i50x/N70x primers and their commercial kits. The agent
therefore omitted unsupported flow-cell flanks.

The ground truth records `PETRI-seq.html` as its source and includes complete
standard Illumina/TruSeq/Nextera fixed sequences. As with DR-seq, those literal
bases are not fully recoverable from the allowed inputs without vendor or
prior knowledge.

The additional six `?` characters are a separate normalization ambiguity. The
agent treated the RT random hexamer as a retained variable structural region,
while the ground truth omits it together with the biological insert. The task
does not explicitly resolve which treatment should take precedence.

### sci-RNA-seq

Reward: `0.9000000`

The only difference was an omitted Nextera Tn5 mosaic-end block:

```text
Ground truth:
... TTT...VN CTGTCTCTTATACACATCT CCGAGCCCACGAGAC ...

Prediction:
... TTT...VN                     CCGAGCCCACGAGAC ...
```

The omitted mosaic-end block is exactly 19 bases, matching the measured
Levenshtein distance of 19.

The supplementary PDF names Nextera TDE1/indexed TDE1 but does not print the
literal mosaic-end sequence. The agent explicitly omitted the unsupported Tn5
junction in accordance with the source-only rule. The ground truth records
`sci-RNA-seq_family.html` as its source and includes the standard mosaic-end
sequence. The 0.1 score loss is therefore predominantly another evidence-scope
mismatch.

### s3-ATAC

Reward: `1.0000000`

The predicted and ground-truth sequences were identical.

The s3-ATAC XLSX input contains an explicit final PCR product in Supplementary
Table 1, while other sheets provide the transposase, adapter-switching, and PCR
oligos. The agent was therefore able to recover every scored element from the
allowed evidence:

- final top-strand order;
- i5, i7, and Tn5 index lengths;
- reverse-complement orientation;
- P5/P7 and adapter fixed bases.

The agent initially selected the wrong orientation, detected the inconsistency
during its self-check, and corrected the output to the P5/Read-1-oriented top
strand before submission.

## Attribution

### Model decisions and unresolved source choices

1. PETRI-seq retained a six-base RT random hexamer that the ground truth omits;
   the task needs an explicit normalization rule to make either treatment
   uniquely required.
2. For 10x RNA, the agent selected the `Poly(dT)VN` schematic label over the
   appendix's explicit final PCR product.
3. DR-seq used an extremely conservative partial structure rather than a
   semantically complete final-library representation.

### Benchmark-definition concerns

1. **Ground-truth and agent evidence scopes differ.** DR-seq, PETRI-seq, and
   sci-RNA-seq ground truth includes curated HTML and standard commercial-kit
   knowledge, while agents are limited to supplied PDF/XLSX files and are
   prohibited from filling sequences from memory or external sources. The
   ground-truth structures may be scientifically correct, but some scored
   literal bases are not recoverable under the task's source-only policy.
2. **The 10x feature ground truth appears off by one.** The supplied PDF says
   `N(10)` for the last variable block; the ground truth scores nine bases.
3. **The 10x oligo-dT rule and gold normalization are not fully aligned.** The
   task says to preserve source-visible `VN`, while the scored ground truth
   removes it.
4. **Modality matching is case-sensitive.** `rna` and `RNA` are not treated as
   equivalent, even though the task instructions use lowercase modality
   examples.
5. **Library assignment is greedy.** When IDs and modalities do not match, the
   evaluator consumes predictions in ground-truth order instead of performing
   normalized global matching.

## Recommendations

1. Make every scored fixed base recoverable from the pinned task inputs, or
   include the curated HTML/kit sequence reference in the agent-visible input.
2. Define evidence priority when a schematic label and an explicit final PCR
   product disagree.
3. Correct the 10x feature block-length inconsistency.
4. Normalize `library_id` and `modality` for case, whitespace, punctuation, and
   common aliases before matching.
5. Use global bipartite matching that maximizes total sequence similarity
   instead of ground-truth-order greedy assignment.
6. For tasks that intentionally omit commercial-kit sequences, allow role
   annotations or unknown fixed regions without literal edit-distance penalties.

## Overall Interpretation

The mean reward of `0.720174` should not be interpreted directly as a 72%
measure of Codex library-reconstruction capability.

- s3-ATAC demonstrates exact reconstruction when complete final-product and
  oligo evidence is present in the supplied XLSX.
- The 10x prediction is nearly exact, and at least one edit likely belongs to
  the ground truth rather than the model.
- A substantial fraction of the sci-RNA-seq, PETRI-seq, and especially DR-seq
  losses comes from a mismatch between the task's source-grounding policy and
  the ground truth's external evidence.

The benchmark-definition and matching issues should be addressed before using
this five-task mean as a clean model-comparison result.
