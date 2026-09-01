from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'malbac/MALBAC.pdf', 'local_path': 'MALBAC.pdf', 'sha256': 'e1cbe9ad3a920c6e899506d1d64aeaffeb9b8625042e9a9ffefb878d7b1341b4'}, {'path': 'malbac/MALBAC_original_paper.pdf', 'local_path': 'MALBAC_original_paper.pdf', 'sha256': '0a15d85fe9ca38a378d9dd2807e9fc57b237b814e6efa2955c381c2b1b5c2110'}]


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
