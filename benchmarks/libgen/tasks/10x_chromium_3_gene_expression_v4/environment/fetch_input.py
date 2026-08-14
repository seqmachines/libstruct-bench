from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = 'sequencing/scg-protocols-v1'
REVISION = '19c29caabcb019a72d08fed59ed8030a2d3c449d'
SOURCES = [{'path': '10x_chromium_3_gene_expression_v4/10xChromium3v4.pdf', 'local_path': '10xChromium3v4.pdf', 'sha256': '6661eb5da6e604436675db567467f859d22f3d901afdb45f13b03a65f42589fb'}, {'path': '10x_chromium_3_gene_expression_v4/Chromium_3p_foundational_paper.pdf', 'local_path': 'Chromium_3p_foundational_paper.pdf', 'sha256': '3fd14d3c9fde8f8a16d938ebfe405920c16feeccc780561dc2d0a504f01baab4'}, {'path': '10x_chromium_3_gene_expression_v4/Chromium_3p_foundational_supplement.pdf', 'local_path': 'Chromium_3p_foundational_supplement.pdf', 'sha256': '700d938ef92c836f2499b5b40135b7fa172328c7871f273e43120a409535b06b'}]


def main() -> int:
    root = Path("/workspace/input")
    for source in SOURCES:
        output = root / source["local_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(_url(source["path"]), headers={"User-Agent": "libstruct-bench"})
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != source["sha256"]:
            raise RuntimeError(
                f"source hash mismatch for {source['path']}: expected {source['sha256']}, found {digest}"
            )
        output.write_bytes(data)
        print(output)
    return 0


def _url(path: str) -> str:
    repo = urllib.parse.quote(REPO_ID.strip().removeprefix("datasets/"), safe="/")
    rev = urllib.parse.quote(REVISION, safe="")
    item = urllib.parse.quote(path.lstrip("/"), safe="/")
    return f"https://huggingface.co/datasets/{repo}/resolve/{rev}/{item}"


if __name__ == "__main__":
    raise SystemExit(main())
