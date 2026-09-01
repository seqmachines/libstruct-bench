from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'crispr_sciatac/CRISPR-sciATAC.pdf', 'local_path': 'CRISPR-sciATAC.pdf', 'sha256': '5a28f795fbed9c02fbf0d99ae67af16e130db36be47dd6dd099ccc45d2b5d1d4'}, {'path': 'crispr_sciatac/CRISPR-sciATAC.xlsx', 'local_path': 'CRISPR-sciATAC.xlsx', 'sha256': '75208b67dd4ec6fa3bd274e738e6a98b272ccbb8d8e602d7c2b64d6d07502f53'}, {'path': 'crispr_sciatac/CRISPR-sciATAC_supplement.pdf', 'local_path': 'CRISPR-sciATAC_supplement.pdf', 'sha256': '9712e93e49d710e2c213fb00e71fd26b8ba07ffaf098aacfc2019ee411119a14'}]


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
