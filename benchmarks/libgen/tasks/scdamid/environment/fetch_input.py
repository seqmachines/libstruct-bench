from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'scdamid/scDamID_indexing_primers_table.doc', 'local_path': 'scDamID_indexing_primers_table.doc', 'sha256': '66e170fbf743ccffd0540b43c991a97e61b512995ebea7585c7b7036cf7cb1b3'}, {'path': 'scdamid/scDamID_paper.pdf', 'local_path': 'scDamID_paper.pdf', 'sha256': '15207de0a14eaad90d99b0a9fea5aad226db62b9ed702bae0cc16abbf7707201'}, {'path': 'scdamid/scDamID_supp.pdf', 'local_path': 'scDamID_supp.pdf', 'sha256': '80a616cc1df0614cd32ac916cf6d55a473ccde9ed3fb6829d04b85c4e13c937c'}]


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
