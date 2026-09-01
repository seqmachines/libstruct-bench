from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'scifi_atac_seq/scifi-ATAC-seq.pdf', 'local_path': 'scifi-ATAC-seq.pdf', 'sha256': '6306fab0a82f1653a0ed060c724d0451f157f8112e15b94b018c194ac32dd4cf'}, {'path': 'scifi_atac_seq/scifi-ATAC-seq_original_paper.pdf', 'local_path': 'scifi-ATAC-seq_original_paper.pdf', 'sha256': '8da266e776285f5a2eb8517c32231fbc74b26e8e70bf08f12816edef85771bd1'}]


def main() -> int:
    root = Path("/workspace/input")
    for source in SOURCES:
        path = root / source["local_path"]
        if not path.is_file():
            raise FileNotFoundError(f"missing embedded source: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise RuntimeError(
                f"source hash mismatch for {source['path']}: expected {source['sha256']}, found {digest}"
            )
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
