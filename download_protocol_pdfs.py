#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib import parse
from urllib import error, request


USER_AGENT = "librarystructdb/1.0"


@dataclass
class ProtocolPDF:
    protocol_id: str
    source_path: Path
    url: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download protocol reference documents referenced by JSON files."
    )
    parser.add_argument(
        "--protocol-dir",
        type=Path,
        default=Path("protocols"),
        help="Directory containing protocol JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("protocol_sources"),
        help="Directory where downloaded source files are written.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite previously downloaded files that already exist.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Per-request timeout in seconds.",
    )
    return parser.parse_args()


def load_protocols(protocol_dir: Path) -> tuple[list[ProtocolPDF], list[str]]:
    records: list[ProtocolPDF] = []
    issues: list[str] = []

    if not protocol_dir.exists():
        return records, [f"Protocol directory does not exist: {protocol_dir}"]

    for path in sorted(protocol_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            issues.append(f"{path}: invalid JSON ({exc})")
            continue

        if not isinstance(data, dict):
            continue

        references = data.get("references")
        if not isinstance(references, dict):
            continue

        protocol_id = data.get("protocol_id")
        if not isinstance(protocol_id, str) or not protocol_id.strip():
            protocol_id = path.stem

        url = references.get("protocol_pdf")
        if url is not None and not isinstance(url, str):
            issues.append(f"{path}: references.protocol_pdf must be a string or null")
            continue

        records.append(
            ProtocolPDF(protocol_id=protocol_id.strip(), source_path=path, url=url)
        )

    return records, issues


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return cleaned.strip("-") or "protocol"


def guess_extension(content_type: str | None, final_url: str) -> str:
    content_type_to_ext = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/csv": ".csv",
        "application/json": ".json",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/zip": ".zip",
    }
    if content_type in content_type_to_ext:
        return content_type_to_ext[content_type]

    suffix = Path(parse.urlparse(final_url).path).suffix.lower()
    if suffix:
        return suffix
    return ".bin"


def find_existing_target(output_dir: Path, protocol_id: str) -> Path | None:
    stem = safe_filename(protocol_id)
    matches = sorted(
        path
        for path in output_dir.glob(f"{stem}.*")
        if path.is_file() and path.name.removesuffix(path.suffix) == stem
    )
    return matches[0] if matches else None


def download_document(
    record: ProtocolPDF, output_dir: Path, overwrite: bool, timeout: int
) -> tuple[str, str]:
    if not record.url:
        return "skipped", f"{record.protocol_id}: no protocol_pdf URL"

    existing_target = find_existing_target(output_dir, record.protocol_id)
    if existing_target is not None and not overwrite:
        return "skipped", f"{record.protocol_id}: {existing_target} already exists"

    req = request.Request(record.url, headers={"User-Agent": USER_AGENT})

    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = response.read()
            content_type = response.headers.get_content_type()
            final_url = response.geturl()
    except error.URLError as exc:
        return "error", f"{record.protocol_id}: request failed for {record.url} ({exc})"

    if not payload:
        return "error", f"{record.protocol_id}: empty response from {record.url}"

    output_dir.mkdir(parents=True, exist_ok=True)
    extension = guess_extension(content_type, final_url)
    target = output_dir / f"{safe_filename(record.protocol_id)}{extension}"
    if overwrite and existing_target is not None and existing_target != target:
        existing_target.unlink()
    tmp_target = target.with_name(f"{target.name}.part")
    tmp_target.write_bytes(payload)
    tmp_target.replace(target)
    return "downloaded", f"{record.protocol_id}: wrote {target}"


def main() -> int:
    args = parse_args()
    records, issues = load_protocols(args.protocol_dir)

    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)

    downloaded = 0
    skipped = 0
    errors = len(issues)

    for record in records:
        status, message = download_document(
            record,
            output_dir=args.output_dir,
            overwrite=args.overwrite,
            timeout=args.timeout,
        )
        print(message)

        if status == "downloaded":
            downloaded += 1
        elif status == "skipped":
            skipped += 1
        else:
            errors += 1

    print(
        f"Summary: downloaded={downloaded} skipped={skipped} errors={errors}",
        file=sys.stderr if errors else sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
