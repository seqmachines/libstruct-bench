# Native Codex adapter

Read `PLAYBOOK.md` before modeling the target and follow its required working
order. Keep the structured work record outside `/logs/artifacts/`; write only
the two task-required predictions there.

Use the pack's declared interfaces rather than selecting individual checkers.
From `/workspace`, when the pack is mounted as `capability_pack`, run:

```bash
python3 capability_pack/tools/compile_work_record.py \
  --work-record /tmp/libgen-work-record.json \
  --t2-out /logs/artifacts/t2_prediction.json \
  --t3-out /logs/artifacts/t3_prediction.json

python3 capability_pack/tools/audit_predictions.py \
  --work-record /tmp/libgen-work-record.json \
  --t2 /logs/artifacts/t2_prediction.json \
  --t3 /logs/artifacts/t3_prediction.json
```

Read `tools/control_index.json` for the exact machine contract. Exit 0 means
the deterministic audit passed, exit 1 means its JSON report contains findings,
and exit 2 means input or execution failure. Resolve findings only from the
target's own sources. This adapter is frozen after S0 initialization so learned
changes remain harness-neutral.
