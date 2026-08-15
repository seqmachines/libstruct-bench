# T2/T3 protocol understanding: Microwell-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/Microwell-seq.pdf` (`sha256:64b440bc37f6cb00700a6ada5258d3fff0a64ea790e04478d895105a9714a38e`)
- `/workspace/input/Microwell-seq_supp1.xlsx` (`sha256:df348ae5348eb00b30211b7cafa3301db6604bbda8db5a2490360c81e5cc1d88`)
- `/workspace/input/Microwell-seq_supp2.xlsx` (`sha256:185aa52c602b216cb015ed1056bd52a7e877d00dacbbdce0ca8d904b5371b5b7`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `microwell_seq`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
