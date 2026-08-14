from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'share_seq/SHARE-seq.pdf', 'local_path': 'SHARE-seq.pdf', 'sha256': 'be7253756f1f3567daa7270433b1ce00306b42da7560978682bc5ea4349b5083'}, {'path': 'share_seq/SHARE-seq.xlsx', 'local_path': 'SHARE-seq.xlsx', 'sha256': 'f3b0ce8e4612f651c26292a4cf0ce1fbfbde5931eee25af05d2b8751b3a4ff71'}, {'path': 'share_seq/SHARE-seq_workflow_supplement.pdf', 'local_path': 'SHARE-seq_workflow_supplement.pdf', 'sha256': '15fac55d0920a31453bad8354d511581d5f12dff2c7992efbc93baa1b815afbe'}]


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
