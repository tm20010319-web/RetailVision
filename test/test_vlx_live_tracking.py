import cv2
import numpy as np

from scripts.probe_vlx_seek import iou
from scripts.vlx_live_tracking import LiveTracks, FlowTrack


def scene(index, present=True):
    frame = np.full((160, 280), 25, np.uint8)
    patch = np.random.default_rng(4).integers(50, 255, (44, 50), dtype=np.uint8)
    x, y = 35 + index*3, 55
    if present:
        frame[y:y+44, x:x+50] = patch
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR), [x, y, x+50, y+44]


def sample(index, present=True):
    frame, box = scene(index, present)
    return (index+1, index/25, '2026-10-23T02:00:00+00:00', frame), box


def test_delayed_recognition_is_replayed_to_current_position_not_old_pixels():
    tracks = LiveTracks()
    source, box = sample(0)
    for index in range(13):
        s, current_box = sample(index)
        tracks.advance(s[0], s[1], cv2.cvtColor(s[3], cv2.COLOR_BGR2GRAY))
    assert tracks.apply(source, [{'class_id': 0, 'label': 'bee', 'bbox_xyxy': box}])
    assert len(tracks.outputs()) == 1
    assert iou(tracks.outputs()[0]['bbox_xyxy'], current_box) > .9
    assert iou(tracks.outputs()[0]['bbox_xyxy'], box) < .25


def test_disappearance_removes_box_and_fresh_vlx_can_reacquire():
    tracks = LiveTracks()
    source, box = sample(0)
    tracks.advance(source[0], source[1], cv2.cvtColor(source[3], cv2.COLOR_BGR2GRAY))
    tracks.apply(source, [{'class_id': 0, 'bbox_xyxy': box}])
    missing, _ = sample(1, present=False)
    tracks.advance(missing[0], missing[1], cv2.cvtColor(missing[3], cv2.COLOR_BGR2GRAY))
    assert tracks.outputs() == []
    new, box = sample(2)
    tracks.advance(new[0], new[1], cv2.cvtColor(new[3], cv2.COLOR_BGR2GRAY))
    assert tracks.apply(new, [{'class_id': 0, 'bbox_xyxy': box}])
    assert len(tracks.outputs()) == 1


def test_old_result_and_empty_result_cannot_resurrect_old_box():
    tracks = LiveTracks()
    source, box = sample(0)
    newer, _ = sample(1)
    for s in (source, newer):
        tracks.advance(s[0], s[1], cv2.cvtColor(s[3], cv2.COLOR_BGR2GRAY))
    tracks.apply(newer, [])
    assert not tracks.apply(source, [{'class_id': 0, 'bbox_xyxy': box}])
    assert tracks.outputs() == []


def test_camera_gap_invalidates_tracks_and_delayed_seeds():
    tracks = LiveTracks()
    source, box = sample(0)
    tracks.advance(source[0], source[1], cv2.cvtColor(source[3], cv2.COLOR_BGR2GRAY))
    tracks.apply(source, [{'class_id': 0, 'bbox_xyxy': box}])
    tracks.advance(2, 1, cv2.cvtColor(source[3], cv2.COLOR_BGR2GRAY))
    assert tracks.outputs() == []
    assert not tracks.apply((3, -20, source[2], source[3]), [{'class_id': 0, 'bbox_xyxy': box}])


def test_rotating_object_is_compared_in_its_own_orientation():
    frame, box = scene(0)
    original = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    track = FlowTrack({'class_id': 1, 'bbox_xyxy': box}, original, 0)
    previous = original
    for angle in range(2, 32, 2):
        transform = cv2.getRotationMatrix2D((60, 77), angle, 1)
        gray = cv2.warpAffine(original, transform, (280, 160), borderValue=25)
        track.update(previous, gray)
        assert track.alive
        previous = gray


def static_sample(sequence, when, present=True):
    frame, box = scene(0,present)
    return (sequence,when,'2026-10-27T03:00:00+00:00',frame),box


def test_single_missed_confirmation_keeps_only_live_track_without_renewing_its_age():
    tracks=LiveTracks(ttl_seconds=8)
    first,box=static_sample(1,0)
    tracks.advance(1,0,cv2.cvtColor(first[3],cv2.COLOR_BGR2GRAY))
    tracks.apply(first,[{'class_id':1,'bbox_xyxy':box}])
    for sequence in range(2,17):
        current,_=static_sample(sequence,(sequence-1)*.5)
        tracks.advance(sequence,current[1],cv2.cvtColor(current[3],cv2.COLOR_BGR2GRAY))
        if sequence in (7,13):
            tracks.apply(current,[])
            assert len(tracks.outputs())==1
            assert tracks.outputs()[0]['confirmed_at']==0
            assert tracks.outputs()[0]['model_frame_id']==1
    expired,_=static_sample(18,8.5)
    tracks.advance(expired[0],expired[1],cv2.cvtColor(expired[3],cv2.COLOR_BGR2GRAY))
    assert tracks.outputs()==[]


def test_ambiguous_candidates_resolve_only_against_the_existing_same_target():
    rng=np.random.default_rng(14)
    frame=cv2.cvtColor(rng.integers(30,240,(160,280),dtype=np.uint8),cv2.COLOR_GRAY2BGR)
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    tracks=LiveTracks()
    first=(1,0,'2026-10-27T03:00:00+00:00',frame)
    target={'class_id':1,'bbox_xyxy':[20,30,70,90]}
    distractor={'class_id':1,'bbox_xyxy':[180,30,230,90]}
    tracks.advance(1,0,gray);tracks.apply(first,[target])
    tracks.advance(2,.2,gray)
    tracks.apply((2,.2,first[2],frame),[],candidates=[target,distractor],ambiguous_classes=[1])
    output=tracks.outputs()
    assert len(output)==1 and iou(output[0]['bbox_xyxy'],target['bbox_xyxy'])>.95
    assert output[0]['model_frame_id']==2
    assert tracks.last_diagnostics['matched_ambiguous']==[1]
    # The same candidates cannot initialize identity without an existing track.
    unseeded=LiveTracks();unseeded.advance(2,.2,gray)
    unseeded.apply((2,.2,first[2],frame),[],candidates=[target,distractor],ambiguous_classes=[1])
    assert unseeded.outputs()==[]


def test_model_label_swap_cannot_move_each_name_onto_the_other_live_target():
    frame=cv2.cvtColor(np.random.default_rng(7).integers(30,240,(160,280),dtype=np.uint8),cv2.COLOR_GRAY2BGR)
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    tracks=LiveTracks()
    rows=[{'class_id':1,'bbox_xyxy':[20,30,70,90]},{'class_id':2,'bbox_xyxy':[180,30,230,90]}]
    first=(1,0,'2026-10-27T03:00:00+00:00',frame)
    tracks.advance(1,0,gray);tracks.apply(first,rows)
    tracks.advance(2,.2,gray)
    swapped=[{'class_id':1,'bbox_xyxy':rows[1]['bbox_xyxy']},{'class_id':2,'bbox_xyxy':rows[0]['bbox_xyxy']}]
    tracks.apply((2,.2,first[2],frame),swapped)
    output={t['class_id']:t for t in tracks.outputs()}
    assert iou(output[1]['bbox_xyxy'],rows[0]['bbox_xyxy'])>.95
    assert iou(output[2]['bbox_xyxy'],rows[1]['bbox_xyxy'])>.95
    assert output[1]['model_frame_id']==1 and output[2]['model_frame_id']==1


def test_miss_retention_cannot_keep_a_removed_object_or_survive_clear():
    tracks=LiveTracks()
    first,box=static_sample(1,0)
    tracks.advance(1,0,cv2.cvtColor(first[3],cv2.COLOR_BGR2GRAY));tracks.apply(first,[{'class_id':1,'bbox_xyxy':box}])
    gone,_=static_sample(2,.2,present=False)
    tracks.advance(2,.2,cv2.cvtColor(gone[3],cv2.COLOR_BGR2GRAY));tracks.apply(gone,[])
    assert tracks.outputs()==[]
    tracks.clear()
    assert tracks.outputs()==[] and not tracks.history


def test_ambiguous_matching_replays_candidates_before_comparing_moving_tracks():
    tracks=LiveTracks()
    sources=[]
    for index in range(13):
        s,box=sample(index)
        s[3][40:90,200:250]=cv2.cvtColor(np.random.default_rng(22).integers(40,240,(50,50),dtype=np.uint8),cv2.COLOR_GRAY2BGR)
        sources.append((s,box))
        tracks.advance(s[0],s[1],cv2.cvtColor(s[3],cv2.COLOR_BGR2GRAY))
        if index==0: tracks.apply(s,[{'class_id':1,'bbox_xyxy':box}])
    delayed,old_box=sources[3]
    tracks.apply(delayed,[],candidates=[{'class_id':1,'bbox_xyxy':old_box},
                                      {'class_id':1,'bbox_xyxy':[200,40,250,90]}],ambiguous_classes=[1])
    assert len(tracks.outputs())==1
    assert iou(tracks.outputs()[0]['bbox_xyxy'],sources[-1][1])>.9
    assert tracks.outputs()[0]['model_frame_id']==delayed[0]


def test_dual_budget_bridges_two_four_second_misses_but_still_expires():
    tracks=LiveTracks(ttl_seconds=16)
    first,box=static_sample(1,0)
    tracks.advance(1,0,cv2.cvtColor(first[3],cv2.COLOR_BGR2GRAY))
    tracks.apply(first,[{'class_id':2,'bbox_xyxy':box}])
    for n in range(1,34):
        s,_=static_sample(n+1,n*.5)
        tracks.advance(s[0],s[1],cv2.cvtColor(s[3],cv2.COLOR_BGR2GRAY))
        if n in (12,20):
            tracks.apply(s,[])
        if n*.5<=16:
            assert len(tracks.outputs())==1
            assert tracks.outputs()[0]['model_frame_id']==1
    assert tracks.outputs()==[]
