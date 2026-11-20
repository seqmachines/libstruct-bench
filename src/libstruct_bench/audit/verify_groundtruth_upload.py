"""Standalone, offline integrity checks for a prepared ground-truth release."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import unicodedata


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text_sha256(text: str) -> str:
    # This is an auxiliary text fingerprint, not a substitute for PDF/figure identity.
    value = ' '.join(unicodedata.normalize('NFKC', text).split())
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    part = PurePosixPath(relative)
    if part.is_absolute() or '..' in part.parts or not part.parts:
        raise ValueError(f'Unsafe manifest path: {relative}')
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'External/symlink path: {relative}')
    return path


def verify(root: Path, *, sources: Path | None = None) -> dict:
    manifest = json.loads((root / 'CHECKSUMS.json').read_text())
    expected = {r['path'] for r in manifest['files']} | {'CHECKSUMS.json'}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    errors = [f'Unexpected file: {p}' for p in sorted(actual - expected)]
    for record in manifest['files']:
        p = safe_file(root, record['path'])
        if not p.is_file() or sha256(p) != record['sha256']:
            errors.append(f'Missing/changed release file: {record["path"]}')
    checked_sources = 0
    if sources is not None:
        for record in json.loads((root / 'SOURCE_MANIFEST.json').read_text())['files']:
            if not record.get('sha256'):
                continue
            p = safe_file(sources, record['path'])
            if not p.is_file() or sha256(p) != record['sha256']:
                errors.append(f'Missing/changed source copy: {record["path"]}')
            checked_sources += 1
    return {'ok': not errors, 'release_files': len(expected),
            'source_files_checked': checked_sources, 'errors': errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--source-root', type=Path,
                        help='Optional user-supplied sources, arranged as protocol_id/filename')
    parser.add_argument('--text-copy', type=Path,
                        help='Print an auxiliary normalized fingerprint of an extracted UTF-8 text file')
    args = parser.parse_args()
    if args.text_copy:
        print(normalized_text_sha256(args.text_copy.read_text(encoding='utf-8')))
        return 0
    result = verify(args.release_root, sources=args.source_root)
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
