# T2/T3 protocol understanding: SMART-seq2

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/SMART-seq2.pdf` (`sha256:bb8ee0c7f0b815b53b91a0cc26c206eb2c5c4d194370ada1e28a00e2dd49dd97`)
- `/workspace/input/SMART-seq2_TSO_table.xlsx` (`sha256:335e18f7419ad9b9662d9874e1dc1d527bed975f8289c090d9616ddf300c55b6`)
- `/workspace/input/SMART-seq2_detailed_protocol.pdf` (`sha256:f06e80ec0dfba042cc950696cbe18e1396662bff714931e70d115dbce55ba023`)
- `/workspace/input/SMART-seq2_protocol_variants_table.xlsx` (`sha256:2d72b73ba646b5140393e65c689d107ccf53c420a9a3a9229b9a96f6772aceff`)
- `/workspace/input/SMART-seq2_supplement.pdf` (`sha256:24c07d2451f41ec512db3f11cdd4ba6ba66de58511a68b1c22788d21b1722aee`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `smart_seq2`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
