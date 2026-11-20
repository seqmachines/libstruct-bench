"""Set up verifier references for a single protocol from the paper panel."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from libstruct_bench.hf_io import download_hf_dataset_file

REPOSITORY = "sequencing/scg-protocols-v1-groundtruth"
REVISION = "87cee8097f882d5005398a75b056ad21601768e7"
PAPER_MANIFEST = Path(__file__).resolve().parents[2] / "paper/provenance/camera_ready/input_manifest.json"


def setup_paper_truth(protocol: str, directory: Path, filenames: tuple[str, ...]) -> None:
    manifest = json.loads(PAPER_MANIFEST.read_bytes())
    panel = {row["protocol_id"] for row in manifest["panels"] if row["panel"] == "fig3a"}
    if protocol not in panel:
        raise ValueError(f"{protocol!r} is outside the paper panel. Supply approved local JSON files with --truth.")
    pins = {
        name: manifest["inputs"][f"external/audit-data/ground_truth/{protocol}/{name}"]
        for name in filenames
    }
    missing = []
    for name, expected in pins.items():
        path = directory / name
        if path.is_symlink() or path.exists():
            _verify_existing(path, expected)
        else:
            missing.append(name)
    if not missing:
        return

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory) as staging:
        for name in missing:
            raw = download_hf_dataset_file(repo_id=REPOSITORY, revision=REVISION, path=f"{protocol}/{name}")
            if hashlib.sha256(raw).hexdigest() != pins[name]:
                raise ValueError(f"{protocol}/{name}: download does not match the paper checksum")
            (Path(staging) / name).write_bytes(raw)
        for name in missing:
            try:
                os.link(Path(staging) / name, directory / name)
            except FileExistsError:
                _verify_existing(directory / name, pins[name])


def _verify_existing(path: Path, expected: str) -> None:
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"{path}: existing file does not match the paper reference. Select a separate --truth folder.")
