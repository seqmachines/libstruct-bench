# T2/T3 protocol understanding: Plate scATAC-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/plate_scATAC-seq.pdf` (`sha256:f5773809db99b63663992e58244bd5650d9dda436231f25c68bcc95d31432042`)
- `/workspace/input/plate_scATAC-seq_original_paper.pdf` (`sha256:713fe064fc5d4577a2f555099a0918f4b819c2ba83e70ebe8f02f8212535bde0`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `plate_scatac_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
