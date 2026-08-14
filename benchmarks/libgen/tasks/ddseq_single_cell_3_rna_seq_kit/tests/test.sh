#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \
  --t2-prediction /logs/artifacts/t2_prediction.json \
  --t3-prediction /logs/artifacts/t3_prediction.json \
  --protocol-id "ddseq_single_cell_3_rna_seq_kit" \
  --groundtruth-repo "sequencing/scg-oligo-groundtruth-v1" \
  --groundtruth-revision "ecd12f41611ed64e3bff53d715b111e0f0ee9a10" \
  --groundtruth-prefix "ddseq_single_cell_3_rna_seq_kit" \
  --t1-sha256 "4f93de000b9d2fe1414dc4c85fe3947be2b1b87c371c0067bada4784cf33c1af" \
  --t2-sha256 "8d2e20b111d6fd5fc1b7d204434b35753dfbaa78501e7080fba5e17203688b60" \
  --t3-sha256 "b25223654ec3601d6b4d01c221c5639d6bf9f30bbc4dcc5188fca04dc1346e85" \
  --schema-root /tests/schemas \
  --reward-out /logs/verifier/reward.json \
  --details-out /logs/verifier/details.json \
  --error-out /logs/verifier/error.json
