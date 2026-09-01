from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'lianti/LIANTI.pdf', 'local_path': 'LIANTI.pdf', 'sha256': '910a738e286f9c83a508b00084b67ecaa3ad76f655a65f8d8a07afd4ff5d4648'}, {'path': 'lianti/LIANTI_original_paper.pdf', 'local_path': 'LIANTI_original_paper.pdf', 'sha256': 'b4956bc9469de7b1d67ce79ce9b3312ad2f00451574cfaa515d1ac17e902d6bb'}]


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
