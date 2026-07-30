# Human Adjudication and Release Policy

## Immutable proposals

Store each validated audit proposal under a unique audit ID and compute its
SHA-256 before review. A reviewer decision references that proposal hash and
the exact baseline artifact hash. Changing a proposal creates a new audit ID;
it does not rewrite review history.

## Human decisions

Each reported issue receives one disposition:

- `accepted`: approve the issue's exact proposed patch;
- `rejected`: retain the current value;
- `needs_more_evidence`: require a new versioned source or further review;
- `deferred`: acknowledge the issue without changing the current release.

Decisions require a stable reviewer ID, timestamp, and rationale. An overall
decision may be `accepted`, `rejected`, `partially_accepted`,
`needs_more_evidence`, or `deferred`, but deterministic application follows
the issue-level decisions.

## Applying corrections

The apply step must:

1. validate the proposal and decision schemas;
2. recompute and match the proposal and baseline SHA-256 values;
3. select only issue patches explicitly marked `accepted`;
4. reject overlapping, stale, or invalid JSON Pointer operations;
5. write a new candidate artifact without changing the immutable baseline;
6. emit a machine-readable application log;
7. generate or update a regression fixture for every accepted correction.

Rerunning the same accepted decisions against the same baseline must produce
byte-identical candidate artifacts.

## Release gates

A frozen release requires:

- a validated manifest entry for every in-scope protocol;
- verified hashes for every input and output artifact;
- a recorded status for every protocol;
- no unreviewed proposed change;
- explicit inclusion, exclusion, or deferral of unresolved protocols;
- passing schema, leakage, deterministic-application, evaluator, and
  regression tests;
- a release manifest pinning policy versions, schema versions, source
  revisions, and the producing code commit.

An unresolved protocol must not be forced into a single truth value. It may be
excluded from scoring or released with a documented limitation after human
review.
