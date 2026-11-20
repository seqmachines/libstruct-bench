"""Write the compact report and verify input preservation and delivered PDFs."""
from pathlib import Path
import hashlib
import json
import re
import fitz
import pandas as pd

OUT = Path(__file__).resolve().parent
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    manifest=json.loads((OUT/'publication_manifest.json').read_text())
    source=Path(manifest['source_directory']);paper=Path(manifest['paper_directory'])
    for name,digest in manifest['source_sha256'].items():assert sha(source/name)==digest,name
    for name,digest in manifest['paper_original_sha256'].items():assert sha(paper/name)==digest,name
    pdf_checks=[]
    for name,digest in manifest['outputs'].items():
        p=OUT/name;assert sha(p)==digest
        d=fitz.open(p);assert len(d)==1 and not d[0].get_images()
        for block in d[0].get_text('dict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    r=fitz.Rect(span['bbox']);page=d[0].rect
                    assert r.x0>=-.1 and r.y0>=-.1 and r.x1<=page.width+.1 and r.y1<=page.height+.1,(name,span['text'])
        target=paper/'figures'/name
        installed=target.exists()
        if installed:assert sha(target)==digest
        pdf_checks.append({'name':name,'vector_only':True,'text_in_bounds':True,'paper_copy_verified':installed,'sha256':digest})
    compile_info=json.loads((OUT/'compile_report.json').read_text())
    work=Path(compile_info['temporary_directory'])
    layout=next(c for c in compile_info['commands'] if c['input']=='main_layout_check.tex')
    assert layout['returncode']==0
    log=(work/'main_layout_check.log').read_text()
    assert not re.findall(r'^!',log,re.M)
    assert not re.findall(r"Reference `([^']+)'",log)
    missing=sorted(set(re.findall(r"Citation `([^']+)'",log)))
    known_bib=set(re.findall(r'@\w+\s*\{\s*([^,\s]+)',(paper/'references.bib').read_text()))
    assert not (set(missing)&known_bib)
    last_pdf=fitz.open(work/'main_layout_check.pdf')
    report=['SCORING-SENSITIVITY NUMERICAL SUMMARY',
        'Sources: finalized saved CSVs in camera_ready_v4_7_0/sensitivity/.',
        'No agents, scorers, or bootstrap computations were run for this publication package.',
        '640 configurations; each scientific vector varied separately; historical C30 fixed.',
        'Scoring ranges are min-max over alternative metric assumptions, NOT confidence intervals.',
        'Task 2 populations: native 19, shared-panel 9, external-knowledge 4, learning validation 4, held-out 10.',
        'Task 3 populations: 20, 10, 5, 5, 10, respectively.',
        '', 'A. NATIVE WEIGHT SENSITIVITY']
    a=pd.read_csv(OUT/'figure_A_data.csv')
    report.append(a.to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    report.extend(['','Native oligo (Kimi), state (Claude), and edge (GPT) leaders persist across all 640 settings.',
        'Transition-weight samples: Gemini first 179/200, GPT 21/200.',
        'State-weight samples: Gemini transition first 160/200, GPT 40/200.',
        'Uniform transition weights put GPT first. Secondary rankings can change.',
        '', 'B. THRESHOLD-ONLY MAXIMUM ABSOLUTE MACRO-F1 CHANGES'])
    b=pd.read_csv(OUT/'figure_B_maxima.csv')
    report.append(b.pivot(index='panel',columns='metric',values='max_abs_change').to_string(float_format=lambda x:f'{x:.4f}'))
    report.extend(['','C. INTERVENTION CONTRASTS',
        'scoring_min/max: all 640 scoring assumptions. default_ci_low/high: saved default-scoring 95% protocol-bootstrap CI (5,000 resamples).'])
    c=pd.read_csv(OUT/'figure_C_data.csv')
    report.append(c[['panel','metric','default_difference','scoring_min','scoring_max','default_ci_low','default_ci_high','positive_configurations','negative_configurations']].to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    report.extend(['','All four validation metric curves are non-monotonic in all 640 configurations.',
        'C58-C0 oligo is positive only under the uniform oligo-weight alternative (1/640).',
        'Both rules-only and KB-supplemented held-out C30 conditions have negative Task 3 contrasts in every configuration.',
        'Model-versus-harness significance was NOT tested under alternative scoring assumptions.',
        '', 'ZERO-REQUIRED TASK 2 EXCLUSION (DEFAULT SCORING)',
        'Both means use the same scoring outputs, with different averaging populations. No paired CI is assigned to this population change.'])
    z=pd.read_csv(OUT/'zero_required_summary.csv')
    report.append(z[['panel','group','all_protocols','informative_only','effective_predicted_families']].to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    report.extend(['','COMPILATION AND PRESERVATION',
        'main.tex and all existing PDF figures are byte-for-byte unchanged.',
        'Normal original and proposed-integrated builds stop at missing existing figures/fig1_benchmark.pdf.',
        'Also missing: figures/fig4b_autonomous_training_scheme.pdf.',
        f'Temporary layout-check build succeeded ({len(last_pdf)} pages), using explicit placeholders only for those two missing pre-existing assets.',
        'All figure references resolve. There are no fatal LaTeX errors in the temporary layout check.',
        f'{len(missing)} existing citation keys are absent from references.bib; no citations were added by the proposed section.',
        'Missing bibliography keys: '+', '.join(missing)+'.',
        'Existing overfull boxes remain in earlier manuscript material; one additional box warning belongs to a diagnostic missing-figure placeholder. None is in the new appendix.',
        'Missing Latin Modern and Computer Modern TeX font resources were supplied inside temporary build directories only; no manuscript font substitutions were made.',
        'See compile_report.json and main_layout_check_compile.log for the full compilation details.'])
    (OUT/'numerical_summary.txt').write_text('\n\n'.join(report)+'\n')
    verification={'frozen_source_tables_unchanged':True,'main_tex_and_existing_pdfs_unchanged':True,
        'pdfs':pdf_checks,'temporary_layout_check_returncode':layout['returncode'],
        'temporary_integrated_pages':len(last_pdf),'unresolved_figure_references':[],
        'preexisting_missing_figures':compile_info['missing_preexisting_figures'],
        'preexisting_missing_bibliography_keys':missing,
        'manual_tex_sha256':{p.name:sha(p) for p in OUT.glob('*_addition.tex')},
        'scripts_sha256':{p.name:sha(p) for p in OUT.glob('*.py')}}
    (OUT/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
    print('PASS: input preservation, vector PDFs, in-bounds labels, numerical package, and temporary LaTeX integration.')

if __name__=='__main__':main()
