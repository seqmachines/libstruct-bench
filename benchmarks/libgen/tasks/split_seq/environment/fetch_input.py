from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'split_seq/SPLiT-seq.xlsx', 'local_path': 'SPLiT-seq.xlsx', 'sha256': '1bf965120d296995ee6793340b5ee0fecf3c06806a55a5318cd5d15cd6bae426'}, {'path': 'split_seq/SPLiT-seq_paper.pdf', 'local_path': 'SPLiT-seq_paper.pdf', 'sha256': 'f374224c2a8533eef5bf47f313812d41d69dd2bee6a87c0a0f0525858f201fc0'}, {'path': 'split_seq/SPLiT-seq_supp.pdf', 'local_path': 'SPLiT-seq_supp.pdf', 'sha256': '79bd5601be71f1a052ed5816740ea842e29a204f4f58f4ab5badac02c1c0c9fc'}]


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
