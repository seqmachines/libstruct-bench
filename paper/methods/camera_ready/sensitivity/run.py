"""Freeze provenance, then recompute all selected predictions, case by case.

Usage: PYTHONDONTWRITEBYTECODE=1 python analysis/paper_results/verifier_sensitivity/run.py --workers 6
No agents, network access, or writes outside this new analysis directory.
"""
import argparse
import concurrent.futures
import csv
import platform
import time
from collections import Counter
from evaluator import *

def rows(path):
    with Path(path).open() as f: return list(csv.DictReader(f))

def csvout(path, records):
    if not records: return
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0])); writer.writeheader(); writer.writerows(records)

def verify(inputs):
    changes = [p for p, h in inputs.items() if not Path(p).is_file() or sha(p) != h]
    if changes: raise RuntimeError('Hash-locked inputs changed: ' + str(changes))
    return len(inputs)

def freeze():
    prior = load(FROZEN/'manifest.json')
    selected = [r for r in rows(FROZEN/'figure_per_protocol.csv') if r['panel'] in ['fig3a','fig4a','fig3b','fig4c','fig4d']]
    ids = {r['case_id'] for r in selected}
    cases = [c for c in prior['cases'] if c['case_id'] in ids]
    # First phase is the union of Figures 3a and 4a, then remaining figures.
    first = {r['case_id'] for r in selected if r['panel'] in ('fig3a','fig4a')}
    cases.sort(key=lambda c: (c['case_id'] not in first, c['case_id']))
    inputs = {}
    for c in cases:
        for p in [c['p2'],c['p3'],*c['truth']['paths'].values()]:
            assert sha(p) == prior['inputs'][p], p
            inputs[p] = prior['inputs'][p]
    # Guard the entire pre-existing frozen result tree, production dependencies,
    # and historical selection. These files are only ever opened for reading.
    for p in FROZEN.rglob('*'):
        if p.is_file(): inputs[str(p)] = sha(p)
    for pattern in ['src/libstruct_bench/libgen/v4_7_0/*.py','src/libstruct_bench/libgen/*.py','src/libstruct_bench/normalization.py','src/libstruct_bench/audit/connected_process.py']:
        for p in ROOT.glob(pattern): inputs[str(p)] = sha(p)
    for p in (OUT.parent/'verifier_sensitivity').rglob('*'):
        if p.is_file(): inputs[str(p)] = sha(p)
    for name in ['verifier_audit.md','verifier_impact_analysis.md']:
        p = OUT.parent/name; inputs[str(p)] = sha(p)
    for p,h in prior['inputs'].items():
        if p.endswith('validation_selection.json'):
            assert sha(p) == h, p
            inputs[p] = h
    paper = Path('external/manuscript/main.tex')
    if paper.exists(): inputs[str(paper)] = sha(paper)
    manifest = dict(verifier='4.7.0', scorer_sha256=SCORER_SHA, inputs=inputs,
        frozen_impact_manifest_sha256=sha(FROZEN/'manifest.json'),
        configurations=configurations(), cases=cases, panels=selected,
        phase1_unique_cases=len(first), unique_cases=len(cases), panel_rows=len(selected),
        variants=['corrected_4_7_0'],
        weight_seed=WEIGHT_SEED, dirichlet_concentration=100, dirichlet_draws_per_vector=200,
        bootstrap_seed=BOOTSTRAP_SEED, bootstrap_replicates=5000,
        historical_checkpoint='C30 (unchanged; selection made under verifier 4.5.0)',
        reference_policy=prior['groundtruth_policy'],
        environment=dict(python=sys.version,platform=platform.platform()),
        thresholds='Adjusted assignment scores at all four nested/final assignment sites',
        analysis='Independent scientific vectors at original thresholds; 3x3 threshold grid at original weights; corrected 4.7.0 only; historical 4.6.0 tables remain separate.')
    dump(OUT/'manifest.json',manifest)
    csvout(OUT/'selected_runs.csv',selected)
    return manifest

def evaluate(case):
    started = time.monotonic(); docs = documents(case)
    doc_hash = hashlib.sha256(json.dumps(docs,sort_keys=True).encode()).hexdigest()
    frozen = load(FROZEN/'cases'/f"{case['case_id']}.json")
    assert frozen['linked_valid'], case['case_id']
    configs = configurations(); results = []; checks = []; example_changes = {}
    for variant in ['corrected_4_7_0']:
        directional = variant == 'provisional_directional'
        module = clone(directional=directional,cached=True); cache = FeatureCache(module,docs)
        original_metrics, original_details = module.grade_libgen(*docs)
        # The historical baseline is a comparison, never an expected corrected score.
        error = 0.0
        sig = signatures(original_details)
        # Original weights passed through the fixed-pair decomposition must
        # recover original values before using it as a counterfactual.
        for family,weights in WEIGHTS.items():
            fixed = fixed_assignment_scores(original_metrics,original_details,dict(family=family,weights=weights))
            assert max(abs(fixed[k]-original_metrics[k]) for k in METRICS) < 1e-12
        checks.append(dict(variant=variant,historical_comparison_only=True,baseline_signatures=sig))
        for config in configs:
            set_config(module,config)
            metrics,details = module.grade_libgen(*docs)
            current_sig = signatures(details)
            fixed = fixed_assignment_scores(original_metrics,original_details,config)
            counts = details['diagnostic_metrics']['t2']
            row = dict(case_id=case['case_id'],protocol_id=case['protocol_id'],variant=variant,config_id=config['config_id'],
                required_families=counts['required_family_count'],effective_predicted_families=counts['effective_prediction_family_count'])
            row.update(metrics)
            for key in METRICS:
                row[key+'_direct_delta'] = fixed[key]-original_metrics[key]
                row[key+'_rematching_delta'] = metrics[key]-fixed[key]
            for layer in sig:
                changed = current_sig[layer] != sig[layer]
                row[layer+'_changed'] = int(changed)
                if changed and (variant,layer) not in example_changes:
                    example_changes[variant,layer] = dict(variant=variant,layer=layer,config_id=config['config_id'],original_signature=sig[layer],new_signature=current_sig[layer])
            results.append(row)
        del cache,module
    assert hashlib.sha256(json.dumps(docs,sort_keys=True).encode()).hexdigest() == doc_hash, 'In-memory documents mutated'
    dump(OUT/'case_results'/f"{case['case_id']}.baseline.json",dict(metrics=original_metrics,details=original_details))
    return dict(case_id=case['case_id'],rows=results,checks=checks,examples=list(example_changes.values()),elapsed=time.monotonic()-started)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,default=6);parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    manifest=load(OUT/'manifest.json') # Prepared by reference-audit gate in ../freeze.py
    verify(manifest['inputs']); assert manifest['configurations'] == configurations()
    code={str(p):sha(p) for p in [OUT/'evaluator.py',OUT/'run.py']}
    codepath=OUT/'evaluation_code_hashes.json'
    if args.resume: assert load(codepath)==code, 'Evaluation code changed; do not mix outputs.'
    else: dump(codepath,code)
    (OUT/'case_results').mkdir(exist_ok=True)
    pending=[c for c in manifest['cases'] if not (OUT/'case_results'/f"{c['case_id']}.json").exists()]
    print(f"{len(pending)} unique cases; {len(configurations())} configurations; one corrected verifier; first phase {manifest['phase1_unique_cases']} cases",flush=True)
    start=time.monotonic()
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(evaluate,c) for c in pending]
        for i,future in enumerate(concurrent.futures.as_completed(futures),1):
            r=future.result()
            records=r.pop('rows');csvout(OUT/'case_results'/f"{r['case_id']}.csv",records);dump(OUT/'case_results'/f"{r['case_id']}.json",r)
            if i%5==0 or i==len(pending):print(f"{i}/{len(pending)} saved; {time.monotonic()-start:.1f}s",flush=True)
    dump(OUT/'evaluation_integrity.json',dict(verified_input_files=verify(manifest['inputs']),completed_cases=len(manifest['cases']),configurations=640,variants=1,total_full_pipeline_scores=len(manifest['cases'])*640,code_hashes=code))
    print('Evaluation complete. All protected input hashes unchanged.',flush=True)

if __name__=='__main__':main()
