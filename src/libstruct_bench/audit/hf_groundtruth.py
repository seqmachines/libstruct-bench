"""Prepare allowlisted public ground-truth packages without changing canonical data.

This is a packaging/provenance check, not a new scientific adjudication or a
substitute for the full audit release gate in release.py. It never uploads.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .connected_process import migrate_connected_process_bundle
from .verify_groundtruth_upload import normalized_text_sha256, safe_file, sha256, verify
from libstruct_bench.protocol_paths import scg_source_root
from libstruct_bench.libgen.staging import GROUNDTRUTH_FILENAMES
from libstruct_bench.libgen.validation import validate_groundtruth_bundle
from libstruct_bench.modalities import canonical_modality_label


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def pinned(path: Path, expected: str) -> Path:
    if sha256(path) != expected:
        raise ValueError(f'Stale provenance hash: {path}')
    return path


def modality_view(document: Any) -> Any:
    if isinstance(document, list):
        return [modality_view(x) for x in document]
    if isinstance(document, dict):
        return {k: canonical_modality_label(v) if k == 'modality' and isinstance(v, str)
                else modality_view(v) for k, v in document.items()}
    return document


def without_modality(document: Any) -> Any:
    if isinstance(document, list):
        return [without_modality(x) for x in document]
    if isinstance(document, dict):
        return {k: without_modality(v) for k, v in document.items() if k != 'modality'}
    return document


def approval_index(audit: Path) -> dict[str, dict]:
    """Match final decisions, promotion hashes and superseding amendment receipts."""
    proofs = {}
    decisions = {sha256(p): p for p in (audit / 'reviews').rglob('*.json')
                 if load(p).get('review_state') == 'final'}
    for receipt in sorted((audit / 'promotions').rglob('*.json')):
        record = load(receipt)
        if not record.get('artifacts'):
            continue
        decision_path = decisions.get(record['decision_sha256'])
        if decision_path is None:
            raise ValueError(f'No hash-matching final decision: {receipt}')
        decision = load(decision_path)
        if decision.get('overall_disposition') not in {'accepted', 'partially_accepted'}:
            raise ValueError(f'Unaccepted promotion: {receipt}')
        if decision.get('proposal_sha256', record['proposal_sha256']) != record['proposal_sha256']:
            raise ValueError(f'Proposal/decision mismatch: {receipt}')
        for item in record['artifacts']:
            if ('proposal_sha256' not in decision
                    and decision.get('candidate_sha256', {}).get(item['task']) != item['sha256']):
                raise ValueError(f'Final candidate hash differs from promotion: {receipt}')
            proofs[item['sha256']] = {
                'basis': 'promotion_with_final_decision',
                'receipt_sha256': sha256(receipt), 'decision_sha256': sha256(decision_path),
                'decision_disposition': decision['overall_disposition'],
                'decision_id': record['decision_id'], 'proposal_sha256': record['proposal_sha256']}
    receipt = audit / 'runs/connected-process-migration-001/final-approval-preview-003.json'
    record = load(receipt)
    if record['status'] != 'final' or record['scientific_disposition'] != 'approved':
        raise ValueError('Connected-process scientific approval is not final')
    pinned(audit / record['preview_manifest']['path'], record['preview_manifest']['sha256'])
    for protocol in record['protocols']:
        for item in protocol['artifacts']:
            proofs[item['candidate_sha256']] = {
                'basis': 'final_scientific_preview', 'receipt_sha256': sha256(receipt),
                'approval_id': record['approval_id'],
                'note': 'Scientific approval; the receipt itself did not authorize installation/publication.'}
    for receipt in sorted((audit / 'applications').glob('*/*/installation-receipt.json')):
        record = load(receipt)
        for name, key in [('decision.json', 'decision_sha256'), ('proposal.json', 'proposal_sha256'),
                          ('application/application-log.json', 'application_log_sha256')]:
            pinned(receipt.parent / name, record[key])
        decision = load(receipt.parent / 'decision.json')
        if (decision['review_state'] != 'final' or decision['overall_disposition'] != 'accepted'
                or decision['proposal_sha256'] != record['proposal_sha256']
                or any(d['disposition'] != 'accept' for d in decision['issue_decisions'])):
            raise ValueError(f'Amendment lacks accepted final decision: {receipt}')
        for item in record['files']:
            if '/ground_truth/' not in item['path']:
                continue
            name = Path(item['path']).name
            pinned(receipt.parent / 'baselines' / name, item['before_sha256'])
            pinned(receipt.parent / 'application/candidates' / name, item['after_sha256'])
            proofs[item['after_sha256']] = {
                'basis': 'final_human_amendment', 'receipt_sha256': sha256(receipt),
                'decision_id': decision['decision_id'], 'decision_sha256': record['decision_sha256'],
                'proposal_sha256': record['proposal_sha256'], 'before_sha256': item['before_sha256']}
    return proofs


def approval_for(path: Path, audit: Path, proofs: dict) -> dict:
    digest = sha256(path)
    if digest in proofs:
        return copy.deepcopy(proofs[digest])
    # Accept only the canonical vocabulary mapping already documented by policy.
    current = load(path)
    matches = []
    for candidate in (audit / 'applications' / path.parent.name).rglob('candidates/' + path.name):
        candidate_hash = sha256(candidate)
        if candidate_hash in proofs and modality_view(load(candidate)) == current:
            matches.append((candidate_hash, proofs[candidate_hash]))
    if not matches:
        # Stage a reviewable, unchanged record without certifying an ambiguous
        # generic label as a scientific alias. Never permit other differences.
        for candidate in sorted((audit / 'applications' / path.parent.name).rglob('candidates/' + path.name)):
            candidate_hash = sha256(candidate)
            if candidate_hash in proofs and without_modality(load(candidate)) == without_modality(current):
                return {'basis': 'unresolved_modality_refinement',
                        'approved_candidate_sha256': candidate_hash,
                        'prior_approval': proofs[candidate_hash],
                        'note': 'Only modality differs, but this is not a recognized alias. Requires review before publication; no canonical data were changed.'}
        raise ValueError(f'Unresolved scientific difference from approved content: {path}')
    digest, proof = sorted(matches, key=lambda x: x[0])[0]
    return {**proof, 'basis': 'approved_content_with_canonical_modality_alias',
            'approved_candidate_sha256': digest,
            'note': 'Only modality labels differ; sequence, chemistry and topology are unchanged.'}


def source_inventory(private: Path, protocol_ids: list[str]) -> dict:
    audit = private / 'ground_truth_audit'
    catalog_path = audit / 'catalogs/source-catalog.connected-process-2026-08-15.json'
    catalog = load(catalog_path)
    records = {pr['protocol_id']: pr for pr in catalog['protocols']}
    # Source manifests supplement provenance metadata; never use them to change labels.
    known_urls: dict[str, set[str]] = {}
    legacy: dict[str, dict[str, dict]] = {}
    for path in sorted((audit / 'manifests').rglob('*.json')):
        manifest = load(path)
        if 'protocol_id' not in manifest or 'sources' not in manifest:
            continue
        pid = manifest['protocol_id']
        for s in manifest['sources']:
            if s.get('sha256') and s.get('original_uri'):
                known_urls.setdefault(s['sha256'], set()).add(s['original_uri'])
            if s['role'] == 'legacy_curated_html' and s.get('path', '').endswith('.html'):
                legacy.setdefault(pid, {})[s['path']] = s
    for pr in records.values():
        for s in pr['sources']:
            if s.get('sha256') and s.get('original_uri'):
                known_urls.setdefault(s['sha256'], set()).add(s['original_uri'])
    files, legacy_files = [], []
    for pid in protocol_ids:
        pr = records[pid]
        related = sorted({s['original_uri'] for s in pr['sources'] if s.get('original_uri')})
        for relative, entry in sorted(legacy.get(pid, {}).items()):
            f = safe_file(private, relative)
            if not f.is_file():
                continue
            # These URLs are discovery aids at protocol level, not file-identity claims.
            urls = re.findall(r'href=["\'](https?://[^"\']+)', f.read_text())
            related.extend(u for u in urls if not any(h in u for h in ('github.com', 'github.io')))
            legacy_files.append({'protocol_id': pid, 'filename': f.name, 'sha256': sha256(f),
                                 'upstream_url': 'https://teichlab.github.io/scg_lib_structs/methods_html/' + f.name,
                                 'upstream_revision': None})
        texts = {}
        text_headers = {}
        rendition_path = audit / 'renditions' / pid / 'rendition_bundle.json'
        if rendition_path.is_file():
            for s in load(rendition_path)['sources']:
                for a in s.get('artifacts', []):
                    if a['kind'] == 'native_text':
                        f = safe_file(rendition_path.parent, a['path'])
                        pinned(f, a['sha256'])
                        text_headers[s['source_sha256']] = f.read_text()[:4500]
                        texts[s['source_sha256']] = {
                            'normalized_text_sha256': normalized_text_sha256(f.read_text()),
                            'extracted_text_sha256': a['sha256'],
                            'extraction_provenance_sha256': sha256(rendition_path)}
        detected_protocol_links = sorted(set(
            'https://doi.org/' + d.rstrip('.,;)') for header in text_headers.values()
            for d in re.findall(r'10\.\d{4,9}/[^\s<>]+', header)))
        for s in pr['sources']:
            if s['role'] != 'primary_evidence':
                continue
            relative = s['path'].removeprefix('protocols/').removeprefix('protocols_scg_all/')
            f = safe_file(scg_source_root(private), relative)
            digest = s.get('sha256')
            if digest:
                pinned(f, digest)
            urls = sorted(known_urls.get(digest, set()))
            header = text_headers.get(digest, '')
            detected_dois = sorted(set(d.rstrip('.,;)') for d in
                                       re.findall(r'10\.\d{4,9}/[^\s<>]+', header)))
            # Retain detected links separately: first-page DOIs can refer to
            # another article and are not automatically certified download URLs.
            # Version tokens are transcribed from saved metadata, never guessed from release date.
            version_tokens = re.findall(r'(?i)\b(?:rev(?:ision)?[ ._-]*[A-Z0-9]+|v\d+(?:\.\d+)+)\b',
                                        s.get('title', '') + ' ' + Path(relative).name)
            cover_versions = re.findall(r'\b(?:Rev[ .]+[A-Z]|Version\s+\d+(?:\.\d+)*)\b', header)
            document_numbers = sorted(set(re.findall(r'\bCG\d{5,6}\b', header)))
            all_versions = sorted(set(version_tokens + cover_versions))
            files.append({'protocol_id': pid, 'path': relative, 'sha256': digest,
                          'size_bytes': f.stat().st_size if f.is_file() else None,
                          'title': s.get('title', Path(relative).name),
                          'source_kind': s.get('source_kind', 'unclassified'),
                          'source_urls': urls, 'url_status': 'recorded' if urls else 'not_recorded',
                          'candidate_doi_links_from_text': ['https://doi.org/' + d for d in detected_dois],
                          'candidate_link_status': 'detected_in_first_4500_characters_not_independently_verified',
                          'related_protocol_links': sorted(set(related)),
                          'candidate_related_protocol_doi_links': detected_protocol_links,
                          'document_version': '; '.join(all_versions) or None,
                          'document_numbers': document_numbers,
                          'version_status': 'saved_metadata_or_native_text_header' if all_versions else 'not_recorded',
                          'redistributed': False, 'license': 'not_assessed_not_redistributed',
                          'available_in_frozen_bundle': bool(digest), **texts.get(digest, {})})
    return {'format_version': 1, 'documents_redistributed': False,
            'catalog_sha256': sha256(catalog_path),
            'text_normalization': 'UTF-8; Unicode NFKC; split/join all whitespace with one ASCII space; no trailing newline',
            'text_hash_limitation': 'Auxiliary fingerprint of existing native-text rendition; extraction-engine/layout changes may change it. Figures/tables require the original file SHA-256.',
            'link_limitation': 'Recorded links may be article landing pages. Related protocol links do not identify an individual file. No online availability check is implied.',
            'files': sorted(files, key=lambda s: s['path']),
            'legacy_curation_provenance': legacy_files}


ATTRIBUTION = '''# Attribution and scope

LibStructBench ground truth was adapted, audited and extended from
[scg_lib_structs](https://github.com/Teichlab/scg_lib_structs), with molecular
workflow records and human-adjudicated corrections. It is not an unchanged copy.
Credit the original curators and the LibStructBench authors when reusing it.

Upstream citation: Xi Chen, Patrick Roelli, Darío Hereñú, Pontus Höjer and Tim
Stuart. *Teichlab/scg_lib_structs: Release 26th Oct 2023*, version 20231026.
DOI: https://doi.org/10.5281/zenodo.10042390.
That archived release declares CC BY 4.0. This citation identifies the upstream
project, not the exact snapshot used for every annotation: the local HTML
snapshot has no recorded upstream commit. Its individual file hashes are
provided in SOURCE_MANIFEST.json; newer protocol records must not be described
as having come from the 2023 release.

See each protocol's source links in SOURCE_MANIFEST.json. Journal articles,
vendor guides and supplements have their own terms; this package does not
redistribute or license them. Audit packets, extracted text, page images,
raw trajectories, private reviewer records and agent outputs are excluded.
Hashes of approval records record provenance without releasing their contents.
The JSON annotations retain descriptive notes; packaging checks do not establish
copyright clearance of every phrase. No third-party endorsement is implied.
'''


def build_package(*, private: Path, repo: Path, protocol_ids: list[str],
                  repo_id: str, output: Path, proofs: dict) -> dict:
    if output.exists():
        raise ValueError(f'Refusing to overwrite package: {output}')
    if not protocol_ids or len(protocol_ids) != len(set(protocol_ids)):
        raise ValueError('Protocol selection must be nonempty and unique')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo_id):
        raise ValueError('Invalid repository ID')
    rows, files = [], []
    # All scientific/provenance validation happens before writing the package.
    for pid in protocol_ids:
        if not re.fullmatch(r'[a-z0-9_]+', pid):
            raise ValueError(f'Unsafe protocol ID: {pid}')
        directory = private / 'ground_truth' / pid
        docs = {t: load(directory / name) for t, name in GROUNDTRUTH_FILENAMES.items()}
        legacy = any('final_state_ids' in w for w in docs['T3']['workflows'])
        view = migrate_connected_process_bundle(docs) if legacy else docs
        validate_groundtruth_bundle(view, protocol_id=pid, schema_root=repo / 'schemas')
        rows.append({'protocol_id': pid, 'protocol_name': docs['T1']['protocol_name'],
                     'workflow_contract': 'legacy_final_state_ids' if legacy else 'connected_final_outputs',
                     'validation': 'passed_after_in_memory_compatibility_view' if legacy else 'passed',
                     'validation_transform': 'migrate_connected_process_bundle' if legacy else 'none'})
        for task, name in GROUNDTRUTH_FILENAMES.items():
            f = directory / name
            files.append({'path': f'{pid}/{name}', 'task': task, 'protocol_id': pid,
                          'sha256': sha256(f), 'size_bytes': f.stat().st_size,
                          'approval': approval_for(f, private / 'ground_truth_audit', proofs)})
    sources = source_inventory(private, protocol_ids)
    output.mkdir(parents=True)
    for entry in files:
        src = private / 'ground_truth' / entry['path']
        pinned(src, entry['sha256'])
        dest = safe_file(output, entry['path'])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    fingerprint = hashlib.sha256(json.dumps([(r['path'], r['sha256']) for r in files],
                                           separators=(',', ':')).encode()).hexdigest()
    legacy_count = sum(r['workflow_contract'] == 'legacy_final_state_ids' for r in rows)
    summary = {'protocol_count': len(rows), 'groundtruth_file_count': len(files),
               'legacy_workflow_protocol_count': legacy_count,
               'source_file_count': len(sources['files']),
               'sources_without_recorded_url': sum(not s['source_urls'] for s in sources['files']),
               'sources_without_version_label': sum(s['document_version'] is None for s in sources['files']),
               'unavailable_source_records': sum(not s['available_in_frozen_bundle'] for s in sources['files']),
               'approval_basis_counts': dict(Counter(f['approval']['basis'] for f in files))}
    summary['approval_review_required'] = [f['path'] for f in files
                                           if f['approval']['basis'] == 'unresolved_modality_refinement']
    write(output / 'MANIFEST.json', {'format_version': 1, 'repository_role': 'verifier_groundtruth',
                                   'repo_id': repo_id, 'groundtruth_content_sha256': fingerprint,
                                   'content_policy': 'canonical_JSON_bytes_unchanged_sources_not_redistributed',
                                   'files': files, **summary})
    write(output / 'PROTOCOLS.json', {'protocols': rows})
    write(output / 'SOURCE_MANIFEST.json', sources)
    schema_names = ['final_library_groundtruth.schema.json', 'oligo_groundtruth.schema.json',
                    'library_generation_workflow.schema.json']
    for name in schema_names:
        dest = output / 'schemas' / name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(repo / 'schemas/groundtruth' / name, dest)
    shutil.copyfile(Path(__file__).with_name('verify_groundtruth_upload.py'), output / 'verify_release.py')
    write(output / 'BUILD_INFO.json', {
        'packager_sha256': sha256(Path(__file__)), 'verifier_compatibility': '4.7.0',
        'compatibility_transform_sha256': sha256(repo / 'src/libstruct_bench/audit/connected_process.py'),
        'validation_sha256': sha256(repo / 'src/libstruct_bench/audit/groundtruth.py'),
        'scope': 'Packaging and approval-hash correspondence; not a fresh scientific audit or new scoring run.'})
    (output / 'ATTRIBUTION.md').write_text(ATTRIBUTION)
    (output / 'LICENSE.md').write_text('''# Creative Commons Attribution 4.0 International

The LibStructBench annotations and accompanying dataset documentation are
prepared for release under CC BY 4.0:
https://creativecommons.org/licenses/by/4.0/legalcode

Preserve attribution, link the license and indicate changes. See ATTRIBUTION.md
for upstream credits and limitations. This grant covers only rights held by
the contributing licensors; it does not grant rights to the third-party
documents identified by the source manifest. Those documents are not included.
''')
    (output / '.gitattributes').write_text('*.json -text\n*.md text eol=lf\n*.py text eol=lf\n')
    (output / 'README.md').write_text(f'''---
language:
- en
license: cc-by-4.0
pretty_name: LibStructBench ground truth ({len(rows)} protocols)
size_categories:
- n<1K
tags:
- biology
- genomics
- benchmark
- libstructbench
---

# LibStructBench ground truth — {len(rows)} protocols

Intended dataset: https://huggingface.co/datasets/{repo_id}

This package contains {len(files)} canonical ground-truth JSON files for
{len(rows)} protocols. Each protocol directory contains:

| File | Task |
|---|---|
| `groundtruth_final_lib_struct.json` | T1: final library structures |
| `groundtruth_oligos.json` | T2: oligo inventory and support annotations |
| `groundtruth_library_generation_workflow.json` | T3: molecular states and transitions |

JSON files are byte-identical to the local canonical records at export time.
`MANIFEST.json` pins each file and its approval provenance; the reference
fingerprint is `{fingerprint}`. This release does not change verifier weights,
predictions, historical scores or the historically selected C30 checkpoint.
Keep these labels out of agent-visible inputs for benchmark evaluation.

## Sources and reproducibility

Source documents are not redistributed. `SOURCE_MANIFEST.json` lists the
{len(sources['files'])} source records, recorded source links, available
document-version labels and exact SHA-256 hashes. Where available, it also
includes an auxiliary normalized-text hash of the existing native-text
rendition. Related protocol links are discovery aids, not exact-file links.
There are {summary['sources_without_recorded_url']} records without a recorded
file-specific URL and {summary['sources_without_version_label']} without an
explicit revision label; null fields mark these gaps. A hash pins the held
copy but does not guarantee that an identical copy remains obtainable online.
{summary['unavailable_source_records']} source records were unavailable and have
no file hash; these are provenance entries, not scored new evidence.

Obtain documents from the original providers under their terms. Arrange
user-supplied files as `protocol_id/filename`, then verify:

```bash
python verify_release.py
python verify_release.py --source-root /path/to/user-supplied-documents
```

The source check requires exact bytes. Different watermarks or revisions will
fail it. `--text-copy extracted.txt` prints a separate normalized-text
fingerprint; a text match alone does not establish equivalent figures/tables.

## Workflow representation

`PROTOCOLS.json` lists each record's contract. {legacy_count} protocols retain
the historical `modality` + `final_state_ids` workflow representation. The
remaining records use `final_outputs`. The exported JSON is unchanged.
The current schemas in `schemas/` describe the latter contract: validate legacy
records using the repository's `migrate_connected_process_bundle` in memory
before linked validation/scoring, as in the frozen 4.7.0 analyses. See
`BUILD_INFO.json` for the implementation hash. Do not interpret a legacy raw
schema failure as a failed scientific annotation or silently rewrite this release.

## Scope and limitations

This is a reference-data package, not agent inference results or a claim that
all protocol/source metadata is complete. The 20-protocol subset and the full
73-protocol collection share identical JSON for overlapping records. They are
different evaluation selections, not new training/test splits. Scientific
approval hashes are checked; private decisions are not redistributed. No new
independent scientific audit or inter-rater estimate is claimed.
Approval correspondence needs additional review for {len(summary['approval_review_required'])}
files: {', '.join(summary['approval_review_required']) or 'none'}.
Where present, these are unchanged canonical files with modality refinements
beyond the recognized alias map; staging does not approve those differences.

## Attribution and license

See [ATTRIBUTION.md](ATTRIBUTION.md) and [LICENSE.md](LICENSE.md).
The source PDFs, supplements, extracted text, audit packets and raw logs are
excluded. The CC BY 4.0 annotation license does not license the linked documents.
''')
    write(output / 'CHECKSUMS.json', {'files': [
        {'path': p.relative_to(output).as_posix(), 'sha256': sha256(p)}
        for p in sorted(output.rglob('*')) if p.is_file()]})
    result = verify(output)
    if not result['ok']:
        raise ValueError(result)
    return {**summary, 'repo_id': repo_id, 'groundtruth_content_sha256': fingerprint,
            'integrity': result}


def prepare(*, private: Path, repo: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError(f'Refusing to overwrite staging directory: {output}')
    selected = [p['protocol_id'] for p in load(repo / 'paper/benchmarks/tasks2_3/protocols.json')['protocols']]
    full = sorted(p.name for p in (private / 'ground_truth').iterdir()
                  if p.is_dir() and (p / GROUNDTRUTH_FILENAMES['T1']).is_file())
    if len(selected) != 20 or len(full) != 73 or not set(selected) <= set(full):
        raise ValueError('Unexpected panel membership; review before preparing another release')
    proofs = approval_index(private / 'ground_truth_audit')
    results = {}
    for folder, ids, repo_id in [('scg-v1-upload', selected, 'sequencing/scg-protocols-v1-groundtruth'),
                                ('scg-all-upload', full, 'sequencing/scg-protocols-all-groundtruth')]:
        results[folder] = build_package(private=private, repo=repo, protocol_ids=ids,
                                       repo_id=repo_id, output=output / folder / 'groundtruth', proofs=proofs)
        (output / folder / 'UPLOAD.md').write_text(f'''# Upload target

Upload **only `groundtruth/`**, as the root of dataset `{repo_id}`.
The surrounding staging directory may contain private source files or receipts.
Do not upload the parent directory or a pre-existing `protocols/` folder.

```bash
python groundtruth/verify_release.py
hf upload {repo_id} groundtruth . --repo-type dataset
```

This command is provided for a later, deliberate publication step. No upload
has been performed by the packager. Review missing source metadata and license
scope in the dataset card before public publication. The repository web URL is
https://huggingface.co/datasets/{repo_id}
''')
    for pid in selected:
        for name in GROUNDTRUTH_FILENAMES.values():
            if sha256(output / 'scg-v1-upload/groundtruth' / pid / name) != sha256(output / 'scg-all-upload/groundtruth' / pid / name):
                raise ValueError('Subset/full-set overlap differs')
    write(output / 'PREPARATION_REPORT.json', results)
    return results
