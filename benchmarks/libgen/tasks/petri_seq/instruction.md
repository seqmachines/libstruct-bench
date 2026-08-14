# T2/T3 protocol understanding: PETRI-seq

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/PETRI-seq.xlsx` (`sha256:10bf95fd2dd0c5428886fac0b9d05eb6b8b22d412beed6ed97d37bfcfdd36707`)
- `/workspace/input/PETRI-seq_paper.pdf` (`sha256:6fca897787f341946baed886faad10f0cd6877bd110ee84f1c5518a46baa6b7c`)
- `/workspace/input/PETRI-seq_protocol.pdf` (`sha256:8c1b4caaf6b8a300beb7901dacda9656015c5868c8fdb2fd20eaf44823a2bdc1`)
- `/workspace/input/supplementary_NIHMS1587539-supplement-1587539_Supp_Fig1-2-Supp_Tab1-2.pdf` (`sha256:d0dfa0b2465647483e02e497c071c8e14a1697cdc7ddced8a2690cd8fb39208a`)
- `/workspace/input/supplementary_NIHMS1587539-supplement-1587539_Supp_Tab3-5.xlsx` (`sha256:e48e334700133a320a4c875453fe45fe9cd57bedf649cfd87996586ddfb02dec`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `petri_seq`. Use local IDs consistently; IDs themselves are not
scored. Write the two required files under `/logs/artifacts/` and run the local
validator shown in the rules. Do not search for or reconstruct benchmark ground
truth, legacy curation, audit records, or prior answers.
