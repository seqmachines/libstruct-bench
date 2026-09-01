# T2/T3 protocol understanding: LIANTI

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/LIANTI.pdf` (`sha256:910a738e286f9c83a508b00084b67ecaa3ad76f655a65f8d8a07afd4ff5d4648`)
- `/workspace/input/LIANTI_original_paper.pdf` (`sha256:b4956bc9469de7b1d67ce79ce9b3312ad2f00451574cfaa515d1ac17e902d6bb`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `lianti`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
