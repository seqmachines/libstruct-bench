# T2/T3 protocol understanding: Drop-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/Drop-seq_author_protocol_v1.1.pdf` (`sha256:0e662a35307956437f2414ffbbe1681f6fcd9f0c6403e4a7f148215e417b1b93`)
- `/workspace/input/drop-seq_paper.pdf` (`sha256:84179dc8bfce57a3195ebe5d53ed1257883d9e18111cef6a2dcd90dfadae92b7`)
- `/workspace/input/drop-seq_supp.pdf` (`sha256:7b63216599c518dccd63c484cb6bc448fa181e8d9ddbdf0f2ac788b12296442d`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `drop_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
