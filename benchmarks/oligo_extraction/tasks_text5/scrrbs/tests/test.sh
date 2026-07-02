#!/usr/bin/env bash
set -euo pipefail

mkdir -p /logs/verifier

python /tests/grade.py \
  --prediction /logs/artifacts/prediction.json \
  --groundtruth /tests/groundtruth.json \
  --protocol-id scrrbs \
  --reward-out /logs/verifier/reward.json \
  --matches-out /logs/verifier/audit.json
