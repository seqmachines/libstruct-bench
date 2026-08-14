from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'seq_well_s3/SeqWell_S3_paper.pdf', 'local_path': 'SeqWell_S3_paper.pdf', 'sha256': 'b634dc0f285332de12725f34dba11715720cacf31d84982d8f52a9a01d07f2a7'}, {'path': 'seq_well_s3/SeqWell_S3_supp.pdf', 'local_path': 'SeqWell_S3_supp.pdf', 'sha256': '0de4dfbdf46f494f68387b80bd122e3614006039257340a94dba75718a870635'}, {'path': 'seq_well_s3/supplementary_mmc1.pdf', 'local_path': 'supplementary_mmc1.pdf', 'sha256': '0d64ab07db193fdb84289d86a58a558d987a8d53e30a79d3180957b56c430d08'}]


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
