"""One readable multi-page table for all protocols, models, and tasks.

Formatting only; source tables and main.tex remain unchanged.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from finalize_tables import DATA, PAPER, METRICS, LABELS, SYSTEMS, PROTOCOLS, read, num, center, row, sha, close, mean

OUT=DATA/'combined_table_v1'
FILENAME='table_name_only_all_tasks.tex'


def make_row(cells, keep=False):
    return row(cells)+('*' if keep else '')


def main():
    if OUT.exists():
        previous=json.loads((OUT/'verification.json').read_text())
        assert sha(OUT/FILENAME)==previous['tex_sha256'], 'Combined table changed outside this builder.'
    protected={str(p):sha(p) for p in DATA.rglob('*') if p.is_file() and not p.is_relative_to(OUT)}
    protected[str(PAPER/'main.tex')]=sha(PAPER/'main.tex')
    prior=json.loads((DATA/'publication_tables_v1/verification.json').read_text())
    for filename,h in prior['inputs_sha256'].items():assert sha(DATA/filename)==h
    native={(r['system'],r['protocol_id'],r['metric']):r for r in read('per_protocol.csv')}
    t1={(r['model_family'],r['protocol_id']):r for r in read('task1_per_protocol.csv')}
    summary={(r['system'],r['metric']):r for r in read('summary.csv')
             if r['policy']=='valid_complete_pairs' and r['baseline']=='bundle_n3'}
    t1summary={r['model']:r for r in read('task1_summary.csv')}
    summary_text=(DATA/'summary.txt').read_text()
    # Recheck the exact aggregate sources used in the combined footer.
    for system,model,display,_ in SYSTEMS:
        for metric,label in zip(METRICS,LABELS):
            a=summary[system,metric]
            pairs=[v for (s,p,m),v in native.items() if s==system and m==metric
                   and v['eligible']=='True' and v['valid_pair']=='True']
            close(mean([float(v['bundle_n3']) for v in pairs]),a['bundle_mean'])
            close(mean([float(v['name_only']) for v in pairs]),a['name_only_mean'])
            prefix=f"{display} | {label} | {a['n_protocols']} | {float(a['bundle_mean']):.3f} | {float(a['name_only_mean']):.3f} | {float(a['name_minus_bundle']):+.3f} ["
            assert prefix in summary_text
        a=t1summary[model]
        prefix=f"{model} | {float(a['pdf_mean']):.3f} | {float(a['name_only_mean']):.3f} | {float(a['difference']):+.3f} | {a['pdf_exact']} | {a['name_only_exact']}"
        assert prefix in summary_text
    OUT.mkdir(exist_ok=True)
    caption=(r'\textbf{Document-supplied versus protocol-name-only reconstruction across Tasks~1--3.} '
        r'D and N denote document-supplied and name-only scores. Task~1 uses separate API executions (one per condition); '
        r'Tasks~2--3 use native agents (GPT/Codex, Claude Code, Gemini/Antigravity), with D averaging three bundle executions and N using one execution. '
        r'Task~1 reports sequence similarity; Tasks~2--3 report F1. Macro-scores use valid pairs: Task~1 $n=20$; '
        r'Task~2 $n=19$ and Task~3 $n=20$ (Claude: 18 and 19). '
        r'ddSEQ is excluded only from Task~2 (--, $N_{\mathrm{req}}=0$). '
        r'F marks Claude\textquotesingle s SHARE-seq output-token-limit failure, excluded from both native-condition means. '
        r'$\Delta=\mathrm{N}-\mathrm{D}$ is computed before rounding. Exact counts apply only to Task~1.').replace(r'Claude\textquotesingle s',"Claude's")
    header=[r'\toprule',
        row(['Protocol','Model',r'\multicolumn{2}{c}{Task~1 (API)}',r'\multicolumn{2}{c}{Task~2 (native)}',r'\multicolumn{6}{c}{Task~3 (native)}']),
        r'\cmidrule(lr){3-4}\cmidrule(lr){5-6}\cmidrule(lr){7-12}',
        row(['','']+[r'\multicolumn{2}{c}{'+x+'}' for x in ['Seq. sim.','Oligo','Transition','State','Typed edge']]),
        r'\cmidrule(lr){3-4}\cmidrule(lr){5-6}\cmidrule(lr){7-8}\cmidrule(lr){9-10}\cmidrule(lr){11-12}',
        row(['','']+[center(x) for x in ['D','N']*5]),r'\midrule']
    lines=[r'% Preamble requires \usepackage{booktabs,longtable}.',
        '% One logical table: it breaks across pages and repeats the column headings.',
        r'\begingroup',r'\footnotesize',r'\setlength{\tabcolsep}{2pt}',
        r'\renewcommand{\arraystretch}{1.04}',r'\setlength{\LTcapwidth}{\textwidth}',
        r'\begin{longtable}{@{}llrrrrrrrrrr@{}}',
        r'\caption{'+caption+r'}\label{tab:name-only-all-tasks}\\']+header+[
        r'\endfirsthead',r'\multicolumn{12}{l}{\textit{Table~\thetable\ (continued)}}\\']+header+[
        r'\endhead',r'\midrule',r'\multicolumn{12}{r}{\textit{Continued on next page}}\\',
        r'\endfoot',r'\bottomrule',r'\endlastfoot']
    body_rows=0
    for pid,label in PROTOCOLS.items():
        for i,(system,model,_,_) in enumerate(SYSTEMS):
            a=t1[model,pid]
            cells=[label if i==0 else '',model,num(a['pdf_similarity']),num(a['name_only_similarity'])]
            for metric in METRICS:
                a=native[system,pid,metric]
                if a['eligible']!='True':cells += [center('--')]*2
                else:
                    cells += [num(a['bundle_n3'])]
                    if a['name_only_status']=='valid_completion':cells.append(num(a['name_only']))
                    else:
                        assert system=='claude-code' and pid=='share_seq'
                        cells.append(center('F'))
            assert len(cells)==12
            lines.append(make_row(cells,keep=i<2));body_rows+=1
        lines.append(r'\addlinespace[1.5pt]')
    assert body_rows==60
    lines += [r'\midrule']
    for i,(system,model,_,_) in enumerate(SYSTEMS):
        a=t1summary[model]
        cells=['Macro (valid pairs)' if i==0 else '',model,num(a['pdf_mean']),num(a['name_only_mean'])]
        for m in METRICS:
            a=summary[system,m];cells += [num(a['bundle_mean']),num(a['name_only_mean'])]
        lines.append(make_row(cells,keep=i<2))
    lines += [r'\addlinespace[2pt]']
    for i,(system,model,_,_) in enumerate(SYSTEMS):
        changes=[t1summary[model]['difference']]+[summary[system,m]['name_minus_bundle'] for m in METRICS]
        lines.append(make_row([r'$\Delta$ (N$-$D)' if i==0 else '',model]+
            [r'\multicolumn{2}{c}{'+num(v,True)+'}' for v in changes],keep=i<2))
    lines += [r'\addlinespace[2pt]']
    for i,(_,model,_,_) in enumerate(SYSTEMS):
        a=t1summary[model]
        lines.append(make_row(['Task~1 exact' if i==0 else '',model,
            center(r'\('+a['pdf_exact']+r'/20\)'),center(r'\('+a['name_only_exact']+r'/20\)'),
            r'\multicolumn{8}{c}{}'],keep=i<2))
    lines += [r'\end{longtable}',r'\endgroup','']
    output=OUT/FILENAME;output.write_text('\n'.join(lines))
    # Compile in a temporary document, keeping the main manuscript unchanged.
    work=Path(tempfile.mkdtemp(prefix='libstruct-name-only-combined-'))
    shutil.copy2(PAPER/'neurips_2026.sty',work/'neurips_2026.sty')
    old=json.loads((DATA/'publication_tables_v1/compile_verification.json').read_text())
    prior_work=Path(old['temporary_directory'])
    for p in prior_work.iterdir():
        if p.suffix in {'.tfm','.vf','.pfb','.enc','.map','.otf'}:shutil.copy2(p,work/p.name)
    shutil.copy2(output,work/FILENAME)
    preamble=(PAPER/'main.tex').read_text().split(r'\title{',1)[0]
    (work/'check.tex').write_text(preamble+'\n'+r'\usepackage{longtable}'+'\n'+
        r'\begin{document}\appendix\renewcommand{\thetable}{S\arabic{table}}'+
        '\n'+r'\input{'+FILENAME+r'}\end{document}')
    cmd=['/opt/homebrew/bin/tectonic','--only-cached','--keep-logs','--keep-intermediates','check.tex']
    result=subprocess.run(cmd,cwd=work,capture_output=True,text=True)
    (OUT/'compile.log').write_text(result.stdout+result.stderr)
    log=(work/'check.log').read_text() if (work/'check.log').exists() else ''
    report=dict(script_sha256=sha(__file__),source_inputs_sha256=prior['inputs_sha256'],
        tex_sha256=sha(output),body_rows=body_rows,protocols=20,models=3,
        numeric_changes=False,agent_inference=False,command=cmd,temporary_directory=str(work),
        returncode=result.returncode,errors=re.findall(r'^!.*',log,re.M),
        overfull_boxes=re.findall(r'Overfull .+',log),
        unresolved_references=re.findall(r'Reference .+ undefined',log))
    if result.returncode==0:
        import fitz
        pdf=fitz.open(work/'check.pdf');report['pages']=len(pdf);report['previews']=[]
        for i,page in enumerate(pdf):
            p=work/f'page-{i+1}.png'
            page.get_pixmap(matrix=fitz.Matrix(1.6,1.6)).save(p)
            report['previews'].append(str(p))
    assert all(sha(p)==h for p,h in protected.items())
    report['existing_results_and_main_tex_unchanged']=True
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    assert result.returncode==0 and not report['errors'] and not report['overfull_boxes'] and not report['unresolved_references']


if __name__=='__main__':main()
