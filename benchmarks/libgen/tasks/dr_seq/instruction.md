# T2/T3 protocol understanding: DR-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/DR-seq.pdf` (`sha256:c6cbc1210a713990c7b6d421ebd768a49ec7a9e7469a6bc9b99248d26bc817b8`)
- `/workspace/input/supplementary_NIHMS61543-supplement-1.pdf` (`sha256:2407cf96e68c81d330dbae2a95e9b9acbfb3a0a8ba5a463f2633815a229afe01`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `dr_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
