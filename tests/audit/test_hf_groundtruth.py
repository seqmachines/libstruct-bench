from pathlib import Path
import shutil
import sys

import pytest

from libstruct_bench.audit.hf_groundtruth import (
    approval_for, build_package, load, pinned, write,
)
from libstruct_bench.audit.verify_groundtruth_upload import (
    normalized_text_sha256, safe_file, sha256, verify,
)
from libstruct_bench.libgen.staging import GROUNDTRUTH_FILENAMES
from tests.audit.test_connected_process import _preview_bundle


REPO = Path(__file__).resolve().parents[2]


def fixture_package(tmp_path):
    private = tmp_path / 'private'
    truth = private / 'ground_truth/example'
    proofs = {}
    for (task, name), doc in zip(GROUNDTRUTH_FILENAMES.items(), _preview_bundle('example')):
        write(truth / name, doc)
        proofs[sha256(truth / name)] = {'basis': 'synthetic_test_approved'}
    # These deliberately private files must never be included in the package.
    (truth / 'audit_packet.txt').write_text('private source excerpt')
    source = private / 'protocols/example/source.pdf'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'not a real PDF: private source bytes')
    write(private / 'ground_truth_audit/catalogs/source-catalog.connected-process-2026-08-15.json', {
        'protocols': [{'protocol_id': 'example', 'sources': [{
            'role': 'primary_evidence', 'path': 'protocols/example/source.pdf',
            'sha256': sha256(source), 'original_uri': 'https://example.org/protocol',
            'title': 'Synthetic protocol v1.1',
        }]}]})
    output = tmp_path / 'release'
    result = build_package(private=private, repo=REPO, protocol_ids=['example'],
                           repo_id='example/groundtruth', output=output, proofs=proofs)
    return private, output, result, proofs


def test_only_allowlisted_files_exported_and_truth_bytes_preserved(tmp_path):
    private, output, result, _ = fixture_package(tmp_path)
    assert result['groundtruth_file_count'] == 3
    assert result['legacy_workflow_protocol_count'] == 1
    for name in GROUNDTRUTH_FILENAMES.values():
        assert (output / 'example' / name).read_bytes() == (private / 'ground_truth/example' / name).read_bytes()
    assert not list(output.rglob('*.pdf'))
    assert not list(output.rglob('*packet*'))
    assert verify(output, sources=private / 'protocols')['ok']
    assert load(output / 'SOURCE_MANIFEST.json')['files'][0]['redistributed'] is False


def test_source_hash_changes_fail_closed(tmp_path):
    private, output, _, proofs = fixture_package(tmp_path)
    (private / 'protocols/example/source.pdf').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Stale provenance'):
        build_package(private=private, repo=REPO, protocol_ids=['example'],
                      repo_id='example/gt', output=tmp_path / 'new_release', proofs=proofs)
    assert not (tmp_path / 'new_release').exists()


def test_approval_rejects_scientific_change_and_flags_generic_modality(tmp_path):
    audit = tmp_path / 'audit'
    candidate = audit / 'applications/example/iteration/candidates/truth.json'
    canonical = tmp_path / 'example/truth.json'
    write(candidate, {'modality': 'RNA', 'sequence': 'ACGT'})
    proofs = {sha256(candidate): {'basis': 'test_approval'}}
    write(canonical, {'modality': 'gene expression', 'sequence': 'ACGT'})
    assert approval_for(canonical, audit, proofs)['basis'] == 'approved_content_with_canonical_modality_alias'
    write(canonical, {'modality': 'gene expression', 'sequence': 'ACGA'})
    with pytest.raises(ValueError, match='Unresolved scientific'):
        approval_for(canonical, audit, proofs)
    write(candidate, {'modality': 'library', 'sequence': 'ACGT'})
    proofs = {sha256(candidate): {'basis': 'test_approval'}}
    write(canonical, {'modality': 'gene expression', 'sequence': 'ACGT'})
    assert approval_for(canonical, audit, proofs)['basis'] == 'unresolved_modality_refinement'


def test_verify_detects_extra_files_and_changed_data(tmp_path):
    _, output, _, _ = fixture_package(tmp_path)
    (output / 'leaked.pdf').write_bytes(b'private')
    assert 'Unexpected file: leaked.pdf' in verify(output)['errors']
    (output / 'README.md').write_text('modified')
    assert 'Missing/changed release file: README.md' in verify(output)['errors']


def test_no_overwrite_or_unsafe_paths(tmp_path):
    private, output, _, proofs = fixture_package(tmp_path)
    with pytest.raises(ValueError, match='overwrite'):
        build_package(private=private, repo=REPO, protocol_ids=['example'],
                      repo_id='example/gt', output=output, proofs=proofs)
    for path in ['../source.pdf', '/etc/passwd']:
        with pytest.raises(ValueError, match='Unsafe'):
            safe_file(tmp_path, path)
    (tmp_path / 'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink'):
        safe_file(tmp_path, 'link')


def test_text_fingerprint_is_auxiliary_and_deterministic():
    assert normalized_text_sha256('A\nＢ  C') == normalized_text_sha256('A B C')
    assert normalized_text_sha256('A C B') != normalized_text_sha256('A B C')


def test_installer_preserves_canonical_sources_and_backs_up_prior_package(tmp_path, monkeypatch):
    from libstruct_bench.cli.install_groundtruth_uploads import main
    private, output, _, _ = fixture_package(tmp_path)
    staging = tmp_path / 'prepared'
    for folder in ['scg-v1-upload', 'scg-all-upload']:
        shutil.copytree(output, staging / folder / 'groundtruth')
    old = private / 'scg-v1-upload/groundtruth'
    old.mkdir(parents=True)
    (old / 'old.json').write_text('old bytes')
    old_sources = private / 'scg-v1-upload/protocols'
    old_sources.mkdir()
    (old_sources / 'private.pdf').write_bytes(b'do not publish or change')
    before = {p: sha256(p) for p in (private / 'ground_truth').rglob('*') if p.is_file()}
    monkeypatch.setattr(sys, 'argv', ['install', '--staging', str(staging), '--private-root', str(private)])
    assert main() == 0
    assert all(sha256(p) == h for p, h in before.items())
    assert (old_sources / 'private.pdf').read_bytes() == b'do not publish or change'
    backups = list((private / 'upload-preparation-backups').glob('*/scg-v1-upload/groundtruth/old.json'))
    assert len(backups) == 1 and backups[0].read_text() == 'old bytes'
    assert verify(private / 'scg-v1-upload/groundtruth')['ok']


def test_renamed_primary_source_root_preserves_frozen_hashes(tmp_path):
    private, old, _, proofs = fixture_package(tmp_path)
    root = private / 'protocols_scg_all'
    (private / 'protocols').rename(root)
    new = tmp_path / 'new-package'
    build_package(private=private, repo=REPO, protocol_ids=['example'],
                  repo_id='example/gt', output=new, proofs=proofs)
    assert verify(new, sources=root)['ok']
    assert load(old / 'SOURCE_MANIFEST.json')['files'] == load(new / 'SOURCE_MANIFEST.json')['files']
