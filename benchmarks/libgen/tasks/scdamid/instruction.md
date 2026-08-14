# T2/T3 protocol understanding: scDamID

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/scDamID_indexing_primers_table.doc` (`sha256:66e170fbf743ccffd0540b43c991a97e61b512995ebea7585c7b7036cf7cb1b3`)
- `/workspace/input/scDamID_paper.pdf` (`sha256:15207de0a14eaad90d99b0a9fea5aad226db62b9ed702bae0cc16abbf7707201`)
- `/workspace/input/scDamID_supp.pdf` (`sha256:80a616cc1df0614cd32ac916cf6d55a473ccde9ed3fb6829d04b85c4e13c937c`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `scdamid`. Use local IDs consistently; IDs themselves are not
scored. Write the two required files under `/logs/artifacts/` and run the local
validator shown in the rules. Do not search for or reconstruct benchmark ground
truth, legacy curation, audit records, or prior answers.
