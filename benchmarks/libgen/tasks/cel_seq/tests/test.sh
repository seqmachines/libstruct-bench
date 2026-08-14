#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \
  --t2-prediction /logs/artifacts/t2_prediction.json \
  --t3-prediction /logs/artifacts/t3_prediction.json \
  --protocol-id "cel_seq" \
  --groundtruth-repo "sequencing/scg-oligo-groundtruth-v1" \
  --groundtruth-revision "ecd12f41611ed64e3bff53d715b111e0f0ee9a10" \
  --groundtruth-prefix "cel_seq" \
  --t1-sha256 "08fd6f2f85dad5fbe1f2457323dc9676d47236a812291afb0c3f76c5289f43de" \
  --t2-sha256 "c552c4cc4748e6b08bc04756cb63f5e701b352776680738b0c5f862c27b32675" \
  --t3-sha256 "1a2623ede2baea3a9e477418abba2bf14301c5a016356a02775eb71a199e7c66" \
  --schema-root /tests/schemas \
  --reward-out /logs/verifier/reward.json \
  --details-out /logs/verifier/details.json \
  --error-out /logs/verifier/error.json
