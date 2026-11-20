"""Regression contract for the default verifier; no private reference fixtures."""
import copy
import hashlib
import math
from pathlib import Path

import pytest

from libstruct_bench.libgen import scoring as default
from libstruct_bench.libgen.v4_7_0 import scoring as corrected
from tests.libgen_fixtures import t2_prediction, t2_groundtruth, t3_prediction, t3_groundtruth


def oligo(sequence, **kw):
    return dict(oligo_id='o', name='RT primer', role='reverse transcription primer',
                kind='single', orientation='5_to_3', components=[], modifications=[],
                sequence=sequence, **kw)


def test_default_is_the_paper_verifier():
    assert default is corrected
    assert default.LIBGEN_BENCHMARK_VERSION == '4.7.0'
    assert hashlib.sha256(Path(default.__file__).read_bytes()).hexdigest() == (
        '1618943605e33e488654c7b02af85b027d3009c82f1aa1572d0d782b7c1da11e')
    docs = (t2_prediction(), t3_prediction(), t2_groundtruth(), t3_groundtruth())
    before = copy.deepcopy(docs)
    metrics, details = corrected.grade_libgen(*docs)
    assert all(v == pytest.approx(1) for v in metrics.values())
    assert details['benchmark_version'] == '4.7.0'
    assert docs == before


@pytest.mark.parametrize('base', list('ACGTU'))
def test_predicted_placeholder_cannot_hide_fixed_bases(base):
    assert not corrected._family_tokens_match('<UMI>', base)
    assert corrected._family_tokens_match(base, '<UMI>')
    gt = oligo(base * 4 + '[UMI:2]')
    pred = oligo('[UMI:6]')
    assert corrected._oligo_similarity(pred, gt, scorable_only=True) == pytest.approx(2/6)
    score, _ = corrected.score_t2([pred], [gt], required_oligo_ids={'o'})
    assert score['exact_required_family_recall'] == 0
    assert score['required_family_f1'] < 1
    assert corrected._transition_oligo_sequence_f1(['o'], ['o'],
        predicted_oligos={'o': pred}, truth_oligos={'o': gt}) == pytest.approx(2/6)


@pytest.mark.parametrize('base', ['N', 'V'])
def test_existing_degenerate_family_compatibility_is_preserved(base):
    # Compatibility of ambiguity tokens is retained; V is not asserted to be
    # a particular fixed nucleotide. Full IUPAC set semantics remain out of scope.
    assert corrected._family_tokens_match('<UMI>', base)
    assert not corrected._family_tokens_match(base, 'A')
    assert corrected._family_template_sequence(oligo('AC' + base * 3)) == 'AC' + base * 3


@pytest.mark.parametrize('flat', [True, False])
@pytest.mark.parametrize('redundant_placeholder', [True, False])
def test_role_prose_and_placeholders_cannot_erase_explicit_component_bases(flat, redundant_placeholder):
    pred = oligo('TTTT[UMI:2]' if flat else '')
    component = dict(name='i7 index adapter', role='sample index', sequence='TTTT', length=4)
    if redundant_placeholder:
        component['placeholder'] = '[I7_INDEX:4]'
    pred['components'] = [component, dict(placeholder='[UMI:2]', length=2)]
    gt = oligo('ACGT[UMI:2]')
    assert corrected._component_family_placeholder(component) is None
    assert corrected._ordered_molecule_sequence(pred, family_template=True) == 'TTTT[UMI:2]'
    assert corrected._oligo_similarity(pred, gt, scorable_only=True) == pytest.approx(.5)


def test_explicit_sequence_wins_over_component_annotations_and_mixed_ambiguity():
    pred = oligo('ACNNVT[UMI:2]')
    pred['components'] = [dict(sequence='ACNNVT', role='cell barcode', placeholder='[CELL_BARCODE:6]')]
    assert corrected._family_template_sequence(pred) == pred['sequence']
    assert corrected._ordered_molecule_sequence(pred, family_template=True) == pred['sequence']
    assert corrected._sequence_similarity('/5Phos/ACrGU[UMI:2]', 'ACGU[UMI:2]', family_truth=True) == 1


def test_correct_concrete_members_still_collapse_against_a_true_family():
    gt = oligo('AAA[CELL_BARCODE:4]TTT')
    preds = [oligo('AAA' + b + 'TTT') for b in ['ACGT', 'TGCA', 'CAGT']]
    for i, p in enumerate(preds): p['oligo_id'] = f'p{i}'
    score, detail = corrected.score_t2(preds, [gt], required_oligo_ids={'o'})
    assert score['predicted_family_count'] == 1
    assert score['required_family_f1'] == pytest.approx(1)
    assert detail['prediction_families'][0]['member_indices'] == [0, 1, 2]


def test_unmatched_explicit_sequences_are_not_collapsed_by_role_prose():
    from tests.test_libgen import _panel_member, _panel_family_truth
    predictions = [_panel_member(oligo_id='required_1', barcode='ACGT'),
                   _panel_member(oligo_id='required_2', barcode='TGCA'),
                   _panel_member(oligo_id='extra_1', barcode='AAAA', prefix='CCC', suffix='GGG'),
                   _panel_member(oligo_id='extra_2', barcode='CCCC', prefix='CCC', suffix='GGG')]
    score, details = corrected.score_t2(predictions, [_panel_family_truth()], required_oligo_ids={'family_rt'})
    # The two exact target-family members still collapse; the two unrelated
    # literal molecules do not become a family merely through a component name.
    assert score['predicted_family_count'] == 3
    assert score['required_family_f1'] == pytest.approx(.5)
    assert [f['member_oligo_ids'] for f in details['unmatched_prediction_families']] == [['extra_1'], ['extra_2']]


def test_placeholder_roles_remain_directional_and_lengths_matter():
    assert corrected._family_tokens_match('<I7_INDEX>', '<SAMPLE_INDEX>')
    assert not corrected._family_tokens_match('<SAMPLE_INDEX>', '<I7_INDEX>')
    assert not corrected._family_tokens_match('<I5_INDEX>', '<I7_INDEX>')
    assert corrected._sequence_similarity('[UMI:2]', '[UMI:4]', family_truth=True) == .5


def test_exactness_is_component_based_without_an_epsilon():
    assert corrected._dimensions_are_exact({'a': (1., True), 'b': (0., False)})
    assert not corrected._dimensions_are_exact({'a': (math.nextafter(1., 0.), True)})
    assert not corrected._dimensions_are_exact({'a': (1., False)})


def test_neutral_transition_reproduces_rounding_discontinuity_and_fix(monkeypatch):
    pred, gt = t3_prediction(), t3_groundtruth()
    gt['workflows'][0]['transitions'][0]['support_status'] = 'externally_completed'
    docs = (t2_prediction(), pred, t2_groundtruth(), gt)
    w = dict(operation=.25531914893617014, substrates=.15957446808510634,
             products=.21276595744680848, disposition=.15957446808510634,
             oligos=.21276595744680848)
    assert sum(w.values()) != 1
    _, baseline = corrected.grade_libgen(*docs)
    assert next(iter(baseline['t3']['workflows'].values()))['transition_matches'][0]['neutralized']
    monkeypatch.setattr(corrected, 'TRANSITION_WEIGHTS', w)
    _, perturbed = corrected.grade_libgen(*docs)
    match = next(iter(perturbed['t3']['workflows'].values()))['transition_matches'][0]
    assert match['neutralized']
    assert match['scientific_alignment_score'] < 1


def test_imperfect_neutral_transition_is_not_excused(monkeypatch):
    pred, gt = t3_prediction(), t3_groundtruth()
    gt['workflows'][0]['transitions'][0]['support_status'] = 'externally_completed'
    pred['workflows'][0]['transitions'][0]['discarded_product_ids'] = ['state_cdna']
    monkeypatch.setattr(corrected, 'TRANSITION_WEIGHTS', dict(operation=1.,substrates=0.,products=0.,disposition=0.,oligos=0.))
    _, details = corrected.grade_libgen(t2_prediction(), pred, t2_groundtruth(), gt)
    match = next(iter(details['t3']['workflows'].values()))['transition_matches'][0]
    assert match['scientific_alignment_score'] == 1
    assert not match['neutralized']


def test_optional_exactness_does_not_depend_on_weights(monkeypatch):
    gt = oligo('ACGT[UMI:2]')
    for weights in [dict(sequence=.2,modifications=.2,kind=.2,orientation=.2,role=.2),
                    dict(sequence=1.,modifications=0.,kind=0.,orientation=0.,role=0.)]:
        monkeypatch.setattr(corrected, 'T2_WEIGHTS', weights)
        score, _ = corrected.score_t2([gt], [gt], required_oligo_ids=set())
        assert score['effective_prediction_family_count'] == 0
        imperfect = {**gt, 'orientation': '3_to_5'}
        score, _ = corrected.score_t2([imperfect], [gt], required_oligo_ids=set())
        assert score['effective_prediction_family_count'] == 1


def test_unmatched_neutral_entity_uses_primitive_exactness():
    counts, _, neutral = corrected._matched_entity_counts([{}], [{}], [], [[.9999999999999998]],
        score=lambda p, g: 0., scorable=lambda g: False, exact=lambda p, g: True)
    assert counts[1] == 0 and neutral == {0}
    counts, _, neutral = corrected._matched_entity_counts([{}], [{}], [], [[1.]],
        score=lambda p, g: 0., scorable=lambda g: False, exact=lambda p, g: False)
    assert counts[1] == 1 and not neutral
