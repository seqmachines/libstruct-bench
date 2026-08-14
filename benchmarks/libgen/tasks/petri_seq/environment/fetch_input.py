from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'petri_seq/PETRI-seq.xlsx', 'local_path': 'PETRI-seq.xlsx', 'sha256': '10bf95fd2dd0c5428886fac0b9d05eb6b8b22d412beed6ed97d37bfcfdd36707'}, {'path': 'petri_seq/PETRI-seq_paper.pdf', 'local_path': 'PETRI-seq_paper.pdf', 'sha256': '6fca897787f341946baed886faad10f0cd6877bd110ee84f1c5518a46baa6b7c'}, {'path': 'petri_seq/PETRI-seq_protocol.pdf', 'local_path': 'PETRI-seq_protocol.pdf', 'sha256': '8c1b4caaf6b8a300beb7901dacda9656015c5868c8fdb2fd20eaf44823a2bdc1'}, {'path': 'petri_seq/supplementary_NIHMS1587539-supplement-1587539_Supp_Fig1-2-Supp_Tab1-2.pdf', 'local_path': 'supplementary_NIHMS1587539-supplement-1587539_Supp_Fig1-2-Supp_Tab1-2.pdf', 'sha256': 'd0dfa0b2465647483e02e497c071c8e14a1697cdc7ddced8a2690cd8fb39208a'}, {'path': 'petri_seq/supplementary_NIHMS1587539-supplement-1587539_Supp_Tab3-5.xlsx', 'local_path': 'supplementary_NIHMS1587539-supplement-1587539_Supp_Tab3-5.xlsx', 'sha256': 'e48e334700133a320a4c875453fe45fe9cd57bedf649cfd87996586ddfb02dec'}]


def main() -> int:
    root = Path("/workspace/input")
    for source in SOURCES:
        output = root / source["local_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(_url(source["path"]), headers={"User-Agent": "libstruct-bench"})
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != source["sha256"]:
            raise RuntimeError(
                f"source hash mismatch for {source['path']}: expected {source['sha256']}, found {digest}"
            )
        output.write_bytes(data)
        print(output)
    return 0


def _url(path: str) -> str:
    repo = urllib.parse.quote(REPO_ID.strip().removeprefix("datasets/"), safe="/")
    rev = urllib.parse.quote(REVISION, safe="")
    item = urllib.parse.quote(path.lstrip("/"), safe="/")
    return f"https://huggingface.co/datasets/{repo}/resolve/{rev}/{item}"


if __name__ == "__main__":
    raise SystemExit(main())
