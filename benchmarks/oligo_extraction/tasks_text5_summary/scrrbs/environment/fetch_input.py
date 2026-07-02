from pathlib import Path

from huggingface_hub import hf_hub_download


REPO_ID = "sequencing/scg-protocols-v1"
REPO_TYPE = "dataset"
REVISION = "main"
INPUT_PATHS = [
    'scrrbs/scRRBS.human_text.txt',
    'scrrbs/paper.mineru_ocr.txt',
    'scrrbs/scRRBS.mineru_ocr.txt',
    'scrrbs/paper.pymupdf_text.txt',
    'scrrbs/scRRBS.pymupdf_text.txt',
    'scrrbs/paper.pypdf_text.txt',
    'scrrbs/scRRBS.pypdf_text.txt',
    'scrrbs/paper.docling_text.txt',
    'scrrbs/scRRBS.docling_text.txt',
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
