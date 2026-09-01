# T2/T3 protocol understanding: MALBAC

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/MALBAC.pdf` (`sha256:e1cbe9ad3a920c6e899506d1d64aeaffeb9b8625042e9a9ffefb878d7b1341b4`)
- `/workspace/input/MALBAC_original_paper.pdf` (`sha256:0a15d85fe9ca38a378d9dd2807e9fc57b237b814e6efa2955c381c2b1b5c2110`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `malbac`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
