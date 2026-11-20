"""Physical locations of primary scg documents, independent of frozen manifest paths."""

from pathlib import Path


def scg_source_roots(private: Path) -> tuple[Path, Path]:
    return private / "protocols_scg_all", private / "protocols"


def scg_source_root(private: Path) -> Path:
    current, legacy = scg_source_roots(private)
    return legacy if not current.exists() and legacy.is_dir() else current
