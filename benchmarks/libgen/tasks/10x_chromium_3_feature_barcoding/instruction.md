# T2/T3 protocol understanding: 10x Chromium 3' Feature Barcoding

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

- `/workspace/input/10xChromium3fb.pdf` (`sha256:4b03b7d4448836f839e0cbedb8012e402a99b152b6a23a8046fabb0c5381a819`)
- `/workspace/input/CG000149_cell_surface_labeling_protocol.pdf` (`sha256:3b5cab389d029681913c0f84c85614b43cd5a45d63161f4356bf8a6d951f174f`)
- `/workspace/input/CITE-seq_author_protocol.pdf` (`sha256:6e647bd42ded5b516ecc7f8e33752aeb000038c015ba01724f325c046708ba42`)
- `/workspace/input/CITE-seq_foundational_paper.pdf` (`sha256:a99c39250a40ad6990bebc0c36c9a259fe8f85d31a8729deeb3fac52cd3f469a`)
- `/workspace/input/CITE-seq_supplement.pdf` (`sha256:fe052825574f89bc187d9a78ac194a8c125d1cc3617f7b54e3c1377cc63d84c4`)

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `10x_chromium_3_feature_barcoding`. Use local IDs consistently; IDs themselves are not
scored. Write the two required files under `/logs/artifacts/` and run the local
validator shown in the rules. Do not search for or reconstruct benchmark ground
truth, legacy curation, audit records, or prior answers.
