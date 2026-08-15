#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \
  --t2-prediction /logs/artifacts/t2_prediction.json \
  --t3-prediction /logs/artifacts/t3_prediction.json \
  --protocol-id "scrrbs" \
  --groundtruth-repo "sequencing/scg-oligo-groundtruth-v1" \
  --groundtruth-revision "ecd12f41611ed64e3bff53d715b111e0f0ee9a10" \
  --groundtruth-prefix "scrrbs" \
  --t1-sha256 "3878b1e370dcf69d6c0c188ec7650ebcd50ff0bcbad410e7340e844946165d32" \
  --t2-sha256 "5b0b2fd507604b8bba2ff58951a2f4b468d51b83b68c3271acc5da4feca71114" \
  --t3-sha256 "4d9a58252a6431a9189197c05adb700960c22c000ccaa57008e0e9d0308a5b7f" \
  --schema-root /tests/schemas \
  --reward-out /logs/verifier/reward.json \
  --details-out /logs/verifier/details.json \
  --error-analysis-out /logs/verifier/error_analysis.json \
  --trajectory /logs/agent/trajectory.json \
  --error-out /logs/verifier/error.json
