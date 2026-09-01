# T2/T3 protocol understanding: Tang 2009

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/Tang_2009_original_paper.pdf` (`sha256:9baea229201a13693ae7b645bf0ba961a5604a5c68b56da482968e0d8c210adf`)
- `/workspace/input/Tang_2009_supplement.pdf` (`sha256:9d2c33752380ca4c9e9df44191f5a69a70bd93612c50e9632f2b86ce66832cf8`)
- `/workspace/input/tang2009.pdf` (`sha256:8486e4b04b963b40c720a90973facd6d099e6506aafe269d0950732b0501f861`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `tang_2009`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
