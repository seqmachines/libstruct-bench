#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \
  --prediction /logs/artifacts/prediction.json \
  --protocol-id "sci_atac_seq" \
  --groundtruth-repo "sequencing/scg-oligo-groundtruth-v1" \
  --groundtruth-path "sci_atac_seq/groundtruth_final_lib_struct.json" \
  --revision "main" \
  --reward-out /logs/verifier/reward.json \
  --audit-out /logs/verifier/audit.json
