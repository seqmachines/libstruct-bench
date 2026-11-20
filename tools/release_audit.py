"""Scan the public working tree and reachable Git history without exposing secrets.

This is a focused pattern scan, not a guarantee that every credential is detected.
No files or Git objects are removed. Run before publishing and review findings.
"""
import argparse
import gzip
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

PATTERNS = {
    'provider_key': re.compile(rb'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{24,}'),
    'github_token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'aws_access_key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'google_api_key': re.compile(rb'\bAIza[A-Za-z0-9_-]{35}\b'),
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}
LOCAL = re.compile(rb'/(?:Users|home)/[A-Za-z][A-Za-z0-9_.-]+/')
PRIVATE_PARTS = {'protocol_sources', 'groundtruth', 'ground_truth'}
DOCUMENT_SUFFIXES = {'.pdf', '.xlsx', '.xls', '.docx', '.doc'}


def is_document(name, data):
    if Path(name).suffix.lower() in DOCUMENT_SUFFIXES or data.startswith(
        (b'%PDF-', bytes.fromhex('d0cf11e0a1b11ae1'))
    ):
        return True
    if data.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                return any(p in archive.namelist() for p in ('word/document.xml', 'xl/workbook.xml'))
        except zipfile.BadZipFile:
            pass
    return False


def scan(data):
    # Locations and categories only: never serialize matched credential values.
    return [dict(kind=k,line=data[:m.start()].count(b'\n')+1) for k,pattern in PATTERNS.items() for m in pattern.finditer(data)]


def git(*args):
    return subprocess.check_output(['git',*args])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--history',action='store_true')
    args=ap.parse_args()
    paths=set(git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0'))-{''}
    rows=[];local=[];private=[];large=[];count=0
    for name in sorted(paths):
        p=Path(name)
        if not p.is_file() or any(part in {'.release-archive','.git'} for part in p.parts):continue
        count+=1;data=p.read_bytes();size=len(data)
        if p.suffix == '.gz':
            data=gzip.decompress(data)
        rows.extend(dict(path=name,**f) for f in scan(data))
        if LOCAL.search(data):local.append(name)
        if is_document(name, data) or (PRIVATE_PARTS.intersection(p.parts) and not name.startswith('schemas/')):private.append(name)
        if size>10_000_000:large.append(dict(path=name,bytes=size))
    result=dict(working_tree_files=count,credential_candidates=rows,local_path_files=local,
                private_payload_paths=private,large_files=large,
                limitation='Pattern-based audit; review false positives and historical content before publishing.')
    if args.history:
        objects=git('rev-list','--objects','--all').decode().splitlines()
        proc=subprocess.Popen(['git','cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
        hist=[];documents=[];local_blobs=0;blobs=0
        for item in objects:
            oid,_,name=item.partition(' ')
            proc.stdin.write((oid+'\n').encode());proc.stdin.flush()
            header=proc.stdout.readline().decode().split()
            if len(header)!=3:continue
            data=proc.stdout.read(int(header[2]));proc.stdout.read(1)
            if header[1]!='blob':continue
            blobs+=1
            hist.extend(dict(object_id=oid,representative_path=name,**f) for f in scan(data))
            local_blobs+=bool(LOCAL.search(data))
            if is_document(name, data):
                documents.append(dict(object_id=oid,representative_path=name,bytes=len(data)))
        proc.stdin.close();proc.wait()
        result['history']=dict(blobs_scanned=blobs,credential_candidates=hist,
            source_document_objects=documents,blobs_with_local_paths=local_blobs,
            note='All locally reachable refs. Document detection checks filenames and common document formats.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in result.items() if k!='history'},indent=2))
    if args.history:print('History:',result['history']['blobs_scanned'],'blobs;',len(hist),'credential candidates;',len(documents),'source document objects')


if __name__=='__main__':main()
