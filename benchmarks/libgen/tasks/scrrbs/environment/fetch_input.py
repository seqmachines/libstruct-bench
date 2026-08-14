from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'scrrbs/paper.pdf', 'local_path': 'paper.pdf', 'sha256': '4d31ab24e81f0f3f5f7547a6cf030bbb8ee19532b191da79604fa151be5a4651'}, {'path': 'scrrbs/scRRBS.pdf', 'local_path': 'scRRBS.pdf', 'sha256': 'c3521a628635553e2de9169214b731a676bae6dcf9f11467d5014aad8fbcbc30'}]


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
