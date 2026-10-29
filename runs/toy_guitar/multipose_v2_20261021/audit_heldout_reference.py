"""Post-evaluation visual audit; preserve frozen training data and raw results."""
from pathlib import Path
import copy,json,sys

RUN=Path(__file__).resolve().parent
ROOT=RUN.parents[2]
sys.path.insert(0,str(ROOT/'tools/training'))
from train_toy_guitar_v2 import fixed_threshold_counts,draw_prediction,iou,sha256

manifest=json.loads((ROOT/'data/toy_guitar_dataset_v2_20261021/split_manifest.json').read_text(encoding='utf-8'))
entries=copy.deepcopy([e for e in manifest['entries'] if e['split']=='test'])
entry=next(e for e in entries if e['image']=='original_frame_0078.jpg')
# Visible white body/headstock extent manually checked on the original frame.
box=[130,133,187,183];x1,y1,x2,y2=box
entry['boxes'].append([1,(x1+x2)/1600,(y1+y2)/896,(x2-x1)/800,(y2-y1)/448])
source=Path(entry['source_root'])/'images'/entry['source_image']
audit={'reason':'Original frame 78 contains a hand-carried white guitar omitted by V1 labels.',
       'audit_timing':'post_evaluation_visual_review; no retraining/threshold/checkpoint changes',
       'source_image':str(source),'image_sha256':sha256(source),'added_class_id':1,'visible_bbox_xyxy':box,
       'raw_reference_boxes':87,'audited_reference_boxes':88,'frozen_source_and_dataset_unchanged':True,
       'comparison':{}}
draw_prediction(source,[{'class_id':1,'confidence':1.0,'bbox_xyxy':box}],RUN/'old78_reference_correction.jpg')
for name in ['v1','v2']:
    predictions=json.loads((RUN/f'{name}_test_predictions.json').read_text())
    audit['comparison'][name]=fixed_threshold_counts(predictions,entries,.5)
    old78=next(r for r in predictions if r['image']==entry['image'])
    audit['comparison'][name]['old78_guitar_candidates']=[{**b,'audited_reference_iou':iou(b['bbox_xyxy'],box)} for b in old78['detections'] if b['class_id']==1]
(RUN/'heldout_annotation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(RUN/'audited_evaluation_reference.json').write_text(json.dumps({'policy':'post_evaluation_single_missing_label_correction','entries':entries},ensure_ascii=False,indent=2),encoding='utf-8')
summary=json.loads((RUN/'training_summary.json').read_text())
summary['post_evaluation_reference_audit']=audit
(RUN/'training_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:{'all':v['all'],'old78_guitar_candidates':v['old78_guitar_candidates']} for k,v in audit['comparison'].items()},indent=2))
