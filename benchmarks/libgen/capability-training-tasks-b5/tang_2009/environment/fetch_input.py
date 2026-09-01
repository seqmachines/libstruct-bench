from __future__ import annotations

import hashlib
from pathlib import Path

SOURCES = [{'path': 'tang_2009/Tang_2009_original_paper.pdf', 'local_path': 'Tang_2009_original_paper.pdf', 'sha256': '9baea229201a13693ae7b645bf0ba961a5604a5c68b56da482968e0d8c210adf'}, {'path': 'tang_2009/Tang_2009_supplement.pdf', 'local_path': 'Tang_2009_supplement.pdf', 'sha256': '9d2c33752380ca4c9e9df44191f5a69a70bd93612c50e9632f2b86ce66832cf8'}, {'path': 'tang_2009/tang2009.pdf', 'local_path': 'tang2009.pdf', 'sha256': '8486e4b04b963b40c720a90973facd6d099e6506aafe269d0950732b0501f861'}]


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
