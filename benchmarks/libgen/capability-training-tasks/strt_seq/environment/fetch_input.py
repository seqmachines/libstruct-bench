from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'strt_seq/STRT-seq.pdf', 'local_path': 'STRT-seq.pdf', 'sha256': '5f6441985a7aa53e5fe6a1f7aa26e753aebea9ed76268d0d133bf40cf4185480'}, {'path': 'strt_seq/STRT-seq_TSO_and_plate_tables.docx', 'local_path': 'STRT-seq_TSO_and_plate_tables.docx', 'sha256': 'cfc4aee0e3c47bc8d6263a5ede13df42817721ae70be6224fe6cc26216c13231'}, {'path': 'strt_seq/STRT-seq_original_paper.pdf', 'local_path': 'STRT-seq_original_paper.pdf', 'sha256': '97077af02b8aa6b03f943fed39b80f97670d1871273a7857d427e9b18ea01faa'}]


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
