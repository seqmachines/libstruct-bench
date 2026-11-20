"""Pair native T2/T3 name-only results with frozen document-grounded replicates."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from libstruct_bench.cli.prepare_libgen_name_only import (
    DEFAULT_OUT, ROOT, find_prediction, sha, write_json,
)
from libstruct_bench.harbor_agents.name_only import validate_lock
from libstruct_bench.libgen.scoring import grade_libgen
from libstruct_bench.libgen.validation import (
    LibgenValidationError, validate_t2_prediction, validate_t3_prediction,
    validate_prediction_links,
)

METRICS = ("t2_required_family_f1", "t3_molecular_transition_f1", "t3_state_f1", "t3_typed_edge_f1")


def metric_eligible(metric: str, n_required: int) -> bool:
    return metric != 't2_required_family_f1' or n_required > 0


def score_trial(trial: Path, pid: str, truth: dict, schema_root: Path,
                *, name_only: bool, expected_lock_sha: str = "",
                compatible_lock_shas: tuple[str, ...] = ()) -> dict:
    path = trial / "result.json"
    row = dict(trial=str(trial), protocol_id=pid, status="not_run", exception=None,
               scores=None, prediction_valid=False, empty_oligos=False, empty_workflows=False)
    if not path.is_file():
        return row
    result = json.loads(path.read_text())
    row["result_sha256"] = sha(path)
    row["exception"] = (result.get("exception_info") or {}).get("exception_type")
    if not result.get("finished_at"):
        row["status"] = "unfinished"
        return row
    if name_only:
        receipt = trial / "agent/name-only-isolation.json"
        if not receipt.is_file():
            row["status"] = "isolation_unverified"
            return row
        check = json.loads(receipt.read_text())
        accepted_lock_shas = {expected_lock_sha, *compatible_lock_shas} - {""}
        if not check.get("ready") or check.get("experiment_lock_sha256") not in accepted_lock_shas:
            row["status"] = "isolation_unverified"
            return row
        trajectory = trial / "agent/trajectory.json"
        if not trajectory.is_file():
            row["status"] = "isolation_unverified"
            return row
        # Any attempted external-knowledge tool invocation invalidates the condition,
        # even if the model happened to produce schema-valid predictions.
        forbidden = []
        for step in json.loads(trajectory.read_text()).get("steps", []):
            for tool in step.get("tool_calls", []) or []:
                name = str(tool.get("function_name") or tool.get("tool_name") or "").lower()
                if any(word in name for word in ("web_search", "search_web", "read_url", "browser", "mcp", "invoke_subagent", "spawn_agent")):
                    forbidden.append(name)
        if forbidden:
            row.update(status="forbidden_tool_invocation", forbidden_tools=sorted(set(forbidden)))
            return row
    predictions = {t: find_prediction(trial, f"{t}_prediction.json") for t in ("t2", "t3")}
    if any(p is None for p in predictions.values()):
        row["status"] = "missing_output"
        return row
    row["prediction_sha256"] = {t: sha(p) for t, p in predictions.items()}
    try:
        t2, t3 = (json.loads(predictions[t].read_text()) for t in ("t2", "t3"))
        validate_t2_prediction(t2, protocol_id=pid, schema_root=schema_root)
        validate_t3_prediction(t3, protocol_id=pid, schema_root=schema_root)
        validate_prediction_links(t2, t3)
    except (OSError, ValueError, LibgenValidationError) as error:
        row.update(status="invalid_output", validation_error=str(error))
        return row
    row.update(prediction_valid=True, empty_oligos=not t2["oligos"], empty_workflows=not t3["workflows"])
    scores, _ = grade_libgen(t2, t3, truth["T2"], truth["T3"])
    row["scores"] = {m: scores[m] for m in METRICS}
    row["status"] = "runtime_failure_with_output" if row["exception"] else "valid_completion"
    return row


def summarize_pairs(pairs: list[tuple[float, float]], rng, draws: int) -> dict:
    if not pairs:
        return dict(n=0, pdf_mean=None, name_only_mean=None, pdf_minus_name_only=None,
                    difference_95ci=None, pdf_wins=0, name_only_wins=0, ties=0)
    a = np.array(pairs, dtype=float)
    delta = a[:, 0] - a[:, 1]
    indices = rng.integers(0, len(a), (draws, len(a)))
    return dict(n=len(a), pdf_mean=float(a[:, 0].mean()), name_only_mean=float(a[:, 1].mean()),
                pdf_minus_name_only=float(delta.mean()),
                difference_95ci=np.quantile(delta[indices].mean(axis=1), [.025, .975]).tolist(),
                pdf_mean_95ci=np.quantile(a[:, 0][indices].mean(axis=1), [.025, .975]).tolist(),
                name_only_mean_95ci=np.quantile(a[:, 1][indices].mean(axis=1), [.025, .975]).tolist(),
                pdf_wins=int((delta > 1e-12).sum()), name_only_wins=int((delta < -1e-12).sum()),
                ties=int((np.abs(delta) <= 1e-12).sum()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, default=DEFAULT_OUT / 'experiment.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'analysis/paper_results/libgen_name_only_statistics.json')
    parser.add_argument('--bootstrap', type=int, default=100_000)
    parser.add_argument('--seed', type=int, default=20261011)
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args(argv)
    if args.bootstrap <= 0:
        parser.error('--bootstrap must be positive')
    lock = validate_lock(args.experiment)
    experiment_sha = sha(args.experiment)
    task_root = args.experiment.parent / 'tasks'
    # Use the frozen grader implementation; refuse silent scoring-code drift.
    exemplar = task_root / lock['protocols'][0]['protocol_id'] / 'tests'
    for relative in ('libgen/scoring.py', 'libgen/version.py', 'libgen/validation.py',
                     'libgen/prediction_validation.py', 'matching.py', 'modalities.py',
                     'normalization.py', 'audit/groundtruth.py', 'audit/artifacts.py'):
        frozen = exemplar / 'libstruct_bench' / relative
        current = ROOT / 'src/libstruct_bench' / relative
        if sha(current) != sha(frozen):
            raise ValueError(f'Source changed since experiment freeze: {current}; review before scoring')
    truths = {}
    for p in lock['protocols']:
        pid = p['protocol_id']
        g = task_root / pid / 'tests/groundtruth'
        truths[pid] = {k: json.loads((g / f).read_text()) for k, f in (
            ('T2', 'groundtruth_oligos.json'), ('T3', 'groundtruth_library_generation_workflow.json'))}

    # Require 60 completed scheduled attempts before producing a final report.
    name_trials = {}
    missing = []
    for system, spec in lock['systems'].items():
        directory = ROOT / 'runs/libgen' / spec['family'] / spec['job']
        by_id = {}
        for path in sorted(directory.glob('*/result.json')):
            result = json.loads(path.read_text())
            pid = result['task_name'].removeprefix('sequencing/libgen-name-only-')
            if pid not in truths:
                raise ValueError(f'Unexpected name-only protocol: {path}')
            if pid in by_id:
                raise ValueError(f'Multiple name-only attempts for {system}/{pid}; adjudicate explicitly')
            by_id[pid] = path.parent
        for pid in truths:
            trial = by_id.get(pid)
            if trial is None or not json.loads((trial / 'result.json').read_text()).get('finished_at'):
                missing.append(f'{system}/{pid}')
            name_trials[system, pid] = trial
    if missing and not args.allow_incomplete:
        parser.error(f'{len(missing)}/60 attempts are missing or unfinished. Run the three jobs first; --allow-incomplete produces an explicitly provisional report.')

    rng = np.random.default_rng(args.seed)
    summaries, rows = [], []
    for system in lock['systems']:
        system_rows = []
        for p in lock['protocols']:
            pid = p['protocol_id']
            baseline = []
            for record in (r for r in lock['baseline'][system] if r['protocol_id'] == pid):
                trial = Path(record['trial'])
                if sha(trial / 'result.json') != record['result_sha256']:
                    raise ValueError(f'Frozen baseline attempt changed: {trial}')
                for pred in record['predictions'].values():
                    if pred and sha(Path(pred['path'])) != pred['sha256']:
                        raise ValueError(f'Frozen baseline prediction changed: {trial}')
                baseline.append(score_trial(trial, pid, truths[pid], exemplar / 'schemas', name_only=False))
            trial = name_trials[system, pid]
            name = (score_trial(trial, pid, truths[pid], exemplar / 'schemas', name_only=True,
                                expected_lock_sha=experiment_sha,
                                compatible_lock_shas=tuple(lock['systems'][system].get(
                                    'compatible_receipt_lock_sha256', []))) if trial else
                    dict(status='not_run', scores=None, exception=None, prediction_valid=False,
                         empty_oligos=False, empty_workflows=False))
            row = dict(system=system, protocol_id=pid, protocol_name=p['canonical_name'],
                       n_required=lock['groundtruth'][pid]['n_required'], pdf_attempts=baseline,
                       name_only_attempt=name, metrics={})
            assert len(baseline) == 3
            for metric in METRICS:
                eligible = metric_eligible(metric, row['n_required'])
                completed = name['status'] not in ('not_run', 'unfinished')
                valid_pair = name['status'] == 'valid_completion' and all(b['status'] == 'valid_completion' for b in baseline)
                pdf = float(np.mean([b['scores'][metric] if b['status'] == 'valid_completion' else 0. for b in baseline]))
                control = name['scores'][metric] if name['status'] == 'valid_completion' else 0.
                row['metrics'][metric] = dict(eligible=eligible,
                    pdf_mean=pdf if eligible else None,
                    name_only=control if eligible and completed else None,
                    difference=pdf-control if eligible and completed else None,
                    valid_pair=valid_pair and eligible)
            system_rows.append(row)
            print(f"{system} | {pid}: {name['status']}", flush=True)
        for metric in METRICS:
            scheduled = [(r['metrics'][metric]['pdf_mean'], r['metrics'][metric]['name_only'])
                         for r in system_rows if r['metrics'][metric]['name_only'] is not None]
            valid = [(r['metrics'][metric]['pdf_mean'], r['metrics'][metric]['name_only'])
                     for r in system_rows if r['metrics'][metric]['valid_pair']]
            # A prespecified first-baseline-attempt comparison accompanies the
            # primary PDF n=3 mean, so n=1 vs n=1 can also be inspected.
            first = [(r['pdf_attempts'][0]['scores'][metric], r['name_only_attempt']['scores'][metric])
                     for r in system_rows if r['metrics'][metric]['eligible']
                     and r['pdf_attempts'][0]['status'] == 'valid_completion'
                     and r['name_only_attempt']['status'] == 'valid_completion']
            summaries.append(dict(system=system, metric=metric,
                scheduled=summarize_pairs(scheduled, rng, args.bootstrap),
                valid_complete_pairs=summarize_pairs(valid, rng, args.bootstrap),
                first_baseline_attempt_sensitivity=summarize_pairs(first, rng, args.bootstrap),
                not_applicable=sum(not r['metrics'][metric]['eligible'] for r in system_rows)))
        rows.extend(system_rows)
    failures = {}
    for system in lock['systems']:
        selected = [r for r in rows if r['system'] == system]
        failures[system] = {
            'pdf_status_counts': dict(Counter(b['status'] for r in selected for b in r['pdf_attempts'])),
            'name_only_status_counts': dict(Counter(r['name_only_attempt']['status'] for r in selected)),
            'pdf_exception_counts': dict(Counter(b['exception'] for r in selected for b in r['pdf_attempts'] if b['exception'])),
            'name_only_exception_counts': dict(Counter(r['name_only_attempt']['exception'] for r in selected if r['name_only_attempt']['exception'])),
            'name_only_empty_oligos': sum(r['name_only_attempt']['empty_oligos'] for r in selected),
            'name_only_empty_workflows': sum(r['name_only_attempt']['empty_workflows'] for r in selected),
        }
    output = dict(condition=lock['condition'], provisional=bool(missing), missing_attempts=missing,
                  experiment_sha256=experiment_sha, benchmark_version=lock['benchmark_version'],
                  analysis_script_sha256=sha(Path(__file__)), bootstrap_draws=args.bootstrap, seed=args.seed,
                  difference_direction='PDF minus protocol-name-only',
                  baseline='Equal-weight protocol mean of three frozen document-grounded attempts.',
                  bootstrap_unit='Protocol; resample paired protocol rows, preserving all baseline replicates together.',
                  zero_required_policy=lock['t2_zero_required_policy'],
                  failure_policy=lock['output_failure_policy'],
                  interpretation='Parametric-knowledge control; not a direct measurement of training-data memorization. Tool restrictions differ from the native baseline.',
                  summary=summaries, failures=failures, protocols=rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out, output)
    print('\nModel | Metric | n | PDF | Name-only | Difference [95% CI] | PDF/name/ties')
    for s in summaries:
        v = s['valid_complete_pairs']
        if v['n']:
            lo, hi = v['difference_95ci']
            print(f"{s['system']} | {s['metric']} | {v['n']} | {v['pdf_mean']:.3f} | {v['name_only_mean']:.3f} | "
                  f"{v['pdf_minus_name_only']:+.3f} [{lo:+.3f}, {hi:+.3f}] | {v['pdf_wins']}/{v['name_only_wins']}/{v['ties']}")
    print(args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
