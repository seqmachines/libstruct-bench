"""Install previously verified staging packages locally, backing up old packages.

No network calls; never copies source documents or changes canonical ground truth.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from libstruct_bench.protocol_paths import scg_source_root
from libstruct_bench.audit.hf_groundtruth import load, pinned, write
from libstruct_bench.audit.verify_groundtruth_upload import sha256, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--private-root', type=Path, required=True)
    args = parser.parse_args()
    staging, private = args.staging.resolve(), args.private_root.resolve()
    names = ['scg-v1-upload', 'scg-all-upload']
    if staging == private or staging.is_relative_to(private / 'ground_truth'):
        raise ValueError('Staging must be separate from canonical ground truth')
    for folder in names:
        source = staging / folder / 'groundtruth'
        check = verify(source, sources=scg_source_root(private))
        if not check['ok']:
            raise ValueError(check)
        # Refuse installation if canonical labels changed after packaging.
        for record in load(source / 'MANIFEST.json')['files']:
            pinned(private / 'ground_truth' / record['path'], record['sha256'])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = private / 'upload-preparation-backups' / stamp
    receipt = {'staging': str(staging), 'backup': str(backup), 'installed': []}
    for folder in names:
        destination = private / folder
        destination.mkdir(exist_ok=True)
        # Copy and verify before moving an old package away.
        pending = destination / ('.groundtruth-prepared-' + stamp)
        shutil.copytree(staging / folder / 'groundtruth', pending)
        check = verify(pending)
        if not check['ok']:
            raise ValueError(check)
        target = destination / 'groundtruth'
        if target.exists():
            old_manifest = load(target / 'MANIFEST.json') if (target / 'MANIFEST.json').is_file() else {}
            backup_target = backup / folder / 'groundtruth'
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup_target)
        else:
            old_manifest = {}
        pending.rename(target)
        for filename in ['UPLOAD.md', 'PREPARATION_REPORT.json', 'PROSE_SCREEN.json']:
            source = staging / folder / filename if filename == 'UPLOAD.md' else staging / filename
            if not source.is_file():
                continue
            dest = destination / filename
            if dest.exists():
                prior = backup / folder / filename
                prior.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dest, prior)
            shutil.copyfile(source, dest)
        receipt['installed'].append({'directory': str(target), 'verified': verify(target),
                                     'manifest_sha256': sha256(target / 'MANIFEST.json'),
                                     'previous_manifest_file_count': len(old_manifest.get('files', []))})
    write(private / 'upload-preparation-backups' / stamp / 'INSTALL_RECEIPT.json', receipt)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
