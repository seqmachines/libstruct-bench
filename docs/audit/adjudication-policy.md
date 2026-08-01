# Human Adjudication and Release Policy

## Immutable proposals

Store each validated audit proposal under a unique audit ID and compute its
SHA-256 before review. A reviewer decision references that proposal hash and
the exact baseline artifact hash. Changing a proposal creates a new audit ID;
it does not rewrite review history.

## Human decisions

Each reported issue receives one disposition:

- `accept`: approve the issue's exact proposed patch;
- `reject`: retain the current value;
- `modify`: approve a reviewer-supplied replacement value and exact patch;
- `unresolved`: preserve the ambiguity and determine release eligibility;
- `exclude`: exclude the affected field, task, or protocol from scoring.

Decisions require a stable reviewer ID, review start/completion timestamps, and
a rationale. An overall decision may be `confirmed`, `accepted`, `rejected`,
`partially_accepted`, `unresolved`, or `excluded`, but deterministic
application follows issue-level decisions.

## Applying corrections

The apply step must:

1. validate the proposal and decision schemas;
2. recompute and match the proposal and existing baseline SHA-256 values, or
   record that a human-approved new artifact had no baseline;
3. select only proposal patches marked `accept` or reviewer-supplied patches
   marked `modify`;
4. reject overlapping, stale, or invalid JSON Pointer operations;
5. write a new candidate artifact without changing an immutable baseline;
6. emit a machine-readable application log;
7. generate or update a regression fixture for every accepted correction.

Rerunning the same accepted decisions against the same baseline, including an
explicitly absent baseline for a new artifact, must produce byte-identical
candidate artifacts.

## Release gates

A frozen release requires:

- a validated manifest entry for every in-scope protocol;
- verified hashes for every input and output artifact;
- a recorded status for every protocol;
- no unreviewed proposed change;
- explicit inclusion, exclusion, or deferral of unresolved protocols;
- passing schema, leakage, deterministic-application, evaluator, and
  regression tests;
- every released T1/T2/T3 artifact hash matching its latest deterministic
  application candidate or unchanged pinned baseline;
- a release manifest pinning policy versions, schema versions, source
  revisions, and the producing code commit.

An unresolved protocol must not be forced into a single truth value. It may be
excluded from scoring or released with a documented limitation after human
review.
