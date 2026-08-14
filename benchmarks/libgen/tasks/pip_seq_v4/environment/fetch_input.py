from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'pip_seq_v4/PIP-seq_oligo_table.xlsx', 'local_path': 'PIP-seq_oligo_table.xlsx', 'sha256': '7a338bc093b8ba415407c4bdad5cd159f5c1bdc384dc4982934a6fcec4456e0a'}, {'path': 'pip_seq_v4/PIP-seq_original_paper.pdf', 'local_path': 'PIP-seq_original_paper.pdf', 'sha256': '3de78ffa0bbeece92a70bd6555a8a921f22b8ea772e1d8bac3559952108e4a12'}, {'path': 'pip_seq_v4/PIP-seqv4.pdf', 'local_path': 'PIP-seqv4.pdf', 'sha256': '9299bdb6ea132d575413e55d0203a094ccfe7cb7942a2d85136bae1994874bb6'}]


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
