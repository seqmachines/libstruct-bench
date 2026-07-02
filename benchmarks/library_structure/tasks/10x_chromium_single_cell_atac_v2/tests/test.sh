#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \
  --prediction /logs/artifacts/prediction.json \
  --protocol-id "10x_chromium_single_cell_atac_v2" \
  --groundtruth-repo "sequencing/scg-oligo-groundtruth-v1" \
  --groundtruth-path "10x_chromium_single_cell_atac_v2/groundtruth_final_lib_struct.json" \
  --revision "main" \
  --reward-out /logs/verifier/reward.json \
  --audit-out /logs/verifier/audit.json
