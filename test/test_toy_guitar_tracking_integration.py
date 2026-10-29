from datetime import datetime,timedelta,timezone
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import nullcontext

import cv2
import numpy as np
import pytest

from scripts.run_toy_guitar_tracking import RecordingSink,select_initial_boxes,OriginalBundleWithYoloInitialization
from agent.sam31_product_tracking import ProductCandidate,ProductDisplaySurface
from agent.stage10_presentation import product_presentation
from agent.stage10_demo import RealtimeWindow,run_windowed_rtsp_demo
from test.test_stage10_1_realtime import _WindowSource,_Bundle,_Runtime,_context,_provider_output


def test_initialization_rejects_distinct_ambiguous_targets_but_accepts_duplicates():
    bee={'class_id':0,'confidence':.9,'bbox_xyxy':[10,10,30,30]}
    guitar={'class_id':1,'confidence':.8,'bbox_xyxy':[50,50,90,70]}
    assert select_initial_boxes([bee,guitar,{**bee,'confidence':.7}])[0]==bee
    with pytest.raises(ValueError,match='Ambiguous'):
        select_initial_boxes([bee,guitar,{**bee,'confidence':.8,'bbox_xyxy':[100,100,120,120]}])
    with pytest.raises(ValueError,match='missing'):select_initial_boxes([bee])


def test_recording_flushes_decodable_segments_and_marks_source_gaps(tmp_path):
    config={'sample_fps':5.,'segment_seconds':.4,'max_recording_gap_seconds':.6}
    sink=RecordingSink(tmp_path,config);start=datetime.now(timezone.utc).replace(tzinfo=None)
    frame=np.full((48,64,3),90,dtype=np.uint8)
    for t in [0,.2,.4,2]:
        result=SimpleNamespace(context=SimpleNamespace(timestamp=start+timedelta(seconds=t)),products=[])
        sink(frame,result)
    sink.close()
    index=json.loads((tmp_path/'recordings/index.json').read_text())
    assert [s['close_reason'] for s in index['segments']]==['segment_limit','source_time_gap','shutdown']
    assert [s['frames'] for s in index['segments']]==[2,1,1]
    for s in index['segments']:
        cap=cv2.VideoCapture(s['path']);ok,decoded=cap.read();cap.release()
        assert ok and decoded.shape==frame.shape


def test_locked_latest_preview_does_not_abort_recording(tmp_path):
    config={'sample_fps':5.,'segment_seconds':10.,'max_recording_gap_seconds':.6}
    sink=RecordingSink(tmp_path,config);start=datetime.now(timezone.utc).replace(tzinfo=None)
    frame=np.full((48,64,3),90,dtype=np.uint8)
    result=SimpleNamespace(context=SimpleNamespace(timestamp=start),products=[])
    real_replace=os.replace
    def replace_with_locked_preview(source,destination):
        if str(destination).endswith('latest.jpg'):
            raise PermissionError(5,'locked')
        return real_replace(source,destination)
    with patch('scripts.run_toy_guitar_tracking.os.replace',side_effect=replace_with_locked_preview):
        sink(frame,result)
    sink.close()
    assert sink.total==1
    assert not (tmp_path/'latest.tmp.jpg').exists()
    index=json.loads((tmp_path/'recordings/index.json').read_text())
    assert index['segments'][0]['frames']==1


def test_original_stage10_delivers_rendered_frames_and_stops_gracefully():
    start=datetime.now(timezone.utc);contexts=tuple(_context(i,start) for i in range(12))
    source=_WindowSource([RealtimeWindow(0,contexts,contexts,0)])
    bundle=_Bundle(outputs=[tuple(_provider_output(c) for c in contexts)])
    seen=[]
    with patch('agent.stage10_demo.Stage10OverlayRenderer.render',return_value=np.full((720,1280,3),90,dtype=np.uint8)):
        summary=run_windowed_rtsp_demo(source=source,runtime=_Runtime(),provider_bundle=bundle,
            frame_consumer=lambda frame,result:seen.append((result.context.source_seq,int(frame.mean()))),stop_requested=lambda:len(seen)>=2)
    assert seen==[(0,90),(1,90)]
    assert summary.frames_processed==2 and source.stopped


def test_two_target_names_are_additive_to_original_catalog():
    assert product_presentation('yellow_bee_toy').chinese_name=='黄色小蜜蜂玩偶'
    assert product_presentation('white_guitar').chinese_name=='白色吉他'
    assert product_presentation('smart_toy_a').chinese_name=='智能熊'


def test_night_initialization_uses_real_surface_evidence_contract(tmp_path):
    masks=[];candidates=[]
    for k,(x,y) in enumerate([(700,490),(700,420)]):
        mask=np.zeros((720,1280),dtype=bool);mask[y:y+30,x:x+50]=True
        candidates.append(ProductCandidate(k,(x,y,x+50,y+30),(x+25,y+15),.9,mask))
    class Predictor:
        def handle_request(self,request):return {'session_id':'fixture'}
    stage7c=SimpleNamespace(add_text_prompt=lambda predictor,session,text:{'outputs':text},
                            candidates_from_outputs=lambda text:(candidates[0 if text=='plush toy' else 1],))
    bundle=SimpleNamespace(base=SimpleNamespace(torch=SimpleNamespace(inference_mode=nullcontext),autocast_context=nullcontext),
        predictor=Predictor(),product_module=SimpleNamespace(stage7c=stage7c),product_surface=ProductDisplaySurface(((0,0),(1,0),(1,1),(0,1))))
    config={'products':{0:'yellow_bee_toy',1:'white_guitar'}}
    wrapper=OriginalBundleWithYoloInitialization(bundle,config,tmp_path/'config.yaml',tmp_path)
    result=wrapper.semantic_initialization(SimpleNamespace(frame=np.zeros((720,1280,3),dtype=np.uint8)))
    assert result['prompts']['yellow_bee_toy']['obj_id']==0
    assert result['prompts']['white_guitar']['obj_id']==1
    assert (tmp_path/'semantic_initialization/class_0_mask.png').exists()
