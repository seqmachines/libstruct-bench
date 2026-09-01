# T2/T3 protocol understanding: sci-fi ATAC-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/scifi-ATAC-seq.pdf` (`sha256:6306fab0a82f1653a0ed060c724d0451f157f8112e15b94b018c194ac32dd4cf`)
- `/workspace/input/scifi-ATAC-seq_original_paper.pdf` (`sha256:8da266e776285f5a2eb8517c32231fbc74b26e8e70bf08f12816edef85771bd1`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `scifi_atac_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
