from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from .artifacts import (
    AuditArtifactError,
    canonical_json_bytes,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)


SUPPORTED_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class RenditionError(ValueError):
    """Raised when an approved primary source cannot be rendered deterministically."""


@dataclass(frozen=True)
class RenditionResult:
    output_dir: Path
    bundle_path: Path
    protocol_id: str
    source_count: int
    artifact_count: int


def build_rendition_bundle(
    *,
    manifest_path: Path,
    source_dataset_dir: Path,
    output_dir: Path,
    manifest_schema_path: Path,
    rendition_schema_path: Path,
    created_at: str | None = None,
    pdf_dpi: int = 144,
) -> RenditionResult:
    """Create native-text, table, and figure renditions for every primary document."""

    manifest_path = _file(manifest_path, "audit input manifest")
    source_dataset_dir = _directory(source_dataset_dir, "source dataset")
    manifest_schema_path = _file(manifest_schema_path, "input manifest schema")
    rendition_schema_path = _file(rendition_schema_path, "rendition schema")
    if pdf_dpi < 72 or pdf_dpi > 300:
        raise RenditionError("PDF DPI must be between 72 and 300")
    manifest = load_json_object(manifest_path, label="audit input manifest")
    _validate(manifest, manifest_schema_path, "audit input manifest")
    sources = [
        source
        for source in manifest["sources"]
        if source["role"] == "primary_evidence"
        and source["approval_status"] == "included"
    ]
    unsupported = [
        source["source_id"]
        for source in sources
        if Path(source["path"]).suffix.lower() not in SUPPORTED_SUFFIXES
    ]
    if unsupported:
        raise RenditionError(
            "included primary sources have unsupported document types: "
            + ", ".join(unsupported)
        )
    if not sources:
        raise RenditionError("manifest has no included primary documents")

    resolved_sources = {
        source["source_id"]: _resolve_dataset_path(
            source_dataset_dir, source["dataset_reference"]["path"]
        )
        for source in sources
    }

    output_dir = output_dir.expanduser().resolve()
    _reject_output(output_dir, set(resolved_sources.values()))
    if output_dir.exists():
        raise RenditionError(f"rendition output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    toolchain: dict[str, str] = {"libstruct-bench-renditions": "1"}
    source_records: list[dict[str, Any]] = []
    try:
        for source_index, source in enumerate(
            sorted(sources, key=lambda item: item["source_id"]), start=1
        ):
            actual = resolved_sources[source["source_id"]]
            if sha256_file(actual) != source["sha256"]:
                raise RenditionError(f"stale source hash: {source['source_id']}")
            if actual.stat().st_size != source["size_bytes"]:
                raise RenditionError(f"stale source size: {source['source_id']}")
            source_dir = temporary_dir / (
                f"{source_index:03d}-{_slug(source['source_id'])}"
            )
            source_dir.mkdir()
            artifacts = _render_source(
                source=actual,
                output_dir=source_dir,
                pdf_dpi=pdf_dpi,
                toolchain=toolchain,
            )
            if not artifacts:
                raise RenditionError(
                    f"no rendition artifacts produced for {source['source_id']}"
                )
            artifact_records: list[dict[str, Any]] = []
            for artifact_index, artifact in enumerate(artifacts, start=1):
                path = artifact["path"]
                relative = path.relative_to(temporary_dir).as_posix()
                record = {
                    "artifact_id": (
                        f"{_slug(source['source_id'])}:rendition:{artifact_index:04d}"
                    ),
                    "kind": artifact["kind"],
                    "path": relative,
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "media_type": artifact["media_type"],
                }
                for key in ("page", "sheet"):
                    if key in artifact:
                        record[key] = artifact[key]
                artifact_records.append(record)
            source_records.append(
                {
                    "source_id": source["source_id"],
                    "source_sha256": source["sha256"],
                    "source_media_type": source["media_type"],
                    "status": "rendered",
                    "artifacts": artifact_records,
                }
            )

        identity = hashlib.sha256(
            canonical_json_bytes(
                {
                    "manifest": sha256_file(manifest_path),
                    "sources": source_records,
                    "toolchain": toolchain,
                }
            )
        ).hexdigest()
        bundle = {
            "bundle_id": f"{manifest['protocol_id']}:renditions:{identity[:16]}",
            "protocol_id": manifest["protocol_id"],
            "input_manifest_sha256": sha256_file(manifest_path),
            "created_at": _timestamp(created_at),
            "toolchain": [
                {"name": name, "version": version}
                for name, version in sorted(toolchain.items())
            ],
            "sources": source_records,
        }
        _validate(bundle, rendition_schema_path, "rendition bundle")
        write_json_atomic(temporary_dir / "rendition_bundle.json", bundle)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return RenditionResult(
        output_dir=output_dir,
        bundle_path=output_dir / "rendition_bundle.json",
        protocol_id=manifest["protocol_id"],
        source_count=len(source_records),
        artifact_count=sum(len(item["artifacts"]) for item in source_records),
    )


def _render_source(
    *, source: Path, output_dir: Path, pdf_dpi: int, toolchain: dict[str, str]
) -> list[dict[str, Any]]:
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return _render_pdf(source, output_dir, pdf_dpi, toolchain)
    if suffix == ".docx":
        return _render_docx(source, output_dir, toolchain)
    if suffix == ".xlsx":
        return _render_xlsx(source, output_dir, toolchain)
    if suffix in {".doc", ".xls"}:
        converted = _libreoffice_convert(source, output_dir, toolchain)
        try:
            if converted.suffix.lower() == ".docx":
                return _render_docx(converted, output_dir, toolchain)
            return _render_xlsx(converted, output_dir, toolchain)
        finally:
            converted.unlink(missing_ok=True)
    raise RenditionError(f"unsupported source type: {source.name}")


def _render_pdf(
    source: Path, output_dir: Path, dpi: int, toolchain: dict[str, str]
) -> list[dict[str, Any]]:
    try:
        import fitz
    except ImportError as error:
        raise RenditionError(
            "PDF rendition requires PyMuPDF; install libstruct-bench[audit-docs]"
        ) from error
    toolchain["PyMuPDF"] = getattr(fitz, "VersionBind", "unknown")
    artifacts: list[dict[str, Any]] = []
    text_parts: list[str] = []
    try:
        document = fitz.open(source)
        for page_index, page in enumerate(document, start=1):
            text_parts.append(f"===== PAGE {page_index} =====\n")
            text_parts.append(page.get_text("text", sort=True))
            text_parts.append("\n")
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            image_path = output_dir / f"page-{page_index:04d}.png"
            pixmap.save(image_path)
            artifacts.append(
                {
                    "path": image_path,
                    "kind": "page_image",
                    "media_type": "image/png",
                    "page": page_index,
                }
            )
        document.close()
    except Exception as error:
        raise RenditionError(f"cannot render PDF {source.name}: {error}") from error
    text_path = output_dir / "native-text.txt"
    text_path.write_text("".join(text_parts), encoding="utf-8")
    artifacts.insert(
        0,
        {"path": text_path, "kind": "native_text", "media_type": "text/plain"},
    )
    return artifacts


def _render_docx(
    source: Path, output_dir: Path, toolchain: dict[str, str]
) -> list[dict[str, Any]]:
    toolchain["python-zipfile-xml"] = "stdlib"
    artifacts: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(source) as archive:
            _validate_zip(archive)
            document = ElementTree.fromstring(archive.read("word/document.xml"))
            lines: list[str] = []
            for paragraph in document.iter(f"{{{_WORD_NS}}}p"):
                text = "".join(
                    node.text or ""
                    for node in paragraph.iter(f"{{{_WORD_NS}}}t")
                ).strip()
                if text:
                    lines.append(text)
            text_path = output_dir / "native-text.txt"
            text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            artifacts.append(
                {"path": text_path, "kind": "native_text", "media_type": "text/plain"}
            )
            for index, member in enumerate(
                sorted(name for name in archive.namelist() if name.startswith("word/media/")),
                start=1,
            ):
                suffix = Path(member).suffix.lower() or ".bin"
                media_path = output_dir / f"embedded-{index:04d}{suffix}"
                media_path.write_bytes(archive.read(member))
                artifacts.append(
                    {
                        "path": media_path,
                        "kind": "embedded_image",
                        "media_type": mimetypes.guess_type(media_path.name)[0]
                        or "application/octet-stream",
                    }
                )
    except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise RenditionError(f"cannot render DOCX {source.name}: {error}") from error
    return artifacts


def _render_xlsx(
    source: Path, output_dir: Path, toolchain: dict[str, str]
) -> list[dict[str, Any]]:
    toolchain["python-zipfile-xml"] = "stdlib"
    artifacts: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(source) as archive:
            _validate_zip(archive)
            shared = _shared_strings(archive)
            sheets = _workbook_sheets(archive)
            lines: list[str] = []
            for sheet_name, member in sheets:
                lines.append(f"===== SHEET {sheet_name} =====")
                root = ElementTree.fromstring(archive.read(member))
                for cell in root.iter(f"{{{_SHEET_NS}}}c"):
                    coordinate = cell.attrib.get("r", "?")
                    value = _cell_value(cell, shared)
                    lines.append(f"{coordinate}\t{value}")
                lines.append("")
            text_path = output_dir / "tables.txt"
            text_path.write_text("\n".join(lines), encoding="utf-8")
            artifacts.append(
                {"path": text_path, "kind": "table_text", "media_type": "text/plain"}
            )
            for index, member in enumerate(
                sorted(name for name in archive.namelist() if name.startswith("xl/media/")),
                start=1,
            ):
                suffix = Path(member).suffix.lower() or ".bin"
                media_path = output_dir / f"embedded-{index:04d}{suffix}"
                media_path.write_bytes(archive.read(member))
                artifacts.append(
                    {
                        "path": media_path,
                        "kind": "embedded_image",
                        "media_type": mimetypes.guess_type(media_path.name)[0]
                        or "application/octet-stream",
                    }
                )
    except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise RenditionError(f"cannot render XLSX {source.name}: {error}") from error
    return artifacts


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{_SHEET_NS}}}t"))
        for item in root.iter(f"{{{_SHEET_NS}}}si")
    ]


def _workbook_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    targets = {
        node.attrib["Id"]: node.attrib["Target"]
        for node in relationships.iter(f"{{{_PACKAGE_REL_NS}}}Relationship")
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.iter(f"{{{_SHEET_NS}}}sheet"):
        relation_id = sheet.attrib[f"{{{_REL_NS}}}id"]
        target = targets[relation_id].lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        normalized = PurePosixPath(target)
        if any(part in {".", ".."} for part in normalized.parts):
            raise RenditionError(f"unsafe XLSX worksheet target: {target}")
        result.append((sheet.attrib["name"], normalized.as_posix()))
    return result


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(
            node.text or "" for node in cell.iter(f"{{{_SHEET_NS}}}t")
        )
    value = cell.find(f"{{{_SHEET_NS}}}v")
    raw = value.text if value is not None and value.text is not None else ""
    if kind == "s" and raw:
        try:
            return shared[int(raw)]
        except (IndexError, ValueError) as error:
            raise RenditionError(f"invalid XLSX shared-string index: {raw}") from error
    formula = cell.find(f"{{{_SHEET_NS}}}f")
    if formula is not None and formula.text:
        return f"={formula.text} -> {raw}"
    return raw


def _libreoffice_convert(
    source: Path, output_dir: Path, toolchain: dict[str, str]
) -> Path:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if executable is None:
        raise RenditionError(
            f"{source.suffix} rendition requires LibreOffice in PATH"
        )
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise RenditionError(f"cannot determine LibreOffice version: {error}") from error
    toolchain["LibreOffice"] = version or "unknown"
    target_format = "docx" if source.suffix.lower() == ".doc" else "xlsx"
    profile = output_dir / "libreoffice-profile"
    profile.mkdir()
    command = [
        executable,
        f"-env:UserInstallation={profile.as_uri()}",
        "--headless",
        "--convert-to",
        target_format,
        "--outdir",
        str(output_dir),
        str(source),
    ]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=300
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RenditionError(f"LibreOffice conversion failed: {error}") from error
    converted = output_dir / f"{source.stem}.{target_format}"
    if completed.returncode != 0 or not converted.is_file():
        raise RenditionError(
            f"LibreOffice conversion failed for {source.name}: {completed.stderr.strip()}"
        )
    shutil.rmtree(profile, ignore_errors=True)
    return converted


def _validate_zip(archive: zipfile.ZipFile) -> None:
    total = 0
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
            raise RenditionError(f"unsafe archive member: {member.filename}")
        total += member.file_size
        if total > 1_000_000_000:
            raise RenditionError("expanded office document exceeds 1 GB safety limit")


def _resolve_dataset_path(root: Path, logical_path: str) -> Path:
    portable = PurePosixPath(logical_path)
    if portable.is_absolute() or not portable.parts or any(
        part in {".", ".."} for part in portable.parts
    ):
        raise RenditionError(f"unsafe source path: {logical_path}")
    candidate = root.joinpath(*portable.parts)
    if candidate.is_symlink():
        raise RenditionError(f"source must not be a symlink: {logical_path}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise RenditionError(f"source is missing: {logical_path}") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise RenditionError(f"source escapes dataset: {logical_path}")
    return resolved


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:100] or "source"


def _validate(document: dict[str, Any], schema: Path, label: str) -> None:
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
        raise RenditionError(str(error)) from error


def _timestamp(value: str | None) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise RenditionError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RenditionError(f"{label} does not exist: {path}")
    return resolved


def _directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise RenditionError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(output: Path, protected_sources: set[Path]) -> None:
    for source in protected_sources:
        source_parent = source.parent
        if (
            output == source_parent
            or output.is_relative_to(source_parent)
            or source_parent.is_relative_to(output)
        ):
            raise RenditionError(
                f"rendition output overlaps an approved source directory: {source_parent}"
            )
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (output == repo or output.is_relative_to(repo)):
        raise RenditionError("private rendition output must not be written inside libstruct-bench")
