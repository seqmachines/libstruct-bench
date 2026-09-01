from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'strt_seq_c1/STRT-seq-C1.pdf', 'local_path': 'STRT-seq-C1.pdf', 'sha256': 'dbc767d4ae895885aab93dba6847795ddc955cabc55fdb89c2305e12782c857a'}, {'path': 'strt_seq_c1/STRT-seq-C1.xlsx', 'local_path': 'STRT-seq-C1.xlsx', 'sha256': '8844d61c3af17102c43663ce3b4c13f46811ec470b67df917a4a4a8488c0cf88'}, {'path': 'strt_seq_c1/STRT-seq-C1_supplement.pdf', 'local_path': 'STRT-seq-C1_supplement.pdf', 'sha256': '5933c3bf658db8390a2d89523009d07b425847f5b209c84109fdec28b215f4b2'}]


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
