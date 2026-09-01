from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'strt_seq_2i/STRT-seq-2i.pdf', 'local_path': 'STRT-seq-2i.pdf', 'sha256': '9b90764c5ff4f40598c0f76efffdaded84f0a76c56786239228398ddf4e55e7b'}, {'path': 'strt_seq_2i/STRT-seq-2i_supplement.pdf', 'local_path': 'STRT-seq-2i_supplement.pdf', 'sha256': '4afeb8e4c15b712c20201125b709ae71ff92344682b91ac32cd18eb7ef503b05'}]


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
