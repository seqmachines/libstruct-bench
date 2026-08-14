# T2/T3 protocol understanding: PIP-seq v4

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/PIP-seq_oligo_table.xlsx` (`sha256:7a338bc093b8ba415407c4bdad5cd159f5c1bdc384dc4982934a6fcec4456e0a`)
- `/workspace/input/PIP-seq_original_paper.pdf` (`sha256:3de78ffa0bbeece92a70bd6555a8a921f22b8ea772e1d8bac3559952108e4a12`)
- `/workspace/input/PIP-seqv4.pdf` (`sha256:9299bdb6ea132d575413e55d0203a094ccfe7cb7942a2d85136bae1994874bb6`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `pip_seq_v4`. Use local IDs consistently; IDs themselves are not
scored. Write the two required files under `/logs/artifacts/` and run the local
validator shown in the rules. Do not search for or reconstruct benchmark ground
truth, legacy curation, audit records, or prior answers.
