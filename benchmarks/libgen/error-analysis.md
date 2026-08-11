# Libgen error analysis

Libgen separates an output disagreement from its cause. A mismatch is not an
agent error until a reviewer has checked source recoverability, benchmark
validity, and the observable trajectory.

## Source-only scoring scope

Let `O_used` be the T2 oligos referenced by a T3 transition or state-segment
derivation. Let `O_score` be the subset of `O_used` whose sequence claims are
explicit or derivable from the agent-visible source bundle.

Canonical claims marked `externally_completed`, `ambiguous`, or `unsupported`
remain in ground truth but are neutral in the source-only benchmark. Exact
predictions of those claims are also neutral. The scorer applies the same
support mask to T3 state architecture, strands, pairing/discontinuity claims,
transitions, transition-local oligo content, and typed edges. Thus an agent is
not penalized for omitting information unavailable from its allowed inputs.

## Output observations and adjudication

The deterministic comparison stage records observable output categories:

- `missing_recoverable_information`;
- `unsupported_completion`;
- `wrong_target_or_modality`;
- `strand_or_orientation_error`;
- `molecular_assembly_or_topology_error`;
- `representation_or_schema_error`.

Infrastructure failures are recorded separately. Each observation contains two
fields that must be adjudicated rather than inferred from score differences:

- `benchmark_validity`: `valid`, `source_scope_mismatch`,
  `ground_truth_defect`, `policy_ambiguity`, `source_conflict`,
  `evaluator_defect`, or `unresolved`;
- `attribution`: `agent`, `benchmark`, `mixed`, `infrastructure`, or
  `unresolved`.

Automatic records begin as `unresolved`, except that direct Harbor or verifier
failures can be attributed to infrastructure. Ground-truth and evaluator
defects therefore remain visible and are never silently counted as raw model
errors.

## Trajectory review

Process categories are assigned only by a reviewer using observable tool and
action traces:

- evidence not retrieved;
- extraction failure;
- evidence retrieved but misinterpreted;
- molecular or strand reasoning error;
- graph abstraction error;
- context or time limitation;
- output bookkeeping error.

The reviewer cites trace locations and separately records whether successful
self-correction was observed. The deterministic scorer never guesses these
process causes from the final output.

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
