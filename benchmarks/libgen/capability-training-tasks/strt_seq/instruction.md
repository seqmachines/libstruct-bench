# T2/T3 protocol understanding: STRT-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/STRT-seq.pdf` (`sha256:5f6441985a7aa53e5fe6a1f7aa26e753aebea9ed76268d0d133bf40cf4185480`)
- `/workspace/input/STRT-seq_TSO_and_plate_tables.docx` (`sha256:cfc4aee0e3c47bc8d6263a5ede13df42817721ae70be6224fe6cc26216c13231`)
- `/workspace/input/STRT-seq_original_paper.pdf` (`sha256:97077af02b8aa6b03f943fed39b80f97670d1871273a7857d427e9b18ea01faa`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `strt_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
