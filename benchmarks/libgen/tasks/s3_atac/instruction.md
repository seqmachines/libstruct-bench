# T2/T3 protocol understanding: s3-ATAC

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/s3-ATAC.pdf` (`sha256:1234d1e43ed42f74d771a141c3731585b39251f3ffc4614413ab3d0672d164ab`)
- `/workspace/input/s3-ATAC.xlsx` (`sha256:3b59fe6bd20c2bd1d123c830fcd8507112b2df34633519685a61c75a9ec0ae89`)
- `/workspace/input/supplementary_NIHMS1707134-supplement-Supplementary_Information.pdf` (`sha256:dd2af9d0611f13f7710a171abbaaa977a880c80608c98c8d95514089ce454064`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `s3_atac`. Use local IDs consistently; IDs themselves are not
scored. Write the two required files under `/logs/artifacts/` and run the local
validator shown in the rules. Do not search for or reconstruct benchmark ground
truth, legacy curation, audit records, or prior answers.
