from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'smart_seq2/SMART-seq2.pdf', 'local_path': 'SMART-seq2.pdf', 'sha256': 'bb8ee0c7f0b815b53b91a0cc26c206eb2c5c4d194370ada1e28a00e2dd49dd97'}, {'path': 'smart_seq2/SMART-seq2_TSO_table.xlsx', 'local_path': 'SMART-seq2_TSO_table.xlsx', 'sha256': '335e18f7419ad9b9662d9874e1dc1d527bed975f8289c090d9616ddf300c55b6'}, {'path': 'smart_seq2/SMART-seq2_detailed_protocol.pdf', 'local_path': 'SMART-seq2_detailed_protocol.pdf', 'sha256': 'f06e80ec0dfba042cc950696cbe18e1396662bff714931e70d115dbce55ba023'}, {'path': 'smart_seq2/SMART-seq2_protocol_variants_table.xlsx', 'local_path': 'SMART-seq2_protocol_variants_table.xlsx', 'sha256': '2d72b73ba646b5140393e65c689d107ccf53c420a9a3a9229b9a96f6772aceff'}, {'path': 'smart_seq2/SMART-seq2_supplement.pdf', 'local_path': 'SMART-seq2_supplement.pdf', 'sha256': '24c07d2451f41ec512db3f11cdd4ba6ba66de58511a68b1c22788d21b1722aee'}]


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
