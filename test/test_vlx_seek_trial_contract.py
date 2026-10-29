from datetime import datetime,timezone
from types import SimpleNamespace

import pytest

from scripts.probe_vlx_seek import initialization_prompts
from scripts.run_toy_guitar_tracking import PrefixedWindowSource,SequenceOffsetReader
from agent.stage10_demo import RealtimeFrameContext,RealtimeWindow


def box(label,coords):
    return {'label':label,**dict(zip(('xmin','ymin','xmax','ymax'),coords))}


def test_vlx_grounding_rejects_absent_or_conflicting_targets():
    items=[box('bee plush toy',[10,10,30,30]),box('white guitar',[50,50,90,80])]
    prompts=initialization_prompts({'result_bbox_list':items},(100,100))
    assert prompts['yellow_bee_toy']['obj_id']==0 and prompts['white_guitar']['obj_id']==1
    with pytest.raises(ValueError,match='both'):initialization_prompts({'result_bbox_list':items[:1]},(100,100))
    with pytest.raises(ValueError,match='ambiguous'):
        initialization_prompts({'result_bbox_list':items+[box('bee plush toy',[60,10,80,30])]},(100,100))


def test_seed_context_identity_and_future_source_sequence_remain_distinct():
    c=RealtimeFrameContext(camera_id='camera',source_seq=0,captured_at=datetime.now(timezone.utc),frame_width=100,frame_height=100)
    reader=SequenceOffsetReader(SimpleNamespace(read_sampled=lambda:c),12)
    assert reader.read_sampled().source_seq==12 and c.source_seq==0
    future=RealtimeWindow(0,(c,),(c,),0)
    source=SimpleNamespace(start=lambda:None,wait_window=lambda **kw:future)
    seeded=PrefixedWindowSource(source,(c,));seeded.start()
    first=seeded.wait_window();second=seeded.wait_window()
    assert first.contexts[0] is c and first.window_no==0 and second.window_no==1


def test_distinct_bee_and_guitar_boxes_may_overlap_when_placed_together():
    # Real setup: the bee stands in front of the guitar neck. Rectangle overlap
    # is expected even though the automatic proposals refer to distinct objects.
    items=[box('bee plush toy',[386,315,447,393]),box('white guitar',[361,332,574,407])]
    assert set(initialization_prompts({'result_bbox_list':items},(800,448)))=={'yellow_bee_toy','white_guitar'}
    with pytest.raises(ValueError,match='conflict'):
        initialization_prompts({'result_bbox_list':[items[0],box('white guitar',[386,315,447,393])]},(800,448))
