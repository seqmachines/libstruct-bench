# T2/T3 protocol understanding: ddSEQ Single-Cell 3' RNA-Seq Kit

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/ddSEQ_3pRNA.pdf` (`sha256:35a78f75c3baf198214338b3ede9741e186ae45bcf792d756b976a0e88512c0a`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `ddseq_single_cell_3_rna_seq_kit`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
