# T2/T3 protocol understanding: 10x Chromium Single Cell ATAC v2

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/10xChromium_scATACv2.pdf` (`sha256:bd4f79e69dc1768e0eb1ba0dc202d87a0998b64a4e51eae5665ea8704b002579`)
- `/workspace/input/10x_scATAC_barcode_table.xlsx` (`sha256:68b9e997ef35d9cd1a56d9b8f2390802a81ecdede3865125eaf687370d7a2289`)
- `/workspace/input/10x_scATAC_foundational_paper.pdf` (`sha256:d8d1486de10fdecbc720a267bb4fb54e3818677380d1873491333f3311c5052f`)
- `/workspace/input/10x_scATAC_foundational_supplement.pdf` (`sha256:8ecc3a81de7feda3cf1917aa9bb802dd89de400b46daf1e98e651c40ed7e5823`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `10x_chromium_single_cell_atac_v2`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
