# T2/T3 protocol understanding: sci-ATAC-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/sci-ATAC-seq.xlsx` (`sha256:954913c9ac149978d5221b375d40689a0ac9ec3e8c60f7165f2fa7d1de3f4ec4`)
- `/workspace/input/sci-ATAC-seq_original_paper_corrected.pdf` (`sha256:e633a06145b85d90feaef58c2790bfdc5b6711ef98c5fb15aed45d88a3ad1d1f`)
- `/workspace/input/sci-ATAC-seq_paper.pdf` (`sha256:bcf526797a5efa310c8b5edab0c0b6c84650b36f0cd4c7aba23a82fe67d898e3`)
- `/workspace/input/sci-ATAC-seq_supp.pdf` (`sha256:1c6fb4ba5a9558de4490cb0c6d7a948a43fffe9c33e87a650378dd632205d1b0`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `sci_atac_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
