from pathlib import Path

from huggingface_hub import hf_hub_download


REPO_ID = "sequencing/scg-protocols-v1"
REPO_TYPE = "dataset"
REVISION = "main"
INPUT_PATHS = [
    '10x_chromium_single_cell_atac_v2/10xChromium_scATACv2.human_text.txt',
    '10x_chromium_single_cell_atac_v2/10xChromium_scATACv2.mineru_ocr.txt',
    '10x_chromium_single_cell_atac_v2/10xChromium_scATACv2.pymupdf_text.txt',
    '10x_chromium_single_cell_atac_v2/10xChromium_scATACv2.pypdf_text.txt',
    '10x_chromium_single_cell_atac_v2/10xChromium_scATACv2.docling_text.txt',
]


def main() -> None:
    out_dir = Path("input")
    out_dir.mkdir(parents=True, exist_ok=True)
    for repo_path in INPUT_PATHS:
        local_path = hf_hub_download(
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
            revision=REVISION,
            filename=repo_path,
        )
        target = out_dir / Path(repo_path).name
        target.write_bytes(Path(local_path).read_bytes())
        print(f"downloaded {repo_path} -> {target}")


if __name__ == "__main__":
    main()
