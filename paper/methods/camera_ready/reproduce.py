"""Rebuild into a new sibling directory using only hash-verified local inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    here=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New, nonexistent sibling directory under analysis/paper_results')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    dest=args.output.resolve()
    assert dest.parent==here.parent and not dest.exists(), 'A new sibling directory is required; existing results are never overwritten.'
    assert args.workers>0
    lock=json.loads((here/'reproducibility_manifest.json').read_text())
    for group in ['protected_inputs','additional_reporting_inputs']:
        for path,expected in lock[group].items():
            assert digest(path)==expected, f'Input changed: {path}'
    for path,expected in lock['artifacts'].items():
        assert digest(here/path)==expected, f'Integration artifact changed: {path}'
    dest.mkdir();(dest/'sensitivity').mkdir()
    for source in list(here.glob('*.py'))+list(here.glob('*.R'))+[here/'README.md']:
        shutil.copy2(source,dest/source.name)
    for source in (here/'sensitivity').glob('*.py'):
        shutil.copy2(source,dest/'sensitivity'/source.name)
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OPENBLAS_NUM_THREADS':'1'}
    commands=[['freeze.py'],['sensitivity/run.py','--workers',str(args.workers)],['score_extras.py'],['assemble.py'],['tables.py'],['sensitivity/analyze.py'],['figures.py'],['sensitivity/figures.py'],['write_reports.py'],['finalize.py']]
    for command in commands:
        print('Running',*command,flush=True)
        subprocess.run([sys.executable,*command],cwd=dest,env=env,check=True)
    new=json.loads((dest/'results_identity.json').read_text())
    assert new['sha256']==lock['authoritative_table']['sha256'], 'Scientific table bytes differ; investigate environment/version changes.'
    print('Reproduced identical authoritative results.csv; plots may have different PDF creation metadata.',flush=True)


if __name__=='__main__':main()
