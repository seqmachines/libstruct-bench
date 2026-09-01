# T2/T3 protocol understanding: STRT-seq-C1

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/STRT-seq-C1.pdf` (`sha256:dbc767d4ae895885aab93dba6847795ddc955cabc55fdb89c2305e12782c857a`)
- `/workspace/input/STRT-seq-C1.xlsx` (`sha256:8844d61c3af17102c43663ce3b4c13f46811ec470b67df917a4a4a8488c0cf88`)
- `/workspace/input/STRT-seq-C1_supplement.pdf` (`sha256:5933c3bf658db8390a2d89523009d07b425847f5b209c84109fdec28b215f4b2`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `strt_seq_c1`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
