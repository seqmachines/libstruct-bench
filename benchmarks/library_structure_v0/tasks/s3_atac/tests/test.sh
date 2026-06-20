#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \
  --prediction /logs/artifacts/prediction.json \
  --protocol-id "s3_atac" \
  --groundtruth-repo "sequencing/scg-oligo-groundtruth-v1" \
  --groundtruth-path "s3_atac/groundtruth_final_lib_struct.json" \
  --revision "main" \
  --reward-out /logs/verifier/reward.json \
  --audit-out /logs/verifier/audit.json
