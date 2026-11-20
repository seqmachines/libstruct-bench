"""Export existing paper artifacts without changing source analyses or scores.

Public tables preserve numerical text. Only machine-specific path prefixes are
replaced; large tables are gzip-compressed. Originals remain private/local.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
TABLES={
 'main':('camera_ready_v4_7_0',[
  'results.csv','selected_protocol_scores.csv','aggregates.csv','paired_contrasts.csv','zero_required.csv',
  'completion_counts.csv','cost_performance.csv','error_profiles.csv','panel_membership.csv',
  'reference_consistency.csv','task1_recomputed.csv','task1_controls_recomputed.csv','task1_summary.csv',
  'fig3b_plot_data.csv','sensitivity_summary.csv','sensitivity_rank_first.csv']),
 'sensitivity':('camera_ready_v4_7_0/sensitivity',[
  'aggregates.csv','per_protocol.csv','paired_contrasts.csv','pairwise_ranking_changes.csv',
  'pairwise_reversal_summary.csv','protocol_drivers.csv','rank_first_frequencies.csv',
  'ranking_correlations.csv','ranking_stability_summary.csv','rematching_counts.csv',
  'trajectory_monotonicity.csv','zero_required_exclusion_baseline.csv','zero_required_spurious_families.csv']),
 'model_harness':('model_harness_v4_7_0/results_v1',[
  'shared_long.csv','contrast_weights.csv','protocol_switch_magnitudes.csv','protocol_paired_effects.csv',
  'protocol_native_contrasts.csv','paired_effects.csv','native_contrasts.csv','mixed_coefficients.csv',
  'mixed_diagnostics.csv','mixed_effects.csv','mixed_omnibus.csv','blocked_factorial.csv',
  'mixed_fixed_covariance.csv','selected_scores.csv','settings_by_trial.csv','task_compatibility.csv']),
 'name_only':('name_only_v4_7_0/results_n1_v1',[
  'native_per_protocol.csv','native_statistics.csv','task1_per_protocol.csv','task1_statistics.csv',
  'execution_settings.csv','completion_counts.csv','name_only_combined_tasks1-3.tex','name_only_paired_statistics.tex']),
 'repeats':('run_variability_v4_7_0/results_v1',[
  'execution_scores.csv','per_protocol_ranges.csv','summary.csv','reference_verification.csv','zero_required_executions.csv']),
 'bonferroni':('bonferroni_revision_20261025',[
  'model_harness_primary_bonferroni.csv','task1_api_agent_bonferroni.csv']),
 'secondary':('model_harness_v4_7_0/one_page_separated_20261025',['secondary_bonferroni.csv']),
}
METHOD_DIRS=['camera_ready_v4_7_0','camera_ready_v4_7_0/sensitivity','model_harness_v4_7_0',
 'name_only_v4_7_0','run_variability_v4_7_0','bonferroni_revision_20261025',
 'camera_ready_v4_7_0/sensitivity/publication_20261017']

# Public folder names are descriptive; source paths and scientific version
# identities remain unchanged in the provenance records and method contents.
PUBLIC_DIRECTORY_NAMES={
 'camera_ready_v4_7_0':'camera_ready',
 'model_harness_v4_7_0':'model_harness',
 'name_only_v4_7_0':'name_only',
 'run_variability_v4_7_0':'run_variability',
 'bonferroni_revision_20261025':'bonferroni',
 'publication_20261017':'publication',
 'results_v1':'results',
 'results_n1_v1':'results_n1',
}


def public_path(relative):
    return Path(*(PUBLIC_DIRECTORY_NAMES.get(part,part) for part in Path(relative).parts))


def digest(data):return hashlib.sha256(data).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--analysis-root',type=Path,default=ROOT/'analysis/paper_results')
    ap.add_argument('--output',type=Path,default=ROOT/'paper')
    args=ap.parse_args();base=args.analysis_root.resolve();dest=args.output.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError('Use a new or empty output directory; existing files are not overwritten')
    dest.mkdir(parents=True,exist_ok=True);records=[]
    def sanitize(text):
        text=text.replace(str(ROOT)+'/','repo/').replace(str(ROOT),'repo')
        # Preserve source identity suffixes but remove host/user names everywhere,
        # including JSON object keys. No change to numeric fields or sequence data.
        text=re.sub(r'/(?:Users|home)/[^/\s"\']+/playground/protocols-test', 'external/audit-data',text)
        text=re.sub(r'/(?:Users|home)/[^/\s"\']+/playground/ai4sci_neurips26','external/manuscript',text)
        text=re.sub(r'/(?:Users|home)/[^/\s"\']+/','external/home/',text)
        return text
    def export(src,relative):
        raw=src.read_bytes();clean=sanitize(raw.decode()).encode();target=dest/public_path(relative)
        if len(clean)>1_000_000 and target.suffix=='.csv':target=target.with_suffix('.csv.gz')
        target.parent.mkdir(parents=True,exist_ok=True)
        data=gzip.compress(clean,compresslevel=9,mtime=0) if target.suffix=='.gz' else clean
        target.write_bytes(data)
        records.append(dict(path=str(target.relative_to(dest)),source=str(src.relative_to(base)),
            original_sha256=digest(raw),published_sha256=digest(data),uncompressed_sha256=digest(clean),
            original_bytes=len(raw),published_bytes=len(data),path_redacted=raw!=clean))
    for group,(directory,names) in TABLES.items():
        for name in names:export(base/directory/name,Path('data')/group/name)
    for directory in METHOD_DIRS:
        for src in sorted((base/directory).iterdir()):
            if src.suffix in ('.py','.R'):
                export(src,Path('methods')/directory/src.name)
    for src in sorted(base.glob('*.py')):
        if src.name.startswith(('prepare_task1','task1_reference','analyze_pdf')) or src.name in ('task1_reference.py',):
            export(src,Path('methods')/src.name)
    for directory,names in {
      'camera_ready_v4_7_0':['input_manifest.json','extra_input_manifest.json','results_identity.json','reference_changes.json','figure_manifest.json'],
      'camera_ready_v4_7_0/sensitivity':['manifest.json','evaluation_code_hashes.json'],
      'model_harness_v4_7_0/results_v1':['input_manifest.json'],
      'name_only_v4_7_0/results_n1_v1':['input_manifest.json'],
      'run_variability_v4_7_0/results_v1':['input_manifest.json','reproducibility_manifest.json'],
    }.items():
        for name in names:export(base/directory/name,Path('provenance')/directory/name)
    for name in ('task1_reference_lock.json','task1_provenance.json'):
        export(base/name,Path('provenance')/name)
    sources={str(base/r['source']):r['original_sha256'] for r in records}
    assert all(digest(Path(p).read_bytes())==h for p,h in sources.items())
    manifest=dict(schema_version=1,verifier='4.7.0',historical_C30_preserved=True,
        source_analysis_root='analysis/paper_results',artifacts=records,
        path_roots={'repo/':'repository root','external/audit-data/':'separate private audit-data repository',
                    'external/manuscript/':'manuscript checkout','external/home/':'other local tooling; not bundled'},
        note='Derived public copies; original hashes refer to unredacted sources. Scientific values unchanged. No raw transcripts or source documents exported.')
    (dest/'export_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Exported',len(records),'artifacts;',round(sum(r['published_bytes'] for r in records)/1e6,1),'MB; sources unchanged.')


if __name__=='__main__':main()
