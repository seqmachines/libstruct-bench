from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': '10x_chromium_3_feature_barcoding/10xChromium3fb.pdf', 'local_path': '10xChromium3fb.pdf', 'sha256': '4b03b7d4448836f839e0cbedb8012e402a99b152b6a23a8046fabb0c5381a819'}, {'path': '10x_chromium_3_feature_barcoding/CG000149_cell_surface_labeling_protocol.pdf', 'local_path': 'CG000149_cell_surface_labeling_protocol.pdf', 'sha256': '3b5cab389d029681913c0f84c85614b43cd5a45d63161f4356bf8a6d951f174f'}, {'path': '10x_chromium_3_feature_barcoding/CITE-seq_author_protocol.pdf', 'local_path': 'CITE-seq_author_protocol.pdf', 'sha256': '6e647bd42ded5b516ecc7f8e33752aeb000038c015ba01724f325c046708ba42'}, {'path': '10x_chromium_3_feature_barcoding/CITE-seq_foundational_paper.pdf', 'local_path': 'CITE-seq_foundational_paper.pdf', 'sha256': 'a99c39250a40ad6990bebc0c36c9a259fe8f85d31a8729deeb3fac52cd3f469a'}, {'path': '10x_chromium_3_feature_barcoding/CITE-seq_supplement.pdf', 'local_path': 'CITE-seq_supplement.pdf', 'sha256': 'fe052825574f89bc187d9a78ac194a8c125d1cc3617f7b54e3c1377cc63d84c4'}]


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
