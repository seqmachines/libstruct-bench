"""Compile only the proposed tables in a temporary NeurIPS-style document."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

BASE=Path(__file__).resolve().parent
OUT=BASE/'results_v1/publication_tables_v1'
PAPER=Path('external/manuscript')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    manifest=json.loads((OUT/'verification.json').read_text())
    assert sha(PAPER/'main.tex')==manifest['manuscript_sha256']
    for f,h in manifest['outputs_sha256'].items():assert sha(OUT/f)==h
    work=Path(tempfile.mkdtemp(prefix='libstruct-name-only-tables-'))
    shutil.copy2(PAPER/'neurips_2026.sty',work/'neurips_2026.sty')
    old=BASE.parent/'camera_ready_v4_7_0/sensitivity/publication_20261017/compile_report.json'
    fonts={}
    if old.exists():
        prior=Path(json.loads(old.read_text())['temporary_directory'])
        if prior.exists():
            for p in prior.iterdir():
                if p.suffix in {'.tfm','.vf','.pfb','.enc','.map','.otf'}:
                    shutil.copy2(p,work/p.name);fonts[p.name]=sha(p)
    names=['summary','task1','gpt','claude','gemini']
    for name in names:
        filename=f'table_name_only_{name}.tex';text=(OUT/filename).read_text()
        text=text.replace(r'\begin{tabular}',r'\begin{lrbox}{\controltablebox}\begin{tabular}')
        text=text.replace(r'\end{tabular}',r'\end{tabular}\end{lrbox}'+'\n'+
                          r'\typeout{CONTROL_TABLE_WIDTH:'+name+r'=\the\wd\controltablebox}'+
                          '\n'+r'\usebox{\controltablebox}')
        (work/filename).write_text(text)
    preamble=(PAPER/'main.tex').read_text().split(r'\title{',1)[0]
    body='\n'.join([r'\newsavebox{\controltablebox}',r'\begin{document}',
        r'\typeout{CONTROL_TEXT_WIDTH=\the\textwidth}',r'\appendix',
        r'\renewcommand{\thetable}{S\arabic{table}}']+
        [r'\input{table_name_only_'+n+r'.tex}\clearpage' for n in names]+[r'\end{document}'])
    (work/'check.tex').write_text(preamble+body)
    cmd=['/opt/homebrew/bin/tectonic','--only-cached','--keep-logs','--keep-intermediates','check.tex']
    result=subprocess.run(cmd,cwd=work,capture_output=True,text=True)
    (OUT/'compile.log').write_text(result.stdout+result.stderr)
    log=(work/'check.log').read_text() if (work/'check.log').exists() else ''
    report=dict(command=cmd,temporary_directory=str(work),returncode=result.returncode,
        errors=re.findall(r'^!.*',log,re.M),overfull_boxes=re.findall(r'Overfull .+',log),
        unresolved_references=re.findall(r'Reference .+ undefined',log),
        natural_table_width_pt={n:float(v) for n,v in re.findall(r'CONTROL_TABLE_WIDTH:(\w+)=(\d+\.\d+)pt',log)},
        text_width_pt=[float(v) for v in re.findall(r'CONTROL_TEXT_WIDTH=(\d+\.\d+)pt',log)],
        manuscript_unchanged=sha(PAPER/'main.tex')==manifest['manuscript_sha256'],
        font_resources=fonts,scope='Temporary compilation of five tables only; main.tex is not edited.')
    if result.returncode==0:
        import fitz
        pdf=fitz.open(work/'check.pdf');report['pages']=len(pdf);report['previews']=[]
        for i,page in enumerate(pdf):
            path=work/f'page-{i+1}.png'
            page.get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(path)
            report['previews'].append(str(path))
    (OUT/'compile_verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='font_resources'},indent=2))
    assert result.returncode==0 and not report['errors'] and not report['overfull_boxes'] and not report['unresolved_references']
    assert len(report['natural_table_width_pt'])==5
    assert max(report['natural_table_width_pt'].values())<=report['text_width_pt'][0]
    assert report['manuscript_unchanged']


if __name__=='__main__':main()
