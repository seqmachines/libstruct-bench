from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': '10x_chromium_single_cell_atac_v2/10xChromium_scATACv2.pdf', 'local_path': '10xChromium_scATACv2.pdf', 'sha256': 'bd4f79e69dc1768e0eb1ba0dc202d87a0998b64a4e51eae5665ea8704b002579'}, {'path': '10x_chromium_single_cell_atac_v2/10x_scATAC_barcode_table.xlsx', 'local_path': '10x_scATAC_barcode_table.xlsx', 'sha256': '68b9e997ef35d9cd1a56d9b8f2390802a81ecdede3865125eaf687370d7a2289'}, {'path': '10x_chromium_single_cell_atac_v2/10x_scATAC_foundational_paper.pdf', 'local_path': '10x_scATAC_foundational_paper.pdf', 'sha256': 'd8d1486de10fdecbc720a267bb4fb54e3818677380d1873491333f3311c5052f'}, {'path': '10x_chromium_single_cell_atac_v2/10x_scATAC_foundational_supplement.pdf', 'local_path': '10x_scATAC_foundational_supplement.pdf', 'sha256': '8ecc3a81de7feda3cf1917aa9bb802dd89de400b46daf1e98e651c40ed7e5823'}]


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
