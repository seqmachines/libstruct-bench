# Protocol-neutral synthetic cases

The fixtures use invented identifiers and short synthetic sequences. They test
internal graph invariants only and are not examples of any real protocol.

- `suite.json` is authoritative. Every case has a stable `case_id`, polarity,
  exact expected exit, and exact finding-code counts.
- `valid/` exercises a complete work record and all registered controls.
- `boundary/` exercises allowed boundary behavior without findings.
- `invalid/` contains protocol-neutral violations with deterministic findings.

The suite runner discovers controls through `tools/control_index.json` and
reports every executed case ID so accepted capability units can cite the exact
fixtures that were run.
