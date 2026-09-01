# T2/T3 protocol understanding: CRISPR-sciATAC

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/CRISPR-sciATAC.pdf` (`sha256:5a28f795fbed9c02fbf0d99ae67af16e130db36be47dd6dd099ccc45d2b5d1d4`)
- `/workspace/input/CRISPR-sciATAC.xlsx` (`sha256:75208b67dd4ec6fa3bd274e738e6a98b272ccbb8d8e602d7c2b64d6d07502f53`)
- `/workspace/input/CRISPR-sciATAC_supplement.pdf` (`sha256:9712e93e49d710e2c213fb00e71fd26b8ba07ffaf098aacfc2019ee411119a14`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `crispr_sciatac`. Use local IDs consistently; IDs themselves are not
scored. Represent barcode/index panels as one family template rather than
enumerating concrete members, and reference that family-level T2 ID from T3.
Write the two required files under `/logs/artifacts/` and run the local validator
shown in the rules. Do not search for or reconstruct benchmark ground truth,
legacy curation, audit records, or prior answers.
