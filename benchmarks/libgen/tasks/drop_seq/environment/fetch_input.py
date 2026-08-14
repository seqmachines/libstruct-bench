from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'drop_seq/Drop-seq_author_protocol_v1.1.pdf', 'local_path': 'Drop-seq_author_protocol_v1.1.pdf', 'sha256': '0e662a35307956437f2414ffbbe1681f6fcd9f0c6403e4a7f148215e417b1b93'}, {'path': 'drop_seq/drop-seq_paper.pdf', 'local_path': 'drop-seq_paper.pdf', 'sha256': '84179dc8bfce57a3195ebe5d53ed1257883d9e18111cef6a2dcd90dfadae92b7'}, {'path': 'drop_seq/drop-seq_supp.pdf', 'local_path': 'drop-seq_supp.pdf', 'sha256': '7b63216599c518dccd63c484cb6bc448fa181e8d9ddbdf0f2ac788b12296442d'}]


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
