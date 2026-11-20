---
name: review-capability
description: Review one frozen LibGen capability-learning protocol section by section, collect native human choices, and record one final hash-bound proposal disposition.
hooks:
  Stop:
    - hooks:
        - type: command
          command: python3
          args:
            - "${CLAUDE_PROJECT_DIR}/.claude/hooks/require_audit_question.py"
---

# Review one capability-learning protocol

## Usage

Run from the `libstruct-bench` repository root:

```text
/review-capability <human_experiment_root> <reviewer_id>
```

This skill is the interactive human-review controller. The frozen comparison
and immutable proposal already exist. Codex remains the proposal author;
Claude Code must not regenerate, silently edit, approve, or apply that
proposal. Deterministic Python validates the pinned hashes, renders each
section, and records only the human's final disposition.

## Non-negotiable boundaries

- Read `CLAUDE.md`, `docs/audit/adjudication-policy.md`, and
  `docs/improvement/human-guided-loop.md` before reviewing.
- Use only the specified human experiment. Never open validation or final-test
  ground truth, scores, solved records, or protocol-specific diagnostics.
- Never modify canonical ground truth, a frozen prediction, comparison JSON,
  proposal JSON, checkpoint, pack, or source artifact.
- Section choices are review feedback, not capability authorization. Only the
  final proposal disposition is recorded, and it does not apply pack bytes.
- Never infer approval from "continue", silence, a question, or a navigation
  request.
- Do not replace the native selector with a numbered list or terminal prompt.

## Native interactive contract

Use Claude Code's `AskUserQuestion` tool for every section gate and for the
final proposal gate. Ask exactly one single-select question at a time. The
tool's free-text option handles questions, corrections, and navigation.

For every section, use this exact sequence in one controller turn:

1. Run the deterministic section renderer and reproduce its complete output
   in normal console text. Do not shorten, summarize, or omit entities.
2. Append the literal hidden marker
   `<!-- capability-review-question-required -->`.
3. Immediately call `AskUserQuestion` with these four choices and concise
   consequences:
   - `Agree with section` — the displayed comparison and linked proposal
     treatment are accurate; continue.
   - `Needs revision` — preserve the human's bounded correction and continue
     reviewing; the immutable proposal is not edited in place.
   - `Unresolved` — this section cannot currently support capability learning.
   - `Stop review` — record no final proposal decision and exit safely.

After `AskUserQuestion` returns:

- Keep the explicit section choice and any human explanation in a session
  ledger.
- If the human asks a question, answer it and call `AskUserQuestion` again for
  the same unresolved section.
- If the human selects `Needs revision` without stating the correction, ask
  the same gate again and use the free-text option to obtain a bounded
  instruction before continuing.
- Do not record a protocol-level decision after an individual section.
- Do not display the next section until the current selector has returned.

The skill-scoped Stop hook blocks Claude from ending after a marked section
without opening `AskUserQuestion`.

## Deterministic section order

Review exactly these six sections, one at a time:

1. `t2`
2. `t3-workflow-boundary`
3. `t3-states`
4. `t3-transitions`
5. `t3-typed-edges`
6. `proposal`

Render a section with:

```bash
PYTHONPATH=src python3 -m libstruct_bench.cli.libgen_capability_improvement \
  human-review-show-section \
  --experiment-root "<human_experiment_root>" \
  --section "<section>"
```

The renderer must show:

- T2: both T2 metrics and every ground-truth and agent oligo, including
  optional, neutral, unmatched, and unscored records. Render every oligo as a
  complete side-by-side field card, including exact matches and optional
  ground-truth-only records. If an invalid linked prediction receives a
  display-only diagnostic family assignment, reproduce every paired card and
  its diagnostic banner verbatim. Never replace the cards with a compressed
  roster or describe the diagnostic similarities as scored metrics.
- Workflow boundary: every ground-truth and agent workflow, initial state,
  terminal output, and state/transition count.
- States: State F1 and every ground-truth and agent molecular state with full
  architecture, strands, segments, pairing, discontinuities, and properties.
- Transitions: Molecular-transition F1 and every ground-truth and agent
  transition with operation, substrates, oligos, products, dispositions,
  reagents, and operation detail.
- Typed edges: Typed-edge F1, every raw edge on both sides, and the aligned
  matched, missing, and extra edge sets.
- Proposal: all metrics, all root findings, eligibility classifications,
  diagnoses, generalized patterns, remedies, applicability, exclusions, and
  trajectory evidence.

If deterministic rendering fails, stop without a scientific choice and report
the exact validator error. Do not bypass a hash or schema check.

## Final proposal gate

After the proposal section is visible, show the accumulated section-choice
ledger, append `<!-- capability-review-question-required -->`, and immediately
call `AskUserQuestion` with:

- `Approve proposal` — accept the exact proposal classifications and admit
  only its eligible findings to later batch synthesis.
- `Request one revision` — record one bounded revision instruction; nothing is
  admitted yet.
- `Reject proposal` — admit none of the proposal.
- `Unresolved` — preserve uncertainty and admit none of the proposal.

If `Request one revision` is chosen, the instruction must be the accumulated
human-authored bounded corrections. If none exists, reopen this gate and ask
the human to enter one through the free-text option. Construct the rationale
from the explicit section choices and human explanations; do not invent a
scientific reason.

Record the final choice through the deterministic CLI. Use `comment` for a
revision request because the CLI maps it to `revision_requested`:

```bash
PYTHONPATH=src python3 -m libstruct_bench.cli.libgen_capability_improvement \
  human-review-record \
  --experiment-root "<human_experiment_root>" \
  --reviewer-id "<reviewer_id>" \
  --disposition "<approve|comment|reject|unresolved>" \
  --rationale "<human-grounded section ledger summary>" \
  --started-at "<UTC start timestamp>" \
  --completed-at "<UTC completion timestamp>" \
  [--revision-instruction "<bounded human instruction>"]
```

Do not run batch synthesis, apply a capability proposal, freeze a checkpoint,
or start the next protocol automatically. Report the recorded disposition and
the deterministic next action, then stop.
