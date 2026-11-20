"""Compile only temporary manuscript copies; never write in the paper directory."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
import tarfile

OUT = Path(__file__).resolve().parent
PAPER = Path('external/manuscript')

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    source = PAPER / 'main.tex'
    digest = sha(source)
    text = source.read_text()
    assert r'\label{app:metric-sensitivity}' not in text
    root = Path(tempfile.mkdtemp(prefix='libstruct-sensitivity-latex-', dir='/private/tmp'))
    work = root / 'paper'
    shutil.copytree(PAPER, work, ignore=shutil.ignore_patterns('main.pdf','main.log','*.aux','*.bbl','*.blg','*.out','*.synctex.gz'))
    # Supply missing local TeX font resources only inside the temporary build.
    # This does not change manuscript typography, source, or any installed files.
    font_zips = [Path('/private/tmp/libstruct-sensitivity-'+name+'.zip') for name in ['lm','amsfonts']]
    for font_zip in font_zips:
      if font_zip.exists():
        with zipfile.ZipFile(font_zip) as bundle:
            for entry in bundle.namelist():
                if Path(entry).suffix in {'.tfm','.pfb','.enc','.map'}:
                    (work / Path(entry).name).write_bytes(bundle.read(entry))
    cm_archive = Path('/private/tmp/libstruct-sensitivity-cm.tar.xz')
    if cm_archive.exists():
        with tarfile.open(cm_archive) as bundle:
            for entry in bundle.getmembers():
                if entry.isfile() and Path(entry.name).suffix in {'.tfm','.pfb','.enc','.map'}:
                    (work / Path(entry.name).name).write_bytes(bundle.extractfile(entry).read())
    for p in OUT.glob('figS_sensitivity_*.pdf'):
        shutil.copy2(p, work / 'figures' / p.name)
    methods = (OUT / 'methods_addition.tex').read_text()
    results = (OUT / 'results_addition.tex').read_text()
    appendix = (OUT / 'appendix_addition.tex').read_text()
    for marker in [r'\subsection{Ground-truth construction, audit, and adjudication}',r'\section{Discussion}',r'\end{document}']:
        assert text.count(marker) == 1, marker
    candidate = text.replace(r'\subsection{Ground-truth construction, audit, and adjudication}', methods+'\n'+r'\subsection{Ground-truth construction, audit, and adjudication}')
    candidate = candidate.replace(r'\section{Discussion}',results+'\n'+r'\section{Discussion}')
    candidate = candidate.replace(r'\end{document}',appendix+'\n'+r'\end{document}')
    (work / 'main_original.tex').write_text(text)
    (work / 'main.tex').write_text(candidate)
    pattern = r'\\includegraphics(?:\[[^\]]*\])?\s*\{([^}]+)\}'
    missing = []
    def replace_missing(match):
        rel = match.group(1).strip()
        if (work / rel).exists(): return match.group(0)
        missing.append(rel)
        escaped = rel.replace('_',r'\_')
        return r'\fbox{\parbox[c][0.7in][c]{0.90\linewidth}{\centering\footnotesize Missing pre-existing figure (layout check only):\par\texttt{'+escaped+'}}}'
    layout = re.sub(pattern,replace_missing,candidate)
    (work / 'main_layout_check.tex').write_text(layout)
    labels = re.findall(r'\\label\{([^}]+)\}',candidate)
    assert len(labels) == len(set(labels)), 'Duplicate manuscript labels'
    newrefs = set(re.findall(r'\\ref\{([^}]+)\}',methods+results+appendix))
    assert newrefs <= set(labels), newrefs-set(labels)
    report = {'paper_source':str(source),'paper_source_sha256':digest,
              'temporary_directory':str(work),'missing_preexisting_figures':missing,
              'new_references_resolve_statically':True,'commands':[]}
    report['temporary_font_dependencies']=[{'file':str(z),'sha256':sha(z)} for z in font_zips if z.exists()]
    if cm_archive.exists():report['temporary_font_dependencies'].append({'file':str(cm_archive),'sha256':sha(cm_archive)})
    (OUT / 'compile_report.json').write_text(json.dumps(report,indent=2)+'\n')
    names = ['main_original.tex','main.tex','main_layout_check.tex'] if missing else ['main_original.tex','main.tex']
    for name in names:
        command = ['/opt/homebrew/bin/tectonic','--keep-logs','--keep-intermediates',name]
        print('Compiling temporary',name,flush=True)
        result = subprocess.run(command,cwd=work,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        logfile = OUT / (Path(name).stem+'_compile.log')
        logfile.write_text(result.stdout)
        print('\n'.join(line for line in result.stdout.splitlines() if any(token in line for token in ['error:','warning:','! ','Writing','Output written'])),flush=True)
        report['commands'].append({'input':name,'command':command,'returncode':result.returncode,
            'console_log':str(logfile),'tex_log':str(work/Path(name).with_suffix('.log')),
            'pdf':str(work/Path(name).with_suffix('.pdf'))})
        (OUT / 'compile_report.json').write_text(json.dumps(report,indent=2)+'\n')
    assert sha(source)==digest
    report['main_tex_unchanged']=True
    (OUT / 'compile_report.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
