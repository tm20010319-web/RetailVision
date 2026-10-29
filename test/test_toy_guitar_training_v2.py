"""Guard holdout membership and fixed-threshold detection accounting."""
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/training'))
from train_toy_guitar_v2 import assign_splits, fixed_threshold_counts, predict_rows


def test_preserved_v1_split_rejects_label_tampering(tmp_path):
    row={'image':'frame_0001.jpg','image_sha256':'image','label_sha256':'label','source_pts_seconds':0}
    path=tmp_path/'split.json';path.write_text(json.dumps({'entries':[{**row,'split':'test'}]}))
    assert assign_splits({'preserve_split_manifest':str(path)},[row])=={'frame_0001.jpg':'test'}
    with pytest.raises(ValueError,match='V1 source changed'):
        assign_splits({'preserve_split_manifest':str(path)},[{**row,'label_sha256':'edited'}])


def test_pose_overrides_and_uncovered_frames():
    source={'split_blocks':[[49,65,'train']],'split_overrides':{52:'test',55:'val'}}
    rows=[{'image':f'frame_{i:04d}.jpg'} for i in [50,52,55]]
    assert list(assign_splits(source,rows).values())==['train','test','val']
    with pytest.raises(ValueError,match='Overlapping/missing'):
        assign_splits(source,rows+[{'image':'frame_0066.jpg'}])


def test_one_truth_cannot_match_duplicate_or_wrong_class_detections():
    entry={'image':'sample.jpg','size':[100,100],'boxes':[[0,.5,.5,.2,.2],[1,.8,.8,.2,.2]],'source_id':'multipose','dynamic_frame':True}
    predictions=[{'image':'sample.jpg','detections':[
        {'class_id':0,'confidence':.9,'bbox_xyxy':[40,40,60,60]},
        {'class_id':0,'confidence':.8,'bbox_xyxy':[40,40,60,60]},
        {'class_id':0,'confidence':.7,'bbox_xyxy':[70,70,90,90]}]}]
    counts=fixed_threshold_counts(predictions,[entry],.5)
    assert counts['all']=={'tp':1,'fp':2,'fn':1}
    assert counts['dynamic_new']==counts['all']
    assert counts['class_1']=={'tp':0,'fp':0,'fn':1}


def test_prediction_keeps_input_names_when_loader_invents_names():
    class Detector:
        def predict(self,source,**kwargs):
            assert isinstance(source,str)
            return [type('Result',(),{'path':'image0.jpg','boxes':[]})()]
    config={'training':{'imgsz':640,'device':'0'},'evaluation':{'confidence':.25,'iou':.7}}
    result=predict_rows(Detector(),[Path('first.jpg'),Path('second.jpg')],config)
    assert [r['image'] for r in result]==['first.jpg','second.jpg']
