from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 's3_atac/s3-ATAC.pdf', 'local_path': 's3-ATAC.pdf', 'sha256': '1234d1e43ed42f74d771a141c3731585b39251f3ffc4614413ab3d0672d164ab'}, {'path': 's3_atac/s3-ATAC.xlsx', 'local_path': 's3-ATAC.xlsx', 'sha256': '3b59fe6bd20c2bd1d123c830fcd8507112b2df34633519685a61c75a9ec0ae89'}, {'path': 's3_atac/supplementary_NIHMS1707134-supplement-Supplementary_Information.pdf', 'local_path': 'supplementary_NIHMS1707134-supplement-Supplementary_Information.pdf', 'sha256': 'dd2af9d0611f13f7710a171abbaaa977a880c80608c98c8d95514089ce454064'}]


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
