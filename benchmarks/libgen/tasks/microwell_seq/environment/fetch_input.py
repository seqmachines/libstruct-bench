from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'microwell_seq/Microwell-seq.pdf', 'local_path': 'Microwell-seq.pdf', 'sha256': '64b440bc37f6cb00700a6ada5258d3fff0a64ea790e04478d895105a9714a38e'}, {'path': 'microwell_seq/Microwell-seq_supp1.xlsx', 'local_path': 'Microwell-seq_supp1.xlsx', 'sha256': 'df348ae5348eb00b30211b7cafa3301db6604bbda8db5a2490360c81e5cc1d88'}, {'path': 'microwell_seq/Microwell-seq_supp2.xlsx', 'local_path': 'Microwell-seq_supp2.xlsx', 'sha256': '185aa52c602b216cb015ed1056bd52a7e877d00dacbbdce0ca8d904b5371b5b7'}]


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
