# T2/T3 protocol understanding: SHARE-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/SHARE-seq.pdf` (`sha256:be7253756f1f3567daa7270433b1ce00306b42da7560978682bc5ea4349b5083`)
- `/workspace/input/SHARE-seq.xlsx` (`sha256:f3b0ce8e4612f651c26292a4cf0ce1fbfbde5931eee25af05d2b8751b3a4ff71`)
- `/workspace/input/SHARE-seq_workflow_supplement.pdf` (`sha256:15fac55d0920a31453bad8354d511581d5f12dff2c7992efbc93baa1b815afbe`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `share_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
