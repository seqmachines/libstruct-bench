from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': 'sci_atac_seq/sci-ATAC-seq.xlsx', 'local_path': 'sci-ATAC-seq.xlsx', 'sha256': '954913c9ac149978d5221b375d40689a0ac9ec3e8c60f7165f2fa7d1de3f4ec4'}, {'path': 'sci_atac_seq/sci-ATAC-seq_original_paper_corrected.pdf', 'local_path': 'sci-ATAC-seq_original_paper_corrected.pdf', 'sha256': 'e633a06145b85d90feaef58c2790bfdc5b6711ef98c5fb15aed45d88a3ad1d1f'}, {'path': 'sci_atac_seq/sci-ATAC-seq_paper.pdf', 'local_path': 'sci-ATAC-seq_paper.pdf', 'sha256': 'bcf526797a5efa310c8b5edab0c0b6c84650b36f0cd4c7aba23a82fe67d898e3'}, {'path': 'sci_atac_seq/sci-ATAC-seq_supp.pdf', 'local_path': 'sci-ATAC-seq_supp.pdf', 'sha256': '1c6fb4ba5a9558de4490cb0c6d7a948a43fffe9c33e87a650378dd632205d1b0'}]


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
