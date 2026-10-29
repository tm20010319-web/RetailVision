import cv2
import numpy as np

from scripts.vlx_dual_support import ViewArbiter, unique_targets, view_quality, dual_query, distinct_targets


def detection(box=(10, 10, 60, 60), key=0):
    return {'class_id': key, 'label': 'target', 'bbox_xyxy': list(box)}


def candidate(camera, score, key=0):
    return {'camera_id': camera, 'camera_name': camera, 'detection': detection(key=key), 'quality': {'score': score}}


def test_one_winner_per_target_can_differ_between_cameras():
    arbiter = ViewArbiter()
    result = arbiter.choose({0: [candidate('one', 85), candidate('three', 50)],
                             1: [candidate('one', 40, 1), candidate('three', 80, 1)]}, 0)
    assert result[0]['camera_id'] == 'one'
    assert result[1]['camera_id'] == 'three'


def test_winner_switch_has_hysteresis_but_failed_camera_cannot_linger():
    arbiter = ViewArbiter(switch_margin=8, hold_seconds=.8)
    assert arbiter.choose({0: [candidate('one', 80), candidate('three', 70)]}, 0)[0]['camera_id'] == 'one'
    assert arbiter.choose({0: [candidate('one', 80), candidate('three', 84)]}, 1)[0]['camera_id'] == 'one'
    assert arbiter.choose({0: [candidate('one', 60), candidate('three', 90)]}, 2)[0]['camera_id'] == 'one'
    assert arbiter.choose({0: [candidate('one', 60), candidate('three', 90)]}, 3)[0]['camera_id'] == 'three'
    assert arbiter.choose({0: [candidate('one', 60)]}, 3.1)[0]['camera_id'] == 'one'
    assert arbiter.choose({0: []}, 4) == {}


def test_clear_removes_winners_and_pending_switch():
    arbiter = ViewArbiter()
    arbiter.choose({0: [candidate('one', 90)]}, 0)
    arbiter.clear()
    assert arbiter.winners == {} and arbiter.pending == {}


def test_multiple_distinct_same_class_instances_are_not_ranked_as_one():
    rows = [detection((10, 10, 50, 50)), detection((100, 100, 150, 150))]
    selected, ambiguous = unique_targets(rows)
    assert selected == [] and ambiguous == [0]
    selected, ambiguous = unique_targets([detection(), detection((11, 11, 59, 59))])
    assert len(selected) == 1 and not ambiguous


def test_actual_bee_body_and_whole_toy_proposals_are_one_target():
    # Actual camera-3 result at 2026-10-23T03:00:14.750514Z. One physical bee,
    # but a body proposal and a larger toy proposal have IoU below 0.7.
    rows = [detection((226.2246, 247.9616, 249.7927, 265.8827)),
            detection((237.4377, 251.2558, 251.4950, 266.2281))]
    selected, ambiguous = unique_targets(rows)
    assert len(selected) == 1 and not ambiguous
    assert selected[0]['bbox_xyxy'][0] <= rows[0]['bbox_xyxy'][0]
    assert selected[0]['bbox_xyxy'][2] >= rows[1]['bbox_xyxy'][2]


def test_actual_two_distinct_guitars_stay_ambiguous():
    rows = [detection((339.435, 245.647, 371.861, 302.574), 1),
            detection((475.553, 268.390, 515.827, 323.547), 1)]
    assert unique_targets(rows) == ([], [1])


def test_large_false_region_cannot_absorb_a_tiny_separate_target():
    assert unique_targets([detection((0, 0, 150, 150)), detection((20, 20, 30, 30))]) == ([], [0])


def test_two_distinct_instrument_categories_are_independent_targets():
    rows = [detection((339, 245, 372, 303), 1), detection((475, 268, 516, 324), 2)]
    selected, ambiguous = distinct_targets(rows)
    assert [d['class_id'] for d in selected] == [1, 2] and not ambiguous
    arbiter = ViewArbiter()
    winners = arbiter.choose({1: [candidate('one', 90, 1), candidate('three', 60, 1)],
                              2: [candidate('one', 50, 2), candidate('three', 90, 2)]}, 0)
    assert winners[1]['camera_id'] == 'one' and winners[2]['camera_id'] == 'three'


def test_same_guitar_region_labeled_as_both_categories_is_not_double_annotated():
    selected, ambiguous = distinct_targets([detection((10, 10, 60, 100), 1),
                                             detection((11, 11, 59, 99), 2), detection((80, 80, 120, 120), 0)])
    assert [d['class_id'] for d in selected] == [0] and ambiguous == [1, 2]


def test_score_uses_actual_pixels_tracking_and_freshness_not_fake_probability():
    frame = np.random.default_rng(9).integers(0, 256, (180, 260, 3), dtype=np.uint8)
    sharp = view_quality(frame, detection(), 1)
    blurry = view_quality(cv2.GaussianBlur(frame, (21, 21), 7), detection(), 1)
    assert sharp['score'] > blurry['score']
    assert sharp['score'] > view_quality(frame, detection(), 6)['score']
    assert view_quality(frame, detection(), 9) is None
    assert view_quality(frame, detection((-1, 2, 20, 30)), 1) is None
    assert sharp['meaning'] == 'view_quality_not_recognition_probability'


def test_quality_and_tracking_share_the_configured_dual_expiry_budget():
    frame=np.random.default_rng(2).integers(0,256,(180,260,3),dtype=np.uint8)
    assert view_quality(frame,detection(),10) is None
    assert view_quality(frame,detection(),10,max_confirmation_age=16) is not None
    assert view_quality(frame,detection(),16.1,max_confirmation_age=16) is None


def test_missing_previously_seen_target_gets_one_isolated_same_image_recheck(monkeypatch):
    import torch
    from scripts.vlx_dual_support import recognize
    monkeypatch.setattr(torch.cuda,'synchronize',lambda:None)
    large={'label':'white electric guitar','xmin':10,'ymin':10,'xmax':40,'ymax':70}
    small={'label':'white ukulele','xmin':60,'ymin':15,'xmax':90,'ymax':60}
    calls=[];proposal_calls=[];boxes=[[10,10,40,70],[60,15,90,60]]
    class Worker:
        def run_task(self,image,task,query,**kwargs):
            calls.append((image,query,kwargs['bbox_list']))
            return {'result_bbox_list':[large] if len(calls)==1 else [small]}
    def proposals(image):
        proposal_calls.append(image)
        return boxes
    frame=np.zeros((80,100,3),np.uint8)
    row=recognize(Worker(),proposals,frame,(1,2),retry_targets=(2,))
    assert [d['class_id'] for d in row['current_detections']]==[1,2]
    assert len(proposal_calls)==1 and len(calls)==2
    assert calls[0][0] is calls[1][0] and calls[0][2] is calls[1][2]
    assert calls[1][1]=='white ukulele'
    assert row['rechecked_classes']==[2]


def test_missing_target_does_not_trigger_unbounded_or_unseeded_rechecks(monkeypatch):
    import torch
    from scripts.vlx_dual_support import recognize
    monkeypatch.setattr(torch.cuda,'synchronize',lambda:None)
    calls=[]
    class Worker:
        def run_task(self,*args,**kwargs):
            calls.append(args[2]);return {'result_bbox_list':[]}
    frame=np.zeros((80,100,3),np.uint8)
    recognize(Worker(),lambda _:[],frame,(1,2))
    assert len(calls)==1
    calls.clear()
    recognize(Worker(),lambda _:[],frame,(1,2),retry_targets=(1,2))
    assert len(calls)==2


def test_dual_queries_respect_selected_target():
    assert 'guitar' not in dual_query((0,))
    assert 'bee' not in dual_query((1,))
    assert 'bee' in dual_query((0, 1)) and 'guitar' in dual_query((0, 1))
    assert dual_query((1, 2)) == 'white electric guitar; white ukulele'
    assert dual_query((2,)) == 'white ukulele'


def test_live_composite_draws_only_the_winning_camera():
    from scripts.run_toy_guitar_vlx_dual import render_views
    sample = (1, 0, '2026-10-23T03:00:00+00:00', np.zeros((448, 800, 3), np.uint8))
    views = [{'id': key, 'name': key, 'sample': sample, 'fresh': True, 'fps': 25, 'seconds': 1.5} for key in ('one', 'three')]
    winner = candidate('one', 90)
    image = render_views(views, {0: winner})
    assert tuple(image[124, 60]) == (74, 213, 255)
    assert tuple(image[124, 860]) == (0, 0, 0)


def test_guitars_can_each_be_drawn_in_a_different_winning_camera():
    from scripts.run_toy_guitar_vlx_dual import render_views
    sample = (1, 0, '2026-10-23T07:00:00+00:00', np.zeros((448, 800, 3), np.uint8))
    views = [{'id': key, 'name': key, 'sample': sample, 'fresh': True, 'fps': 20, 'seconds': 1.4}
             for key in ('one', 'three')]
    big, small = candidate('one', 90, 1), candidate('three', 90, 2)
    big['detection']['bbox_xyxy'] = [10, 40, 60, 100]
    small['detection']['bbox_xyxy'] = [100, 140, 160, 200]
    image = render_views(views, {1: big, 2: small})
    assert tuple(image[164, 60]) == (255, 215, 72)
    assert tuple(image[164, 860]) == (0, 0, 0)
    assert tuple(image[264, 160]) == (0, 0, 0)
    assert tuple(image[264, 960]) == (221, 153, 255)
