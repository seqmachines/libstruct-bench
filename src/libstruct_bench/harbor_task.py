"""Publish complete generated Harbor tasks without mixing inputs and build output."""

import shutil
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from libstruct_bench.audit.artifacts import sha256_file


DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".tsv", ".txt"}


@contextmanager
def build_task_directory(
    out: Path, protocol_id: str, *, force: bool, inputs: Iterable[Path | str | None]
) -> Iterator[Path]:
    if (
        not protocol_id
        or Path(protocol_id).name != protocol_id
        or protocol_id in {".", ".."}
    ):
        raise ValueError(f"invalid protocol ID: {protocol_id!r}")
    target = out.resolve() / protocol_id
    for directory in inputs:
        if directory is None:
            continue
        source = Path(directory).resolve()
        if source.is_relative_to(target) or target.is_relative_to(source):
            raise ValueError("task output overlaps a source or ground-truth directory")
    if target.is_symlink() or (target.exists() and (not target.is_dir() or not force)):
        raise FileExistsError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as tmp:
        stage = Path(tmp) / "task"
        yield stage
        previous = Path(tmp) / "previous"
        if target.exists() and force:
            target.rename(previous)
        try:
            stage.rename(target)
        except OSError:
            if previous.exists():
                previous.rename(target)
            raise


def copy_task_modules(package: Path, destination: Path, files: Iterable[str]) -> None:
    """Install the modules needed by one side of a prepared task."""
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / relative, target)


def document_inventory(root: Path, protocol_ids: list[str], documents: list[str]) -> dict:
    """Build an in-memory inventory from explicitly selected local evidence."""
    if len(protocol_ids) != 1:
        raise ValueError("--document requires exactly one --protocol-id")
    protocol = protocol_ids[0]
    if not protocol or Path(protocol).name != protocol or protocol in {".", ".."}:
        raise ValueError(f"invalid protocol ID: {protocol!r}")
    directory = root.resolve() / protocol
    sources = []
    seen = set()
    for value in documents:
        name = Path(value)
        path = directory / name
        if (
            name.is_absolute()
            or ".." in name.parts
            or path.resolve() != path
            or not path.is_file()
            or path.suffix.lower() not in DOCUMENT_SUFFIXES
            or path.name.lower().startswith(("groundtruth", "ground_truth"))
        ):
            raise ValueError(f"expected a primary document inside the protocol folder: {value}")
        if name in seen:
            raise ValueError(f"repeated document: {value}")
        seen.add(name)
        sources.append({"path": (Path(protocol) / name).as_posix(), "sha256": sha256_file(path)})
    return {
        "protocols": [{
            "protocol_id": protocol,
            "sources": sources,
            "input_paths": [item["path"] for item in sources],
        }]
    }
