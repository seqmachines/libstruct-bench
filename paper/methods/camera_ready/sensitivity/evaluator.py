"""Isolated, hash-guarded verifier 4.7.0 sensitivity evaluator.

Only three scientific weight dictionaries and four threshold call sites vary.
Memoization stores weight-independent features plus assignment results keyed by
the entire newly computed score matrix and threshold; no frozen alignment is reused. Cached full-pipeline runs are checked against an
uncached clone. No production module or input document is mutated.
"""
from __future__ import annotations
import ast
import functools
import hashlib
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
SCORER = ROOT / 'src/libstruct_bench/libgen/v4_7_0/scoring.py'
SCORER_SHA = '1618943605e33e488654c7b02af85b027d3009c82f1aa1572d0d782b7c1da11e'
FROZEN = ROOT / 'analysis/paper_results/verifier_impact/results_frozen_v1'
OUT = Path(__file__).resolve().parent
WEIGHTS = {
    't2': dict(sequence=.65, modifications=.15, kind=.10, orientation=.05, role=.05),
    'transition': dict(operation=.30, substrates=.15, products=.20, disposition=.15, oligos=.20),
    'state': dict(reference_strand=.50, architecture=.15, segments=.20, pairing=.15),
}
METRICS = ['t2_required_family_f1', 't3_molecular_transition_f1', 't3_state_f1', 't3_typed_edge_f1']
WEIGHT_SEED = 20261012
BOOTSTRAP_SEED = 20261013

def load(path):
    return json.loads(Path(path).read_text())

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')

def configurations():
    import numpy as np
    configs = [dict(config_id='original', family='original', kind='original', weights=None, state_threshold=.25, transition_threshold=.25)]
    for i, (family, weights) in enumerate(WEIGHTS.items()):
        keys = list(weights); original = np.array(list(weights.values()))
        values = [('uniform', 'uniform', np.ones(len(keys)) / len(keys))]
        for j, key in enumerate(keys):
            for scale in [.8, 1.2]:
                x = original.copy(); x[j] *= scale; x /= x.sum()
                values.append((f'{key}_{int(scale*100)}', 'individual', x))
        rng = np.random.default_rng(np.random.SeedSequence([WEIGHT_SEED, i]))
        values += [(f'dirichlet_{j:03d}', 'dirichlet', x) for j, x in enumerate(rng.dirichlet(100 * original, size=200))]
        for label, kind, x in values:
            configs.append(dict(config_id=f'{family}_{label}', family=family, kind=kind, weights=dict(zip(keys, map(float, x))), state_threshold=.25, transition_threshold=.25))
    for s in [.15, .25, .40]:
        for t in [.15, .25, .40]:
            if s == t == .25: continue
            configs.append(dict(config_id=f'threshold_s{s:.2f}_t{t:.2f}', family='threshold', kind='threshold', weights=None, state_threshold=s, transition_threshold=t))
    assert len(configs) == 640
    return configs

class ThresholdOverlay(ast.NodeTransformer):
    def __init__(self): self.sites = []
    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == 'best_partial_one_to_one_matching':
            for kw in node.keywords:
                if kw.arg == 'minimum_score' and isinstance(kw.value, ast.Name) and kw.value.id == '_T3_MIN_ASSIGNMENT_SCORE':
                    matrix = node.args[0].id
                    assert matrix in ('state_assignment_scores', 'transition_assignment_scores')
                    replacement = '_SENS_STATE_THRESHOLD' if matrix.startswith('state') else '_SENS_TRANSITION_THRESHOLD'
                    self.sites.append((node.lineno, matrix, replacement))
                    kw.value = ast.copy_location(ast.Name(id=replacement, ctx=ast.Load()), kw.value)
        return node

def clone(*, directional=False, cached=False):
    assert sha(SCORER) == SCORER_SHA, 'Scorer changed: do not silently use another verifier.'
    tree = ast.parse(SCORER.read_text()); overlay = ThresholdOverlay(); tree = overlay.visit(tree)
    assert [s[1] for s in overlay.sites] == ['state_assignment_scores', 'transition_assignment_scores'] * 2, overlay.sites
    ast.fix_missing_locations(tree)
    name = f'_sensitivity_{directional}_{cached}'
    module = types.ModuleType(name); module.__file__ = str(SCORER); sys.modules[name] = module
    exec(compile(tree, str(SCORER), 'exec'), module.__dict__)
    module._SENS_STATE_THRESHOLD = module._SENS_TRANSITION_THRESHOLD = .25
    module._sensitivity_threshold_sites = overlay.sites
    if directional: raise ValueError('Provisional directional overlay is not verifier 4.7.0')
    if directional:
        old = module._family_tokens_match
        def directional_match(predicted, truth):
            if module._FAMILY_TOKEN_RE.fullmatch(predicted) and module._NUCLEOTIDE_TOKEN_RE.fullmatch(truth): return False
            return old(predicted, truth)
        module._family_tokens_match = directional_match
    return module

def set_config(module, config):
    for family, attribute in [('t2', 'T2_WEIGHTS'), ('transition', 'TRANSITION_WEIGHTS'), ('state', 'STATE_WEIGHTS')]:
        setattr(module, attribute, dict(config['weights'] if config['family'] == family else WEIGHTS[family]))
    module._SENS_STATE_THRESHOLD = config['state_threshold']
    module._SENS_TRANSITION_THRESHOLD = config['transition_threshold']

class FeatureCache:
    """Case-local immutable-feature cache, with registered input-object keys.

    Whole original documents are retained while a case runs. Ephemeral dicts
    are keyed by content, so Python id reuse cannot cause false cache hits.
    Return values are read-only at all production call sites listed below.
    """
    def __init__(self, module, documents):
        self.registered = {}; self.stores = {}; self.module = module
        def register(x):
            if isinstance(x, (dict, list)):
                self.registered[id(x)] = x
                for v in (x.values() if isinstance(x, dict) else x): register(v)
        for document in documents: register(document)
        for name in [
            '_t2_dimensions', '_state_dimensions', '_transition_dimensions',
            '_oligo_similarity', '_collapse_prediction_families',
            '_transition_oligo_sequence_f1', '_state_assignment_compatible',
            '_state_boundary_classes', '_state_event_contexts',
            '_state_metadata_diagnostics', '_matched_strand_orientation_accuracy',
            '_text_collection_f1', '_name_similarity', '_strand_is_exact',
            'best_one_to_one_matching', 'best_partial_one_to_one_matching',
        ]: self.wrap(name)
        for name in ['_sequence_similarity', '_sequence_value', '_state_sequence_value']:
            old = getattr(module, name)
            setattr(module, name, functools.lru_cache(maxsize=100000)(old))

    def key(self, x):
        if id(x) in self.registered: return ('input', id(x))
        if isinstance(x, dict): return ('dict', tuple(sorted((k, self.key(v)) for k, v in x.items())))
        if isinstance(x, (list, tuple)): return ('list', tuple(self.key(v) for v in x))
        if isinstance(x, (set, frozenset)): return ('set', frozenset(self.key(v) for v in x))
        return x

    def wrap(self, name):
        original = getattr(self.module, name); cache = {}; self.stores[name] = cache
        def cached(*args, **kwargs):
            key = (self.key(args), self.key(kwargs))
            if key not in cache: cache[key] = original(*args, **kwargs)
            return cache[key]
        setattr(self.module, name, cached)

def documents(case):
    from libstruct_bench.audit.connected_process import migrate_connected_process_bundle
    docs = {k: load(p) for k, p in case['truth']['paths'].items()}
    if case['truth']['transform'] != 'none': docs = migrate_connected_process_bundle(docs)
    return load(case['p2']), load(case['p3']), docs['T2'], docs['T3']

def signatures(details):
    t2 = details['t2']; workflows = details['t3']['workflows']
    payloads = {
        'family_members': t2['prediction_families'],
        'family_matches': [(m['prediction_family_index'], m['groundtruth_index'], m['prediction_index']) for m in t2['matches']],
        'neutralized_families': t2['neutralized_prediction_family_indices'],
        'workflows': sorted((w['predicted_workflow_id'] or '', w['groundtruth_workflow_id'] or '') for w in workflows.values()),
        'states': [(k, w['state_id_map'], [(m['prediction_index'], m['groundtruth_index'], m['scored']) for m in w['state_matches']]) for k, w in sorted(workflows.items())],
        'transitions': [(k, w['transition_id_map'], [(m['prediction_index'], m['groundtruth_index'], m['scored']) for m in w['transition_matches']]) for k, w in sorted(workflows.items())],
        'edges': [(k, w['typed_edges']) for k, w in sorted(workflows.items())],
    }
    return {k: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest() for k, v in payloads.items()}

def fixed_assignment_scores(original_metrics, original_details, config):
    """Counterfactual decomposition ONLY: hold original pairs/members/counts.

    Actual reported scores always come from a fresh grade_libgen call. Residual
    captures rematching, best-member changes, neutralization and count changes.
    Scientific support masks are retained and renormalized for T2 and states.
    """
    scores = {key: original_metrics[key] for key in METRICS}
    family = config['family']
    if family not in WEIGHTS: return scores
    weights = config['weights']; diag = original_details['diagnostic_metrics']
    if family == 't2':
        matches = original_details['t2']['matches']; metric = METRICS[0]
        denominator = diag['t2']['effective_prediction_family_count'] + diag['t2']['required_family_count']
    else:
        matches = [m for w in original_details['t3']['workflows'].values() for m in w[f'{family}_matches'] if m['scored']]
        metric = METRICS[1 if family == 'transition' else 2]
        denominator = diag['t3'][f'predicted_{family}_count'] + diag['t3'][f'groundtruth_{family}_count']
    total = 0.
    for match in matches:
        dims = match['dimension_scores']
        enabled = {k:v for k,v in dims.items() if v is not None}
        normalizer = 1. if family == 'transition' else sum(weights[k] for k in enabled)
        total += sum(weights[k]*v for k,v in enabled.items()) / normalizer if normalizer else 0.
    scores[metric] = 2*total/denominator if denominator else 1.
    return scores
