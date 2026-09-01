# T2/T3 protocol understanding: STRT-seq-2i

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/STRT-seq-2i.pdf` (`sha256:9b90764c5ff4f40598c0f76efffdaded84f0a76c56786239228398ddf4e55e7b`)
- `/workspace/input/STRT-seq-2i_supplement.pdf` (`sha256:4afeb8e4c15b712c20201125b709ae71ff92344682b91ac32cd18eb7ef503b05`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `strt_seq_2i`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
