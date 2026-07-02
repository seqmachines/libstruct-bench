from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path

PROTOCOL_ID = 'sci_atac_seq'
REPO_ID = 'sequencing/scg-protocols-v1'
INPUT_PATHS = ['sci_atac_seq/sci-ATAC-seq.xlsx', 'sci_atac_seq/sci-ATAC-seq_paper.pdf', 'sci_atac_seq/sci-ATAC-seq_supp.pdf']
REVISION = '59f49763dc258db15d754ba6504b5cacd3404e94'


def main() -> int:
    output_dir = Path("input")
    output_dir.mkdir(exist_ok=True)
    for input_path in INPUT_PATHS:
        url = _dataset_url(REPO_ID, REVISION, input_path)
        output_path = output_dir / Path(input_path).name
        with urllib.request.urlopen(url, timeout=120) as response:
            output_path.write_bytes(response.read())
        print(output_path)
    return 0


def _dataset_url(repo_id: str, revision: str, path: str) -> str:
    return (
        "https://huggingface.co/datasets/"
        + urllib.parse.quote(repo_id.strip().removeprefix("datasets/"), safe="/")
        + "/resolve/"
        + urllib.parse.quote(revision or "main", safe="")
        + "/"
        + urllib.parse.quote(path.lstrip("/"), safe="/")
    )


if __name__ == "__main__":
    raise SystemExit(main())
