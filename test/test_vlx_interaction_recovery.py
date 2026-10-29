"""Recorded regressions for repeated interactions and anonymous track continuity."""
import numpy as np
import json
from pathlib import Path

from agent.vlx_interaction import InteractionAnalyzer,InteractionConfig,ObjectEvidence,PersonEvidence
from test.test_vlx_interaction_video import baseline, pickup, release, obj, person
from agent.vlx_person_tracking import AnonymousUsers, PersonDetection


def test_real_guitar_return_extent_jitter_does_not_merge_next_user_pickup():
    data=json.loads((Path(__file__).parent/'fixtures/vlx_guitar_return_extent_jitter_trace.json').read_text(encoding='utf-8'))
    analyzer=InteractionAnalyzer()
    for row in data['rows']:
        analyzer.process(row['timestamp_seconds'],[ObjectEvidence(**o) for o in row['objects']],
                         [PersonEvidence(**p) for p in row['people']])
    sessions=analyzer.finish()['sessions']
    assert len(sessions)>=2
    assert 56<=sessions[0]['putdown_seconds']<=60
    # This regression checks separation; sparse source review is not exact
    # onset ground truth and the saved localization detects this lift late.
    assert 69<=sessions[1]['pickup_seconds']<=76
    assert sessions[0]['user_id']!=sessions[1]['user_id']


def test_long_hold_keeps_its_observed_return_origin_past_idle_cache_ttl():
    a=InteractionAnalyzer();baseline(a);pickup(a)
    for i in range(175):
        a.process(2+i*.2,[obj(.5)],[person(1,.5)])
    for i in range(6):
        a.process(37+i*.2,[obj(.4)],[person(1,.4)])
    assert a.finish()['total']['completed_count']==1


def test_brief_other_wrist_contact_does_not_poison_original_holder():
    a=InteractionAnalyzer();baseline(a);pickup(a)
    a.process(2,[obj(.5)],[person(1,.5),person(2,.5)])
    for i in range(1,6):
        a.process(2+i*.2,[obj(.5)],[person(1,.5)])
    release(a,offset=2)
    r=a.finish()
    assert r['total']['completed_count']==1
    assert not r['sessions'][0]['attribution_uncertain']


def test_real_partial_body_extent_preserves_two_anonymous_user_tracks():
    data=json.loads((Path(__file__).parent/'fixtures/vlx_partial_person_extent_trace.json').read_text())
    users=AnonymousUsers()
    for row in data['rows']:
        people=users.update(row['timestamp_seconds'],[PersonDetection(**d) for d in row['detections']])
        if 135 <= row['timestamp_seconds'] < 138:
            assert {p.user_id for p in people}=={1,2}


def test_real_crossing_keeps_clothing_tracks_with_the_same_people():
    data=json.loads((Path(__file__).parent/'fixtures/vlx_crossing_people_trace.json').read_text())
    users=AnonymousUsers()
    for row in data['rows']:
        people=users.update(row['timestamp_seconds'],[PersonDetection(**d) for d in row['detections']])
        if 99 <= row['timestamp_seconds'] < 100:
            left,middle,right=sorted(people,key=lambda p:p.bbox[0])
            assert [left.user_id,middle.user_id,right.user_id]==[3,1,2]


def test_segmentation_speck_cannot_expand_a_resting_object_to_another_hand():
    from scripts.vlx_mask_refinement import mask_geometry
    mask=np.zeros((100,100),dtype=np.uint8)
    mask[40:60,40:60]=1;mask[2:4,2:4]=1
    geometry=mask_geometry(mask)
    assert geometry['bbox']==[.4,.4,.6,.6]


def test_mask_mapping_rejects_two_equally_plausible_instances():
    from scripts.vlx_mask_refinement import match_instances
    candidates={4:{'bbox':(.4,.4,.5,.5)},5:{'bbox':(.4,.4,.5,.5)}}
    assert match_instances(candidates,{'bee_plush':(.4,.4,.5,.5)})=={}


def test_fast_lift_then_continuous_toss_and_other_hand_catch_counts_once():
    a=InteractionAnalyzer();baseline(a)
    a.process(1,[obj(.4)],[person(1,.4)])
    a.process(1.2,[obj(.43)],[person(1,.43)])
    a.process(1.4,[obj(.46)],[person(1,.46)])
    a.process(1.6,[obj(.60)],[person(1)])
    a.process(1.8,[obj(.65)],[person(1)])
    for t,x in ((2,.57),(2.2,.55),(2.4,.55),(2.6,.55)):
        catch=PersonEvidence(1,(.05,.1,.85,.95),((.1,.2),(x+.04,.44)))
        a.process(t,[obj(x)],[catch])
    assert a.finish()['total']['pickup_count']==1


def test_real_semantic_validation_recovers_three_guitar_sessions_without_switching_instances():
    from scripts.vlx_grounded_instance import GroundedInstanceSelector, overlap
    data=json.loads((Path(__file__).parent/'fixtures/vlx_guitar_semantic_recovery_trace.json').read_text())
    selector=GroundedInstanceSelector(data['seed'])
    analyzer=InteractionAnalyzer(InteractionConfig(user_missing_grace_seconds=4))
    for row in data['rows']:
        t=row['timestamp_seconds']
        g=selector.update(t,row['candidates'],row['vlx'])
        if 117.3<t<118 and g and row['vlx']:
            # The recorded VLX label jumped to the blue guitar. Fresh masks
            # must not certify that different instance as the white guitar.
            assert overlap(g['bbox'],row['vlx']['bbox'])<.1
        objects=[ObjectEvidence('white_guitar',tuple(g['bbox']),g['continuous'])] if g else []
        analyzer.process(t,objects,[PersonEvidence(**p) for p in row['people']])
    result=analyzer.finish()
    assert result['total']['pickup_count']==result['total']['putdown_count']==3
    assert result['total']['incomplete_count']==0
    assert [s['user_id'] for s in result['sessions']]==[1,2,6]
    first,second,third=result['sessions']
    assert 50<first['pickup_seconds']<54 and 56<first['putdown_seconds']<60
    assert 72<second['pickup_seconds']<76 and 89<second['putdown_seconds']<95
    assert 116<third['pickup_seconds']<118 and 124<third['putdown_seconds']<129


def test_pending_same_hand_can_recover_without_counting_hidden_confirmation_time():
    a=InteractionAnalyzer();baseline(a)
    a.process(1,[obj(.4)],[person(1,.4)])
    a.process(1.2,[obj(.43)],[person(1,.43)])
    a.process(1.4,[obj(.46)],[person(1,.46)])
    a.process(1.6,[obj(.49)],[person(1,wrists_visible=False)])
    a.process(1.8,[],[person(1,wrists_visible=False)])
    assert a.report()['total']['pickup_count']==0
    for t in (2,2.2,2.4,2.6):
        a.process(t,[obj(.51,continuous=t!=2)],[person(1,.51)])
    result=a.finish()
    assert result['total']['pickup_count']==1
    assert result['sessions'][0]['pickup_seconds']==1.2
    assert result['sessions'][0]['pickup_confirmed_seconds']>=2.2


def test_bystander_contact_does_not_cancel_an_already_supported_same_hand_lift():
    a=InteractionAnalyzer();baseline(a)
    a.process(1,[obj(.4)],[person(1,.4)])
    a.process(1.2,[obj(.43)],[person(1,.43)])
    for t,x in ((1.4,.46),(1.6,.49),(1.8,.52)):
        a.process(t,[obj(x)],[person(1,x),person(2,x)])
    result=a.finish()
    assert result['total']['pickup_count']==1
    assert result['sessions'][0]['user_id']==1


def test_person_occlusion_grace_does_not_extend_object_missing_grace():
    a=InteractionAnalyzer(InteractionConfig(user_missing_grace_seconds=4));baseline(a);pickup(a)
    for i in range(15):
        a.process(2+i*.2,[obj(.5)],[])
    release(a,offset=3)
    assert a.finish()['total']['completed_count']==1
    b=InteractionAnalyzer(InteractionConfig(user_missing_grace_seconds=4));baseline(b);pickup(b)
    for i in range(15):
        b.process(2+i*.2,[],[person(1,.5)])
    assert b.finish()['sessions'][0]['end_reason']=='object_lost'


def test_semantic_target_loss_cannot_be_replaced_by_a_known_neighbour():
    from scripts.vlx_grounded_instance import GroundedInstanceSelector
    seed=(.5,.4,.56,.7);neighbour=(.41,.5,.46,.7)
    selector=GroundedInstanceSelector(seed)
    assert selector.update(0,[{'bbox':seed,'score':.9},{'bbox':neighbour,'score':.9}])
    for i in range(1,15):
        assert selector.update(i*.2,[{'bbox':neighbour,'score':.95}],
                               {'bbox':neighbour,'continuous':True}) is None


def test_return_to_origin_cancels_pending_onset_during_wrist_occlusion():
    a=InteractionAnalyzer();baseline(a)
    a.process(1,[obj(.4)],[person(1,.4)])
    a.process(1.2,[obj(.43)],[person(1,.43)])
    a.process(1.4,[obj(.4)],[person(1,wrists_visible=False)])
    for t in (1.6,1.8):
        a.process(t,[obj(.4)],[person(1,.4),person(2,.4)])
    a.process(2,[obj(.4)],[person(1,.4)])
    for t,x in ((2.2,.43),(2.4,.46),(2.6,.49),(2.8,.52)):
        a.process(t,[obj(x)],[person(1,x)])
    result=a.finish()
    assert result['total']['pickup_count']==1
    assert result['sessions'][0]['pickup_seconds']==2.2
