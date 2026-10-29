"""Real recorded evidence: reject false release and recover supported actions."""
import json
from pathlib import Path

import pytest

from agent.vlx_interaction import (
    InteractionAnalyzer, InteractionConfig, ObjectEvidence, PersonEvidence, TARGETS,
)


@pytest.fixture(scope='module')
def toy_result():
    data = json.loads((Path(__file__).parent/'fixtures/vlx_three_toy_occlusion_trace.json').read_text())
    a = InteractionAnalyzer(InteractionConfig(user_missing_grace_seconds=4), targets=TARGETS[:3])
    for row in data['rows']:
        a.process(row['timestamp_seconds'], [ObjectEvidence(**o) for o in row['objects']],
                  [PersonEvidence(**p) for p in row['people']])
    return a.finish()


def test_recorded_orange_release_does_not_wait_for_hidden_wrist(toy_result):
    rows = [s for s in toy_result['sessions'] if s['object_key']=='orange_cartoon']
    assert len(rows)==1
    assert rows[0]['user_id']==1
    assert 99 <= rows[0]['putdown_seconds'] <= 103


def test_recorded_white_continuous_hold_is_not_split_by_partial_extent(toy_result):
    rows = [s for s in toy_result['sessions'] if s['object_key']=='white_egg']
    assert len(rows)==1
    assert rows[0]['user_id']==3
    assert 65 <= rows[0]['pickup_seconds'] <= 67
    assert rows[0]['putdown_seconds'] is not None
    assert 134 <= rows[0]['putdown_seconds'] <= 144
    assert rows[0]['duration_includes_occlusion'] is True
    assert len(rows[0]['occlusion_intervals'])==1


def test_recorded_pink_first_lift_survives_stationary_bystander_hand(toy_result):
    rows = [s for s in toy_result['sessions'] if s['object_key']=='pink_plush']
    assert len(rows)==2
    assert all(s['user_id']==2 for s in rows)
    assert 61 <= rows[0]['pickup_seconds'] <= 64
    assert 64 <= rows[0]['putdown_seconds'] <= 67
    assert 66 <= rows[1]['pickup_seconds'] <= 69
    assert 147 <= rows[1]['putdown_seconds'] <= 153


def holding_with_fragment():
    from test.test_vlx_interaction_video import baseline, pickup, person
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    fragment = ObjectEvidence('orange_cartoon',(.52,.42,.54,.44))
    a.process(2,[fragment],[person(1,.5)])
    return a,fragment


def test_hidden_wrist_is_not_release_while_object_overlaps_owner():
    from test.test_vlx_interaction_video import baseline, pickup, obj
    a = InteractionAnalyzer(); baseline(a); pickup(a)
    owner = PersonEvidence(1,(.3,.2,.8,.8),(None,(.1,.1)))
    for i in range(8):
        a.process(2+i*.2,[obj(.5)],[owner])
    assert a.finish()['total']['putdown_count']==0


def test_hidden_wrist_can_release_when_body_and_visible_hand_are_clear():
    from test.test_vlx_interaction_video import baseline, pickup, obj
    a = InteractionAnalyzer(); baseline(a); pickup(a)
    owner = PersonEvidence(1,(.05,.1,.35,.8),(None,(.2,.2)))
    for i in range(8):
        a.process(2+i*.2,[obj(.5)],[owner])
    assert a.finish()['sessions'][0]['putdown_seconds']==2


def test_small_fragment_alone_does_not_prove_release():
    a,fragment = holding_with_fragment()
    owner = PersonEvidence(1,(.3,.2,.8,.8),((.3,.3),(.7,.3)))
    for i in range(1,9):
        a.process(2+i*.2,[fragment],[owner])
    assert a.finish()['total']['putdown_count']==0


@pytest.mark.parametrize('owner_stays', [True,False])
def test_occlusion_retention_is_bounded_and_requires_owner(owner_stays):
    from test.test_vlx_interaction_video import person
    a,_ = holding_with_fragment()
    for i in range(1,91):
        a.process(2+i*.2,[],[person(1,.5)] if owner_stays else [])
    r = a.finish()
    assert r['total']['putdown_count']==0
    assert r['sessions'][0]['status']=='incomplete'
    assert r['sessions'][0]['holding_seconds'] is None
    assert r['sessions'][0]['end_reason'] in ('object_lost','user_track_lost')


def test_occlusion_cannot_recover_at_disjoint_new_position():
    from test.test_vlx_interaction_video import person, obj
    a,_ = holding_with_fragment()
    for i in range(1,21):
        a.process(2+i*.2,[],[person(1,.5)])
    a.process(6.2,[obj(.8,continuous=False)],[person(1,.8)])
    r = a.finish()
    assert r['sessions'][0]['end_reason']=='object_lost'
    assert r['total']['putdown_count']==0


def test_fragment_can_preserve_but_never_confirm_return():
    from test.test_vlx_interaction_video import baseline, pickup, obj, person
    a = InteractionAnalyzer(); baseline(a); pickup(a)
    a.process(2,[obj(.4)],[person(1,.4)])
    fragment = ObjectEvidence('orange_cartoon',(.425,.45,.455,.48))
    for i in range(1,7):
        a.process(2+i*.2,[fragment],[person(1,.4)])
    assert a.report()['total']['putdown_count']==0
    a.process(3.4,[obj(.4)],[person(1,.4)])
    assert a.finish()['sessions'][0]['putdown_seconds']==2


def test_both_precontact_users_stay_ambiguous():
    from test.test_vlx_interaction_video import baseline, obj, person
    a = InteractionAnalyzer(); baseline(a)
    for i,x in enumerate((.4,.425,.45,.475,.5,.525)):
        a.process(1+i*.2,[obj(x)],[person(1,x),person(2,x)])
    assert a.finish()['total']['pickup_count']==0


def test_recorded_rotated_pink_at_new_support_still_releases():
    data=json.loads((Path(__file__).parent/'fixtures/vlx_pink_changed_rest_release_trace.json').read_text())
    a=InteractionAnalyzer(InteractionConfig(user_missing_grace_seconds=4),targets=(TARGETS[2],))
    for row in data['rows']:
        a.process(row['timestamp_seconds'],[ObjectEvidence(**o) for o in row['objects']],
                  [PersonEvidence(**p) for p in row['people']])
    r=a.finish()
    assert r['total']['pickup_count']==r['total']['putdown_count']==2
    assert 140<=r['sessions'][1]['putdown_seconds']<=144
