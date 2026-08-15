# Libgen error analysis

Libgen separates an output disagreement from its cause. A mismatch is not an
agent error until a reviewer has checked source recoverability, benchmark
validity, and the observable trajectory.

Every generated Harbor verifier writes three independent artifacts:

- `verifier/reward.json` contains only the benchmark metrics;
- `verifier/details.json` contains match-level scoring diagnostics;
- `verifier/error_analysis.json` converts those diagnostics into structured,
  reviewable discrepancy records.

Generating the third artifact is side-effect-free with respect to scoring. If
analysis generation itself fails, the reward and details remain intact and the
analysis artifact records a non-scoring infrastructure issue.

## Source-only scoring scope

Let `O_used` be the T2 oligo families referenced by a T3 transition or
state-segment derivation. Let `O_score` be the subset of `O_used` whose
family-template sequence claims are explicit or derivable from the
agent-visible source bundle. Concrete members collapsed into a recovered
family do not produce duplicate error observations; an unmatched unrelated
family produces one observation listing its collapsed member IDs.

Canonical claims marked `externally_completed`, `ambiguous`, or `unsupported`
remain in ground truth but are neutral in the source-only benchmark. Exact
predictions of those claims are also neutral. The scorer applies the same
support mask to T3 state architecture, strands, pairing/discontinuity claims,
transitions, transition-local oligo content, and typed edges. Thus an agent is
not penalized for omitting information unavailable from its allowed inputs.

## Output observations and adjudication

The deterministic comparison stage records a compact set of observable output
categories:

- `missing_recoverable_information`;
- `unsupported_completion`;
- `strand_or_orientation_error`;
- `molecular_state_or_assembly_error`;
- `operation_error`;
- `workflow_or_topology_error`;
- `representation_or_schema_error`;
- `other` or `unresolved`.

Each observation includes the matched prediction and ground-truth IDs when
available, its soft match score, affected metrics, source-support status,
signals, and separate fields for benchmark validity, attribution, and process
cause. Deterministic output differences begin with unresolved validity and
attribution. A representation-equivalent claim that recomputes as exact is
non-substantive and is flagged only as an `evaluator_defect` candidate.
When an older preserved run's match details differ from deterministic
rescoring of the same frozen prediction and ground truth, the analyzer records
that inconsistency as the same kind of non-substantive candidate and uses the
current canonical comparison for scientific discrepancy records. It never
rewrites the preserved reward or details artifacts.

Infrastructure failures are recorded separately. Each observation contains two
fields that must be adjudicated rather than inferred from score differences:

- `benchmark_validity`: `valid`, `source_scope_mismatch`,
  `ground_truth_defect`, `policy_ambiguity`,
  `evaluator_defect`, or `unresolved`;
- `attribution`: `agent`, `benchmark`, `mixed`, `infrastructure`, or
  `unresolved`.

Automatic records begin as `unresolved`, except that direct Harbor or analysis
infrastructure failures can be attributed to infrastructure. Defect candidates
remain candidates until frozen audit metadata or human adjudication supports a
confirmed label. Optional and neutralized claims do not become substantive
observations.

The top-level `summary` reports substantive discrepancy count, attribution and
category counts, unresolved and infrastructure issue counts, benchmark-defect
candidate counts, trajectory availability, process-event counts, and observed
self-corrections.

## Trajectory review

Process categories require observable tool and action evidence:

- `evidence_not_retrieved`;
- `extraction_failure`;
- `evidence_retrieved_but_misinterpreted`;
- `molecular_or_strand_reasoning_error`;
- `graph_abstraction_error`;
- `output_bookkeeping_error`;
- `unresolved`.

The automatic analyzer recognizes only narrow structured evidence, currently
including an explicit local validation failure followed by a later successful
validation. It records the two trajectory step locators as an observed
self-correction. It does not attach that event as the root cause of scientific
T2/T3 mismatches. All other mismatch-specific process causes remain
`unresolved` until a reviewer cites trajectory evidence. The analyzer never
uses hidden reasoning or guesses a cause from the final prediction alone.

Generated tasks declare `/logs/agent/trajectory.json` as a best-effort Harbor
artifact. Harbor collects it after the agent phase and re-materializes it at
the same path inside the separate verifier container. A missing trajectory does
not fail scoring or analysis; it leaves trajectory availability false and all
process causes unresolved.

## Sixty-trial pilot gate

Harbor retains the complete agent and verifier log directories when no log
filters are set. Libgen additionally copies every available agent trace,
verifier diagnostic, linked prediction, and trial metadata into a preserved
review pack with SHA-256 inventory entries:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.prepare_libgen_error_review \
  --runs-root runs/libgen \
  --experiment-lock runs/libgen/plans/pilot/experiment_lock.json \
  --out analysis/libgen/pilot-error-review
```

Reviewers adjudicate every substantive mismatch in `error_analysis.json` and
perform trajectory review for agent- or mixed-attributed failures. Confirmed
ground-truth or evaluator defects are fixed through their normal reviewed
workflow, after which the generated benchmark tasks are frozen again.

The final gate validates all 60 records, summarizes raw output categories
separately from validity and attribution, records the post-review task digest,
and emits a clearance file:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.validate_libgen_error_review \
  --review-root analysis/libgen/pilot-error-review \
  --tasks benchmarks/libgen/tasks \
  --record-refreeze \
  --recorded-by CURATOR_ID \
  --out analysis/libgen/pilot-review-status.json
```

The full matrix planner refuses to run unless this status reports that all 60
trials are resolved and its frozen task digest matches the current benchmark.
