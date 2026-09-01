from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'plate_scatac_seq/plate_scATAC-seq.pdf', 'local_path': 'plate_scATAC-seq.pdf', 'sha256': 'f5773809db99b63663992e58244bd5650d9dda436231f25c68bcc95d31432042'}, {'path': 'plate_scatac_seq/plate_scATAC-seq_original_paper.pdf', 'local_path': 'plate_scATAC-seq_original_paper.pdf', 'sha256': '713fe064fc5d4577a2f555099a0918f4b819c2ba83e70ebe8f02f8212535bde0'}]


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
